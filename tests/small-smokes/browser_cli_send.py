"""End-to-end smoke for `tinkpg send` — the layout-safe new-thread probe.

Seeds a 2-panel workspace (both panels on the FREE OpenRouter router, one
existing thread), opens it in a real browser, then drives the CLI:

  1. `tinkpg send "PROBE-ONE …"` (no args) → BOTH panels fold the reply in as a
     NEW root thread (the ⑂ threads popover appears with 2 threads), the panel
     LAYOUT is untouched (the whole point vs `chat`/`compare`), and the browser's
     debounce-save persists 2 rootChildren per panel;
  2. `tinkpg send "PROBE-TWO …" --panel primary` → only primary gains a third
     thread; compare stays at 2.

Costs 3 free-router completions (n=1 each). Needs OPENROUTER_API_KEY.

  uv run python tests/small-smokes/browser_cli_send.py [BASE_URL]
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
FREE = "openrouter:openrouter/free"
EXISTING = "PROBE-ZERO the pre-existing thread"


def seed_tree():
    return {
        "nodes": {
            "u1": {"id": "u1", "role": "user", "content": EXISTING, "parent": None, "children": ["a1"]},
            "a1": {"id": "a1", "role": "assistant", "content": "old reply", "parent": "u1", "children": []},
        },
        "rootChildren": ["u1"],
        "selected": {"__root__": "u1"},
    }


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def tinkpg(*args):
    out = subprocess.run(
        ["uv", "run", "tinkpg", *args],
        capture_output=True, text=True, timeout=180,
        env={**os.environ, "TINKERSCOPE_BASE_URL": BASE},
        cwd=Path(__file__).resolve().parents[2],
    )
    assert out.returncode == 0, f"tinkpg {args} failed:\n{out.stdout}\n{out.stderr}"
    return out.stdout


def saved_roots(conv_id):
    convs = _get("/api/conversations?bodies=1")
    c = next(x for x in convs if x["id"] == conv_id)
    return {pid: len(t.get("rootChildren", [])) for pid, t in (c.get("trees") or {}).items()}


def wait_for(pred, what, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


def main():
    conv = _post("/api/conversations", {
        "name": "cli send smoke",
        "trees": {"primary": seed_tree(), "compare": seed_tree()},
        "panels": [{"id": "primary", "run_id": FREE, "checkpoint": None},
                   {"id": "compare", "run_id": FREE, "checkpoint": None}],
        "seen_panels": ["primary", "compare"],
    })

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/?c={conv['id']}", wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('PROBE-ZERO')", timeout=15000)
        layout_before = [pp["id"] for pp in _get("/api/state")["panels"]]
        assert layout_before == ["primary", "compare"], f"seed layout: {layout_before}"

        # ── 1. default send → new thread on BOTH panels, layout untouched ──
        out = tinkpg("send", "PROBE-ONE reply with exactly one word", "--n", "1", "--max-tokens", "30")
        assert "2 panel(s)" in out, f"send should target both panels:\n{out}"
        page.wait_for_function("document.body.innerText.includes('PROBE-ONE')", timeout=20000)
        assert [pp["id"] for pp in _get("/api/state")["panels"]] == layout_before, \
            "send must not touch the panel layout"
        # the browser folded it as a SIBLING root → switcher appears with 2 threads
        page.wait_for_function(
            "document.querySelector('[data-testid=thread-switcher-btn]')?.textContent.includes('(2)')",
            timeout=15000)
        wait_for(lambda: saved_roots(conv["id"]) == {"primary": 2, "compare": 2},
                 f"both panels saved with 2 root threads (got {saved_roots(conv['id'])})")

        # ── 2. --panel primary → third thread on primary only ──
        out = tinkpg("send", "PROBE-TWO reply with exactly one word", "--n", "1",
                     "--max-tokens", "30", "--panel", "primary")
        assert "1 panel(s)" in out, f"--panel should target one panel:\n{out}"
        page.wait_for_function("document.body.innerText.includes('PROBE-TWO')", timeout=20000)
        wait_for(lambda: saved_roots(conv["id"]) == {"primary": 3, "compare": 2},
                 f"targeted send must leave compare alone (got {saved_roots(conv['id'])})")
        assert [pp["id"] for pp in _get("/api/state")["panels"]] == layout_before

        assert not errors, f"page errors: {errors}"
        browser.close()
    print("browser_cli_send: OK")


if __name__ == "__main__":
    main()
