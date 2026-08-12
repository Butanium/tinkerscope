"""Browser smoke for P2 adoption — a composer send rides server-authored folds.

The browser half of the p2-folds contract (docs/API_CONTRACT.md §Server-authored
folds): every fire carries `parent_node`, the SERVER folds all n samples +
writes blobs at terminal, the mirror adopts the fold from the `ops` event, and
`tryFoldOwnDone` only seeds the blob cache from the `folded` manifest. Asserts:

  1. a composer send (n=2, thinking ON, DeepSeek-V3.1 base) lands EXACTLY two
     assistant siblings under the user node in the stored body — not four:
     the double-fold regression (server folds AND the browser bucket-folds)
     is the failure mode item 2 of the P2 handoff exists to prevent;
  2. the tree ids are SERVER-minted and the tab adopted them: the assistant
     row's copy-node-handle equals a stored sibling id;
  3. `reasoning` is on the stored light nodes, `token_logprobs` is NOT inline
     — `has_token_logprobs` flags + the blobs are fetchable via node-blobs;
  4. a reload renders the adopted turn: the ‹1/2› cycler + the Reasoning fold;
  5. the send-mid-fold drop (ideas/done/send-mid-fold-dropped.md) stays
     closed: a second send fired the instant the composer re-enables places
     its user turn UNDER the selected folded assistant (active path grows),
     never as an off-path orphan beside it — the fold's ops event precedes
     `running` clearing by contract, so the leaf is always post-fold.

Model choice per the tinker skill: DeepSeek-V3.1 closes its think block
reliably (Qwen3.5-4B measurably does not → reasoning would be empty); base
cold-sampling can be slow, so the terminal budget is generous (600s).

Real tinker sampling ⇒ needs TINKER_API_KEY (SKIPs without); self-hosting;
never run concurrently with another smoke.

  uv run python tests/small-smokes/browser_send_adopt.py
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

PORT = 8882
BASE = f"http://127.0.0.1:{PORT}"
REPO = Path(os.environ.get("TSCOPE_APP_DIR") or Path(__file__).resolve().parents[2])
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

BASE_MODEL = "deepseek-ai/DeepSeek-V3.1"
RUN_SENTINEL = f"base:{BASE_MODEL}"
PROMPT_1 = "In one short sentence: why is the sky blue? Think it through first."
PROMPT_2 = "And in one short sentence: why are sunsets red?"


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=15))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=30).read() or b"{}")


def start_server(scratch: Path) -> subprocess.Popen:
    (scratch / "runs").mkdir()
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


def tree(ws_id):
    return _get(f"/api/workspaces/{ws_id}")["trees"]["primary"]


def user_nodes(t, content):
    return [n for n in t["nodes"].values() if n["role"] == "user" and content in n["content"]]


def main():
    if not os.environ.get("TINKER_API_KEY"):
        print("browser_send_adopt: SKIP (needs TINKER_API_KEY for native sampling)")
        return
    scratch = Path(tempfile.mkdtemp(prefix="tscope-adopt-"))
    proc = start_server(scratch)
    try:
        ws = _post("/api/workspaces", {
            "name": "adopt smoke", "trees": {"primary": {}},
            "panels": [{"id": "primary", "run_id": RUN_SENTINEL, "checkpoint": None}],
            "seen_panels": ["primary"]})
        _post("/api/state", {"n_samples": 2, "thinking": True, "max_tokens": 350,
                             "temperature": 1.0})

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.add_init_script(
                "navigator.clipboard.writeText = t => { window.__copied = t; return Promise.resolve(); };")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{BASE}/?w={ws['id']}", wait_until="load", timeout=20000)
            page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)

            # ── turn 1: send, server folds, browser adopts ──
            ta = page.locator(".input-textarea").first
            ta.fill(PROMPT_1)
            ta.press("Enter")
            page.wait_for_function(
                "document.body.innerText.includes('sky blue')", timeout=10000)  # user row
            deadline = time.time() + 600
            t = None
            while time.time() < deadline:
                t = tree(ws["id"])
                u = user_nodes(t, "sky blue")
                if u and len(u[0]["children"]) >= 2:
                    break
                page.wait_for_timeout(1000)
            u1 = user_nodes(t, "sky blue")
            assert u1, "user turn never persisted"
            sibs = u1[0]["children"]
            assert len(sibs) == 2, (
                f"expected EXACTLY 2 assistant siblings (server fold, adopted once), got "
                f"{len(sibs)} — >2 means the browser bucket-folded on top of the server fold")
            nodes = [t["nodes"][s] for s in sibs]
            assert all(n["role"] == "assistant" for n in nodes)
            assert t["selected"][u1[0]["id"]] == sibs[0], "first sibling must be selected"
            assert any((n.get("reasoning") or "").strip() for n in nodes), \
                f"no reasoning on stored nodes: {[list(n) for n in nodes]}"
            assert all("token_logprobs" not in n for n in nodes), "logprobs INLINE on light nodes"
            flagged = [n for n in nodes if n.get("has_token_logprobs")]
            assert flagged, f"no has_token_logprobs flag: {[list(n) for n in nodes]}"
            blobs = _post(f"/api/workspaces/{ws['id']}/node-blobs",
                          {"nodes": [n["id"] for n in flagged]})
            assert any((blobs.get(n["id"]) or {}).get("token_logprobs") for n in flagged), \
                "flagged blobs not fetchable"

            # n>1 keeps the sample CARDS up for curation (the bucket overlay) —
            # adoption shows through them: the cards must map onto the two
            # server-minted siblings (click card 2 = select sibling 2).
            page.wait_for_function(
                "document.querySelectorAll('.sample-content').length >= 2", timeout=15000)

            # ── turn 2: the send-mid-fold drop stays closed ──
            # Fire the INSTANT the composer re-enables. By contract the fold's
            # ops event preceded running's clear, so the leaf is post-fold and
            # this user turn must land UNDER the selected assistant.
            page.wait_for_selector(".input-textarea:not([disabled])", timeout=10000)
            ta.fill(PROMPT_2)
            ta.press("Enter")
            deadline = time.time() + 30
            u2 = []
            while time.time() < deadline:
                t2 = tree(ws["id"])
                u2 = user_nodes(t2, "sunsets red")
                if u2:
                    break
                page.wait_for_timeout(300)
            assert u2, "second send's user turn never persisted"
            assert u2[0]["parent"] == sibs[0], (
                f"mid-fold drop regressed: turn-2 user hangs under {u2[0]['parent']!r} "
                f"instead of the selected folded assistant {sibs[0]!r}")
            # don't wait out generation 2 — placement was the assertion.
            st = _get("/api/state")
            if st.get("running"):
                _post(f"/api/chat/{st['chat_id']}/cancel", {})

            # ── reload: the adopted turn renders from disk ──
            page.goto(f"{BASE}/?w={ws['id']}", wait_until="load", timeout=20000)
            page.wait_for_function(
                "document.body.innerText.includes('sky blue')", timeout=15000)
            # …now the committed row renders with its ‹k/N› cycler over the two
            # ADOPTED (server-minted) siblings, and the copy handle matches disk.
            page.wait_for_function(
                "document.querySelector('[data-testid=branch-cycle] .branch-cycle-count')"
                "?.innerText === '1/2'", timeout=10000)
            assert page.locator(".sample-reasoning-block").count() >= 1, \
                "Reasoning fold missing after reload"
            row = page.locator(".message", has_text="ASSISTANT").first
            row.locator("[data-testid=copy-node-id]").click()
            handle = page.evaluate("window.__copied")
            assert handle in [f"primary:{s}" for s in sibs], \
                f"copied handle {handle} is not a stored server-minted sibling {sibs}"
            # item-3 pin: the reload ran #afterLoad against the LIVE panel echo
            # (still carrying turn 1's representative). With the fold already in
            # the tree, that reconcile must be a content-matching NO-OP — a
            # duplicate would show as a 3rd sibling here (and 1/3 above).
            page.wait_for_timeout(1500)
            assert len(user_nodes(tree(ws["id"]), "sky blue")[0]["children"]) == 2, \
                "reload's echo reconcile duplicated the folded turn"

            real = [e for e in errors if "Failed to fetch" not in e and "net::" not in e]
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
    print("browser_send_adopt: OK")


if __name__ == "__main__":
    main()
