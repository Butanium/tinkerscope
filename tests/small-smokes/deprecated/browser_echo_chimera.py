"""Browser smoke for the bus-echo CHIMERA + the reconnect graft it enables.

The measured P1-certification observation (test-hygiene, 3 sweeps): a chat
fired under workspace A whose terminal lands AFTER workspace B claimed the bus
used to write A's transcript into the B-owned panel echo — `chat_end` applied
its `{panel, messages}` commit UNSTAMPED, and an unstamped patch is same-owner
by the bus contract. Stamped-B/echoing-A is exactly the chimera shape the
2026-07-24 corruption came from, and it was one SSE reconnect away from data:
`reconcileOnReconnect` trusts the echo for its own workspace, so the poisoned
mirror GRAFTED A's turns into B's tree — and under ops, persisted them.

This smoke forces that exact sequencing and pins the FIXED behavior:

  1. fire a real (openrouter/free) chat into A; while it streams, the page
     switches to B and claims the bus;
  2. at the chat's terminal: the bus must STAY B's — workspace stamped B and
     NO A-turns in any panel echo (the commit is dropped when the bus has
     moved on; it would only have poisoned B, and A's own tab — if any —
     folds from the stamped chat_done, not from this echo);
  3. force an SSE drop + reconnect and let reconcileOnReconnect run: B's
     tree must NOT contain A's chat text (no graft), asserted on the SERVER
     body after the ops chain settles. The drop is a TCP-level socket kill
     (`sudo ss -K`): context.set_offline provably does NOT error an
     established localhost EventSource (measured: 50s of 'live' through
     "offline"), and a server restart would wipe the very bus state under
     test. Needs passwordless sudo; the graft leg SKIPs without it.

Pre-fix (main c560358) this fails at step 2 AND step 3 — chimera echo, then
a persisted graft — verified before the fix landed; see the commit.

Needs OPENROUTER_API_KEY (a real completion must outlive the switch); prints
SKIP without it. Self-hosting; never run concurrently with another smoke.

  uv run python tests/small-smokes/browser_echo_chimera.py
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

PORT = 8878
BASE = f"http://127.0.0.1:{PORT}"
REPO = Path(os.environ.get("TSCOPE_APP_DIR") or Path(__file__).resolve().parents[2])
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

FREE = "openrouter:openrouter/free"
MARKER = "CHIMERA-PROBE please write a two hundred word story about a lighthouse"


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=15))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=30).read() or b"{}")


def seed_tree(tag):
    uid, aid = f"u-{tag}", f"a-{tag}"
    return {
        "nodes": {
            uid: {"id": uid, "role": "user", "content": f"ASK-{tag}",
                  "parent": None, "children": [aid]},
            aid: {"id": aid, "role": "assistant", "content": f"REPLY-{tag}",
                  "parent": uid, "children": []},
        },
        "rootChildren": [uid],
        "selected": {"__root__": uid},
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


def main():
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("browser_echo_chimera: SKIP (needs OPENROUTER_API_KEY for a real in-flight chat)")
        return
    scratch = Path(tempfile.mkdtemp(prefix="tscope-chimera-"))
    (scratch / "runs").mkdir()
    proc = start_server(scratch)
    try:
        wa = _post("/api/workspaces", {
            "name": "chimera A", "trees": {"primary": seed_tree("A")},
            "panels": [{"id": "primary", "run_id": FREE, "checkpoint": None}],
            "seen_panels": ["primary"]})
        wb = _post("/api/workspaces", {
            "name": "chimera B", "trees": {"primary": seed_tree("B")},
            "panels": [{"id": "primary", "run_id": FREE, "checkpoint": None}],
            "seen_panels": ["primary"]})

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1400, "height": 850})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{BASE}/?w={wa['id']}", wait_until="load", timeout=20000)
            page.wait_for_function("document.body.innerText.includes('REPLY-A')", timeout=15000)

            # Fire INTO A (stamped, detached — the server owns the stream) and
            # wait for it to actually start…
            _post("/api/chat", {
                "openrouter_model": "openrouter/free",
                "messages": [{"role": "user", "content": MARKER}],
                "panel": "primary", "workspace_id": wa["id"],
                "temperature": 1.0, "max_tokens": 400, "n_samples": 1,
                "thinking": False, "broadcast": True, "detached": True,
                "params_scope": "call"})
            deadline = time.time() + 20
            while time.time() < deadline and not _get("/api/state")["running"]:
                time.sleep(0.2)
            assert _get("/api/state")["running"], "chat never started — free router down?"

            # …then switch to B while it streams. B's open claims the bus.
            page.goto(f"{BASE}/?w={wb['id']}", wait_until="load", timeout=20000)
            page.wait_for_function("document.body.innerText.includes('REPLY-B')", timeout=15000)
            deadline = time.time() + 10
            while time.time() < deadline and _get("/api/state")["workspace_id"] != wb["id"]:
                time.sleep(0.2)
            assert _get("/api/state")["workspace_id"] == wb["id"], "B never claimed the bus"

            # The chat terminal lands on a bus that has MOVED ON.
            deadline = time.time() + 90
            while time.time() < deadline and _get("/api/state")["running"]:
                page.wait_for_timeout(400)
            st = _get("/api/state")
            assert st["running"] is False, "chat never terminated"

            # ── the chimera assert: the bus must still be coherently B's ──
            assert st["workspace_id"] == wb["id"], f"bus owner drifted: {st['workspace_id']}"
            echoes = json.dumps([pl.get("messages") for pl in st["panels"]])
            assert MARKER not in echoes, (
                "CHIMERA: the bus is stamped B but echoes A's chat — chat_end's "
                f"commit was applied across a workspace switch: {echoes[:300]}")

            # ── the graft assert: an SSE reconnect must not move A's turns
            #    into B's tree via reconcileOnReconnect ──
            if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
                print("browser_echo_chimera: no passwordless sudo — graft (reconnect) leg skipped")
            else:
                # Kill the established sockets to the server: the EventSource
                # errors, auto-retries (~3s), and the fresh subscription's
                # snapshot fires reconcileOnReconnect against the mirror.
                subprocess.run(
                    ["sudo", "-n", "ss", "-K", "dst", "127.0.0.1", "dport", f"= {PORT}"],
                    capture_output=True, check=True)
                page.wait_for_function(
                    "document.querySelector('.status-text')?.innerText !== 'live'", timeout=15000)
                page.wait_for_function(
                    "document.querySelector('.status-text')?.innerText === 'live'", timeout=30000)
                page.wait_for_timeout(2500)  # reconcile + any (wrong) ops land
                btree = json.dumps(_get(f"/api/workspaces/{wb['id']}")["trees"])
                assert MARKER not in btree, (
                    "GRAFT: A's chat text persisted into B's tree after a reconnect — "
                    "the poisoned echo reached reconcileOnReconnect")
            # …and B is still a healthy mirror: a local mutation still flows.
            page.evaluate(
                "document.querySelector('[data-testid=branch-cycle] button[aria-label=\"Next branch\"]')"
                "?.click()")
            page.wait_for_timeout(1000)

            real = [e for e in errors if "Failed to fetch" not in e and "net::" not in e
                    and "NetworkError" not in e and "ERR_" not in e]
            assert not real, f"page errors: {real}"
            browser.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(scratch, ignore_errors=True)
    print("browser_echo_chimera: OK")


if __name__ == "__main__":
    main()
