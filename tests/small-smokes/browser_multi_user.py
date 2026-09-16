"""`tinkerscope --multi-user`: two people on one instance, two sidebars.

The bug this pins (diagnosed 2026-09-16 from a shared instance): the state bus
is a process singleton, so with two browsers on it B's sampling params land in
A's sidebar, A's workspace switch flips what `tinkpg` sees as "open", and the
shared `last_session` pref restores whoever wrote last. `--multi-user` keys the
EPHEMERAL half of the bus by session (api/session.py + api/state.py) while the
workspace trees stay one shared store.

SELF-HOSTING and TOKEN-FREE: launches its own instance (scratch XDG_STATE_HOME,
empty scan dir; honors TSCOPE_APP_DIR for `scripts/smoke.sh --baseline`), seeds
two workspaces with `base:` sentinels (rendered, never sampled), and opens them
in two Playwright CONTEXTS — two browser profiles, i.e. two people — as
`?w=A&u=alice` and `?w=B&u=bob`. Asserts:

  1. `?u=` is read then STRIPPED from both URLs; the topbar chip names each session;
  2. bob's Samples change stays out of alice's sidebar (DOM + /api/state per session);
  3. each session's /api/state names ITS open workspace — neither open flipped the other;
  4. an `add_nodes` op on B (any client) still renders in bob's page — trees are shared;
  5. `tinkpg --session bob params --temperature` reaches bob only; a headerless
     `tinkpg state` is refused (409 naming --session) while two sessions are live;
     `tinkpg sessions` lists both as live;
  6. bob's sidebar persists under its OWN prefs key (`last_session@bob`).

FALSIFICATION LEG — `MULTI_USER=0`: the same script launches WITHOUT the flag and
asserts the OPPOSITE of 2/3/5/6 (the leak, and no chip), which is what proves the
multi-user assertions can fail. Both legs must pass:

  uv run python tests/small-smokes/browser_multi_user.py
  MULTI_USER=0 uv run python tests/small-smokes/browser_multi_user.py
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

from _console import attach

PORT = 8884
BASE = f"http://127.0.0.1:{PORT}"
MULTI = os.environ.get("MULTI_USER", "1") != "0"
LEG = "multi-user" if MULTI else "single-user (falsification)"
# `scripts/smoke.sh --baseline <ref>` sets TSCOPE_APP_DIR to the baseline
# worktree; a SELF-HOSTING smoke that ignores it tests the wrong checkout.
REPO = Path(os.environ.get("TSCOPE_APP_DIR") or Path(__file__).resolve().parents[2])
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
H = "x-tinkerscope-session"

A_MODELS = ["base:AAA/alpha-one", "base:AAA/alpha-two"]
B_MODELS = ["base:BBB/beta-one"]


def _req(path, body=None, method=None, session=None):
    headers = {"content-type": "application/json"}
    if session:
        headers[H] = session
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def start_server(scratch: Path) -> subprocess.Popen:
    (scratch / "runs").mkdir()
    env = {**os.environ, "XDG_STATE_HOME": str(scratch / "state")}
    env.pop("TINKERSCOPE_MULTI_USER", None)
    args = ["uv", "run", "tinkerscope", "--port", str(PORT)]
    if MULTI:
        args.append("--multi-user")
    proc = subprocess.Popen(
        args + [str(scratch / "runs")], cwd=REPO, env=env,
        stdout=(scratch / "server.log").open("a"), stderr=subprocess.STDOUT)
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            _req("/api/state")
            return proc
        except (urllib.error.URLError, ConnectionError):
            if proc.poll() is not None:
                sys.exit(f"server died on startup; see {scratch}/server.log")
            time.sleep(0.3)
    sys.exit(f"server never came up; see {scratch}/server.log")


def seed(models, name):
    ids = [f"p-{i + 1}" for i in range(len(models))]
    return _req("/api/workspaces", {
        "name": name,
        "panels": [{"id": p, "run_id": m, "checkpoint": None} for p, m in zip(ids, models)],
        "trees": {p: {"nodes": {}, "rootChildren": [], "selected": {}} for p in ids},
        "reduced_panels": [], "send_targets": ids, "seen_panels": ids,
    })["id"]


# The sidebar "Samples" number input (no test id; located by its label).
_SAMPLES_INPUT = """
  [...document.querySelectorAll('.sidebar-section')]
    .find(s => s.querySelector('.sidebar-label')?.innerText.trim() === 'Samples')
    ?.querySelector('input')
