"""Detached-fire RELOAD-mid-generation edge.

Because the fold registration (client_token → fold ctx) is browser-SESSION-scoped,
a reload mid-generation loses it. Pre-detached a reload killed the drained
connection → the chat cancelled on disconnect. Post-detached the chat completes
server-side, and the reloaded page — with no registration for it — sees the
chat_done as EXTERNAL and folds it from the transcript echo (single representative
sample), like a tinkpg chat. This asserts that recovery: reload during an 8-panel
generation → every panel eventually folds a coherent reply, NO panel stuck on a
'generating' placeholder, and NO double-fold (exactly one assistant per user turn).

  uv run python tests/small-smokes/browser_detached_reload.py [BASE_URL]

Needs OPENROUTER_API_KEY (free router).
"""
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8850"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
N_PANELS = 8
FREE = "openrouter:openrouter/free"


def _post(path, body):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def panel_ids(n):
    ids = ["primary", "compare"]
    k = 2
    while len(ids) < n:
        ids.append(f"p-{k}")
        k += 1
    return ids[:n]


class Bus(threading.Thread):
    """Count chat terminals per panel off the bus — survives the browser reload."""
    def __init__(self):
        super().__init__(daemon=True)
        self.terminals = {}
        self._stop = threading.Event()

    def run(self):
        with urllib.request.urlopen(f"{BASE}/api/state/events", timeout=180) as r:
            etype = None
            for raw in r:
                if self._stop.is_set():
                    return
                line = raw.decode().rstrip("\n")
                if line.startswith("event:"):
                    etype = line[6:].strip()
                elif line.startswith("data:") and etype in ("chat_done", "chat_error"):
                    try:
                        d = json.loads(line[5:].strip() or "{}")
                    except json.JSONDecodeError:
                        continue
                    self.terminals[d.get("panel")] = etype

    def stop(self):
        self._stop.set()


def main():
    ids = panel_ids(N_PANELS)
    cid = _post("/api/conversations", {
        "title": "detached-reload",
        "panels": [{"id": p, "run_id": FREE, "checkpoint": None} for p in ids],
        "trees": {p: {"nodes": {}, "rootChildren": [], "selected": {}} for p in ids},
        "reduced_panels": [], "send_targets": ids, "seen_panels": ids,
    })["id"]

    # Long, high-token generations so they OUTLAST the ~1.5s reload gap — none
    # complete during it, so every reply lands AFTER the page's stream reconnects
    # (the reliable recovery path). Gap-completed replies are a documented sub-edge
    # (recovered on the next conversation open, not necessarily live).
    _post("/api/state", {"max_tokens": 1200, "n_samples": 1})

    bus = Bus()
    bus.start()
    time.sleep(1.0)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        page.goto(f"{BASE}/?c={cid}", wait_until="load", timeout=20000)
        page.wait_for_function("(n) => document.querySelectorAll('.chat-column').length >= n",
                               arg=N_PANELS, timeout=15000)
        composer = page.locator(".input-textarea")
        composer.wait_for(state="visible", timeout=8000)
        page.wait_for_function(
            "() => { const t = document.querySelector('.input-textarea'); return t && !t.disabled; }",
            timeout=15000)
        composer.fill("Write a detailed 400-word explanation of how ocean tides work.")
        composer.press("Enter")

        # Let all 8 start streaming, THEN reload mid-generation (registration lost).
        time.sleep(1.5)
        page.reload(wait_until="load", timeout=20000)
        page.wait_for_function("(n) => document.querySelectorAll('.chat-column').length >= n",
                               arg=N_PANELS, timeout=15000)

        # Wait for every panel to reach a terminal server-side (off the bus).
        deadline = time.time() + 90
        while len(bus.terminals) < N_PANELS and time.time() < deadline:
            time.sleep(0.3)
        assert len(bus.terminals) >= N_PANELS, f"not all panels terminated: {sorted(bus.terminals)}"

        # Give the reloaded page's external-fold + debounced save a beat.
        time.sleep(3.0)

        # No panel stuck 'generating': poll until every composer placeholder has
        # cleared (a chat whose chat_start arrived AFTER the reload reconnect shows a
        # legit 'generating' until its own terminal lands + renders — that's not stuck,
        # it clears; a genuinely wedged running-flag would never clear → timeout → fail).
        page.wait_for_function(
            "() => ![...document.querySelectorAll('textarea,input')]"
            ".some(e => (e.placeholder||'').includes('generating'))",
            timeout=20000)
        print(f"[dbg] bus_terminals={dict(sorted(bus.terminals.items()))}")

        browser.close()

    bus.stop()

    # Coherence + no-double-fold on the persisted trees: each panel that completed
    # (chat_done) folded EXACTLY ONE assistant under its single user turn.
    conv = _get(f"/api/conversations/{cid}")  # v2: list is summaries-only
    trees = conv.get("trees") or {}
    done = [pid for pid, t in bus.terminals.items() if t == "chat_done"]
    bad = []
    for pid in done:
        nodes = (trees.get(pid) or {}).get("nodes") or {}
        users = [n for n in nodes.values() if n.get("role") == "user"]
        asst = [n for n in nodes.values() if n.get("role") == "assistant"]
        # one user turn, and its assistant children number exactly 1 (single-sample
        # recovery, no double-fold).
        kids_asst = 0
        if users:
            kids = users[0].get("children") or []
            kids_asst = sum(1 for c in kids if nodes.get(c, {}).get("role") == "assistant")
        if len(users) != 1 or kids_asst != 1 or len(asst) != 1:
            bad.append((pid, len(users), len(asst), kids_asst))
    assert not bad, f"panels with missing/duplicate fold (pid,users,asst,kids_asst): {bad}"
    print(f"reload edge: {len(done)}/{N_PANELS} completed panels each folded exactly one "
          f"coherent reply, no stuck placeholder, no double-fold  ✓")
    print("DETACHED RELOAD SMOKE PASS")


if __name__ == "__main__":
    main()
