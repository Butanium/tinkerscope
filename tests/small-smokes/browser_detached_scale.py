"""Regression smoke for the >4-panel connection-starvation bug (detached fire).

THE BUG (pre-detached): every browser-fired chat held its POST /api/chat SSE open
for the whole generation. With the permanent /api/state/events EventSource that's
1 + N connections against Chrome's ~6 per-host HTTP/1.1 cap, so a send to ≥5
panels left the 5th+ POST QUEUED inside the browser — it never reached uvicorn, no
chat_start fired, and the panel showed NO "generating" placeholder (silently idle).

THE FIX: detached fire — the POST returns immediately and the generation streams
only to the bus. So this asserts the red→green: seed 8 panels (all on the free
OpenRouter router), send ONE message, and every one of the 8 panels must show
chat_start within ~1.5s (not 5-then-gated-on-a-freeing-slot). Then all 8 reach a
terminal, and every panel that completed folds its reply into its tree.

  uv run python tests/small-smokes/browser_detached_scale.py [BASE_URL]

Needs OPENROUTER_API_KEY (free router). No servable-window dependency.
"""
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8812"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
N_PANELS = 8
FREE = "openrouter:openrouter/free"


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def panel_ids(n):
    # Mirrors +page nextPanelId(): 'primary', 'compare', then p-2, p-3, …
    ids = ["primary", "compare"]
    k = 2
    while len(ids) < n:
        ids.append(f"p-{k}")
        k += 1
    return ids[:n]


def empty_tree():
    return {"nodes": {}, "rootChildren": [], "selected": {}}


def seed_conversation():
    ids = panel_ids(N_PANELS)
    body = {
        "title": "detached-scale",
        "panels": [{"id": pid, "run_id": FREE, "checkpoint": None} for pid in ids],
        "trees": {pid: empty_tree() for pid in ids},
        "reduced_panels": [],
        "send_targets": ids,
        "seen_panels": ids,
    }
    return _post("/api/conversations", body)["id"], ids


class Bus(threading.Thread):
    """Timestamp chat_start / chat_done / chat_error per panel off the state bus."""
    def __init__(self):
        super().__init__(daemon=True)
        self.t0 = time.monotonic()
        self.starts, self.dones, self.errors = {}, {}, {}
        self._stop = threading.Event()

    def run(self):
        with urllib.request.urlopen(f"{BASE}/api/state/events", timeout=120) as r:
            etype = None
            for raw in r:
                if self._stop.is_set():
                    return
                line = raw.decode().rstrip("\n")
                if line.startswith("event:"):
                    etype = line[6:].strip()
                elif line.startswith("data:"):
                    try:
                        d = json.loads(line[5:].strip() or "{}")
                    except json.JSONDecodeError:
                        continue
                    t = time.monotonic() - self.t0
                    p = d.get("panel")
                    if etype == "chat_start":
                        self.starts[p] = t
                    elif etype == "chat_done":
                        self.dones[p] = t
                    elif etype == "chat_error":
                        self.errors[p] = t

    def stop(self):
        self._stop.set()


def main():
    cid, ids = seed_conversation()
    bus = Bus()
    bus.start()
    time.sleep(1.0)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        page.goto(f"{BASE}/?c={cid}", wait_until="load", timeout=20000)
        # Wait for all 8 columns to render.
        page.wait_for_function("(n) => document.querySelectorAll('.chat-column').length >= n",
                               arg=N_PANELS, timeout=15000)
        composer = page.locator(".input-textarea")
        composer.wait_for(state="visible", timeout=8000)
        page.wait_for_function(
            "() => { const t = document.querySelector('.input-textarea'); return t && !t.disabled; }",
            timeout=15000)

        t_send = time.monotonic() - bus.t0
        composer.fill("Say a short hello and count to three.")
        composer.press("Enter")

        # (1) THE FIX: every panel must show chat_start promptly — not gated on a
        # freeing connection slot. Poll the bus until all 8 started (or timeout).
        deadline = time.time() + 20
        while len(bus.starts) < N_PANELS and time.time() < deadline:
            time.sleep(0.1)
        started = set(bus.starts)
        missing = [pid for pid in ids if pid not in started]
        assert not missing, f"panels never got chat_start (starvation!): {missing}  (got {sorted(started)})"
        span = max(bus.starts[pid] for pid in ids) - t_send
        assert span < 3.0, f"chat_start span after send too slow ({span:.2f}s) — smells like connection gating"
        print(f"(1) all {N_PANELS} panels chat_start within {span:.2f}s of send  ✓")

        # (2) all reach a terminal.
        deadline = time.time() + 90
        while (len(bus.dones) + len(bus.errors)) < N_PANELS and time.time() < deadline:
            time.sleep(0.2)
        done, err = set(bus.dones), set(bus.errors)
        terminal = done | err
        missing_term = [pid for pid in ids if pid not in terminal]
        assert not missing_term, f"panels never reached a terminal: {missing_term}"
        print(f"(2) all {N_PANELS} panels reached a terminal ({len(done)} done, {len(err)} error)  ✓")

        # (3) every COMPLETED panel folded its reply — assert on the persisted trees
        # (setTree → debounced save). Give the saves a beat to flush, then GET.
        time.sleep(2.0)
        conv = next(c for c in _get("/api/conversations") if c["id"] == cid)
        trees = conv.get("trees") or {}
        folded, empty = [], []
        for pid in done:  # only panels that actually completed should fold
            tree = trees.get(pid) or {}
            nodes = tree.get("nodes") or {}
            has_assistant = any(n.get("role") == "assistant" for n in nodes.values())
            (folded if has_assistant else empty).append(pid)
        assert not empty, f"completed panels with NO folded assistant reply: {empty}"
        print(f"(3) all {len(done)} completed panels folded a reply into their tree  ✓")

        browser.close()
    bus.stop()
    print("DETACHED SCALE SMOKE PASS")


if __name__ == "__main__":
    main()
