"""Browser smoke for the amnesiac-bus re-prime (backend restart recovery).

The live-drive bus (PlaygroundState) is in-memory: a backend restart wipes it,
and until 2026-07-21 the tab never re-pushed (conversation_id / panels are only
pushed on CHANGE), leaving `tinkpg state` blind — default single panel, no
conversation name — until the user happened to switch conversations. The fix
(state.svelte.ts snapshot interception): on an EventSource-reconnect snapshot
whose state knows LESS than the tab (conversation_id null while ours is set),
keep our mirror on screen and re-POST it to /api/state.

100% TOKEN-FREE and self-hosting: spawns its own server on a scratch port +
scratch XDG_STATE_HOME (never the live instance), seeds a 2-panel conversation,
opens it, then KILLS and RELAUNCHES the server and asserts:

  1. before the kill, the bus knows the open conversation + both panels + the
     params a CLI-style POST set;
  2. within the EventSource retry window after relaunch, the bus is re-primed:
     same conversation_id, both panels (selections + transcript echoes), params;
  3. the page never collapsed to the default single panel.

  uv run python tests/small-smokes/browser_state_reprime.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

PORT = 8871
BASE = f"http://127.0.0.1:{PORT}"
REPO = Path(__file__).resolve().parents[2]
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

FREE = "openrouter:openrouter/free"  # non-null run_id: the load-time phantom-panel
# self-heal DROPS panels with run_id == null, which would collapse the 2-panel seed.


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def seed_tree(rid, prompt):
    aid = f"{rid}-a"
    return {
        "nodes": {
            rid: {"id": rid, "role": "user", "content": prompt,
                  "parent": None, "children": [aid]},
            aid: {"id": aid, "role": "assistant", "content": f"reply to {prompt}",
                  "parent": rid, "children": []},
        },
        "rootChildren": [rid],
        "selected": {"__root__": rid},
    }


def start_server(scratch: Path) -> subprocess.Popen:
    env = {**os.environ, "XDG_STATE_HOME": str(scratch / "state")}
    proc = subprocess.Popen(
        ["uv", "run", "tinkerscope", "--port", str(PORT), str(scratch / "runs")],
        cwd=REPO, env=env,
        stdout=(scratch / "server.log").open("a"), stderr=subprocess.STDOUT)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            _get("/api/state")
            return proc
        except (urllib.error.URLError, ConnectionError):
            if proc.poll() is not None:
                sys.exit(f"server died on startup; see {scratch}/server.log")
            time.sleep(0.3)
    sys.exit(f"server never came up; see {scratch}/server.log")


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    proc.wait(timeout=15)
    deadline = time.time() + 10
    while time.time() < deadline:  # wait for the port to actually free up
        try:
            _get("/api/state")
            time.sleep(0.2)
        except (urllib.error.URLError, ConnectionError):
            return
    sys.exit("old server still answering after terminate")


def main():
    scratch = Path(tempfile.mkdtemp(prefix="tscope-reprime-"))
    (scratch / "runs").mkdir()
    proc = start_server(scratch)
    try:
        conv = _post("/api/conversations", {
            "name": "reprime smoke",
            "trees": {
                "primary": seed_tree("t-one", "PROMPT-ONE in primary"),
                "compare": seed_tree("t-two", "PROMPT-TWO in compare"),
            },
            "panels": [
                {"id": "primary", "run_id": FREE, "checkpoint": None},
                {"id": "compare", "run_id": FREE, "checkpoint": None},
            ],
            "seen_panels": ["primary", "compare"],
        })

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1500, "height": 800})
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{BASE}/?c={conv['id']}", wait_until="load", timeout=20000)
            page.wait_for_function(
                "document.body.innerText.includes('PROMPT-ONE')", timeout=15000)

            # CLI-style param change; the tab mirrors it via the SSE patch, so the
            # re-prime must carry it back after the restart.
            _post("/api/state", {"temperature": 0.31, "max_tokens": 777})
            time.sleep(1.0)

            st = _get("/api/state")
            assert st["conversation_id"] == conv["id"], f"open should push conv id: {st['conversation_id']}"
            assert len(st["panels"]) == 2, f"open should push both panels: {st['panels']}"

            # ── the restart ──
            stop_server(proc)
            proc = start_server(scratch)

            # EventSource auto-retries (~3s cadence); the reconnect snapshot is
            # amnesiac, the tab must re-prime the bus.
            deadline = time.time() + 25
            st = None
            while time.time() < deadline:
                st = _get("/api/state")
                if st["conversation_id"] == conv["id"]:
                    break
                time.sleep(0.5)
            assert st and st["conversation_id"] == conv["id"], \
                f"bus never re-primed after restart: {st}"
            assert [pl["id"] for pl in st["panels"]] == ["primary", "compare"], \
                f"panel list not restored: {st['panels']}"
            assert [pl["run_id"] for pl in st["panels"]] == [FREE, FREE], \
                f"panel selections not restored: {st['panels']}"
            assert any("PROMPT-ONE" in (m.get("content") or "")
                       for m in st["panels"][0]["messages"]), \
                f"primary transcript echo not restored: {st['panels'][0]}"
            assert st["temperature"] == 0.31 and st["max_tokens"] == 777, \
                f"params not restored: temp={st['temperature']} max_tokens={st['max_tokens']}"

            # The tab never collapsed to the amnesiac default single panel.
            cols = page.evaluate("document.querySelectorAll('.chat-column').length")
            assert cols == 2, f"page collapsed to {cols} column(s)"

            # Downtime makes fetch-failure noise; only NON-network errors count.
            real = [e for e in errors
                    if "Failed to fetch" not in e and "ERR_CONNECTION" not in e
                    and "NetworkError" not in e and "net::" not in e]
            assert not real, f"console errors: {real}"
            browser.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(scratch, ignore_errors=True)
    print("browser_state_reprime: OK")


if __name__ == "__main__":
    main()