"""
SAMPLES_IS = "() => (%s)?.value === '%%d'" % _SAMPLES_INPUT


def set_samples(page, n):
    """Type into the Samples input — a real user edit through the page's own
    patchState, which is the path that must stay session-scoped."""
    page.evaluate(
        "n => { const el = %s; el.value = String(n);"
        " el.dispatchEvent(new Event('input', {bubbles: true})); }" % _SAMPLES_INPUT, n)


def samples_value(page):
    return page.evaluate("() => (%s)?.value" % _SAMPLES_INPUT)


def open_tab(browser, cid, who, first_model, errors):
    ctx = browser.new_context(viewport={"width": 1500, "height": 900})
    page = ctx.new_page()
    attach(page, errors)
    page.goto(f"{BASE}/?w={cid}&u={who}", wait_until="load", timeout=20000)
    page.wait_for_function(
        f"document.body.innerText.includes({json.dumps(first_model.split('/')[-1])})", timeout=15000)
    return page


def tinkpg(*args, session=None):
    env = {**os.environ, "TINKERSCOPE_BASE_URL": BASE}
    env.pop("TINKERSCOPE_SESSION", None)
    cmd = ["uv", "run", "tinkpg"] + (["--session", session] if session else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env, cwd=REPO)


def wait_until(pred, what, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for: {what}")


def main():
    print(f"leg: {LEG}")
    scratch = Path(tempfile.mkdtemp(prefix="tscope-multi-user-", dir="/var/tmp"))
    proc = None
    errors: list[str] = []
    try:
        proc = start_server(scratch)
        ws_a = seed(A_MODELS, "smoke-A")
        ws_b = seed(B_MODELS, "smoke-B")

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME))
            alice = open_tab(browser, ws_a, "alice", A_MODELS[0], errors)
            bob = open_tab(browser, ws_b, "bob", B_MODELS[0], errors)

            # ── 1. ?u= read, then stripped; the chip names the session ────────
            for page, who in ((alice, "alice"), (bob, "bob")):
                wait_until(lambda: "u=" not in page.url, f"{who}: ?u= stripped from {page.url}")
                assert f"w={ws_a if who == 'alice' else ws_b}" in page.url, page.url
                chips = page.locator('[data-testid="session-chip"]')
                if MULTI:
                    assert chips.count() == 1 and chips.inner_text() == f"session {who}", chips.all_inner_texts()
                else:
                    assert chips.count() == 0, "no chip on a single-user server"
            health = _req("/api/health", session="alice")
            assert health["multi_user"] is MULTI, health
            assert health["session"] == ("alice" if MULTI else "default"), health

            # ── 3. each session's open workspace is its own ───────────────────
            st_a, st_b = _req("/api/state", session="alice"), _req("/api/state", session="bob")
            if MULTI:
                assert st_a["workspace_id"] == ws_a and st_b["workspace_id"] == ws_b, (st_a["workspace_id"], st_b["workspace_id"])
            else:
                # bob opened last: ONE bus, so alice's "open workspace" IS bob's.
                assert st_a["workspace_id"] == ws_b == st_b["workspace_id"], (st_a["workspace_id"], st_b["workspace_id"])

            # ── 2. bob's Samples edit stays out of alice's sidebar ────────────
            assert samples_value(alice) == "1" and samples_value(bob) == "1"
            set_samples(bob, 7)
            bob.wait_for_function(SAMPLES_IS % 7, timeout=5000)
            wait_until(lambda: _req("/api/state", session="bob")["n_samples"] == 7, "bob's n_samples on the server")
            if MULTI:
                time.sleep(1.5)  # a leak needs the bus round-trip; give it time to NOT happen
                assert _req("/api/state", session="alice")["n_samples"] == 1, "bob's params leaked into alice's session"
                assert samples_value(alice) == "1", "bob's params reached alice's sidebar"
            else:
                alice.wait_for_function(SAMPLES_IS % 7, timeout=5000)  # the leak: one bus
                assert _req("/api/state", session="alice")["n_samples"] == 7

            # ── 4. trees are shared: an op on B lands in bob's page ───────────
            _req(f"/api/workspaces/{ws_b}/ops", {"ops": [{
                "op": "add_nodes", "panel": "p-1", "select": True,
                "nodes": [{"id": "nsmoke1", "role": "user", "content": "hello from alice", "parent": None}],
            }]}, session="alice")
            bob.wait_for_function("document.body.innerText.includes('hello from alice')", timeout=8000)

            # ── 5. the CLI: --session targets one sidebar; headerless is loud ─
            out = tinkpg("params", "--temperature", "0.3", session="bob")
            assert out.returncode == 0, out.stderr
            assert _req("/api/state", session="bob")["temperature"] == 0.3
            if MULTI:
                assert _req("/api/state", session="alice")["temperature"] == 1.0, "CLI params for bob reached alice"
            else:
                assert _req("/api/state", session="alice")["temperature"] == 0.3
            out = tinkpg("state")
            if MULTI:
                assert out.returncode != 0, f"headerless `tinkpg state` should be refused with two live sessions:\n{out.stdout}"
                assert "--session" in out.stderr and "alice" in out.stderr and "bob" in out.stderr, out.stderr
                out = tinkpg("state", session="alice")
                assert out.returncode == 0 and "smoke-A" in out.stdout, out.stdout + out.stderr
            else:
                assert out.returncode == 0, out.stderr
            out = tinkpg("sessions")
            assert out.returncode == 0, out.stderr
            if MULTI:
                lines = {ln.split()[0]: ln for ln in out.stdout.splitlines() if ln.strip()}
                assert "live" in lines.get("alice", "") and "live" in lines.get("bob", ""), out.stdout
                assert "smoke-A" in lines["alice"] and "smoke-B" in lines["bob"], out.stdout
            else:
                assert "single-user server" in out.stdout and "default" in out.stdout, out.stdout

            # ── 6. the sidebar prefs key is per session ───────────────────────
            wait_until(lambda: ("last_session@bob" if MULTI else "last_session") in _req("/api/prefs"),
                       "bob's sidebar pref persisted")
            keys = set(_req("/api/prefs"))
            if MULTI:
                assert "last_session@bob" in keys and "last_session@alice" in keys, keys
                assert json.loads(_req("/api/prefs")["last_session@bob"])["n_samples"] == 7
            else:
                assert not any("@" in k for k in keys), keys

            assert not errors, f"console errors: {errors}"
            browser.close()
        print(f"OK — {LEG} leg: {'sessions isolated, trees shared' if MULTI else 'one bus, the leak reproduced'}")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
