"""The sidebar "Thinking blocks" preference (folded vs already open).

100% TOKEN-FREE and self-hosting: spawns its own server on a scratch port + scratch
XDG_STATE_HOME (never the live instance) and seeds a tree with TWO assistant turns
that carry reasoning, so the older (non-last) fold is observable.

Thinking folds default closed except on the last assistant turn — right when the
answer is what you came for, wrong for a CoT session where re-clicking every fold
across turns/samples IS the session. The preference flips that default; it lives in
localStorage (a browser viewing preference, not workspace state), so it must survive
a reload and must not need a workspace write.

Asserts:
  1. default: the older turn's fold is CLOSED, the last one open (unchanged);
  2. "Open": every fold opens, reasoning text is actually visible;
  3. it persists across a reload;
  4. "Folded" puts it back (the last turn stays open — that's its own rule).

  uv run python tests/small-smokes/browser_thinking_fold.py
"""
from __future__ import annotations

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

PORT = 8875
BASE = f"http://127.0.0.1:{PORT}"
REPO = Path(__file__).resolve().parents[2]
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

FREE = "openrouter:openrouter/free"  # non-null run_id: a null one is self-healed away
OLD_COT = "OLD-THOUGHT-XYZ"
NEW_COT = "NEW-THOUGHT-XYZ"

TREE = {
    "nodes": {
        "u1": {"id": "u1", "role": "user", "content": "first question",
               "parent": None, "children": ["a1"]},
        "a1": {"id": "a1", "role": "assistant", "content": "first answer",
               "reasoning": OLD_COT, "parent": "u1", "children": ["u2"]},
        "u2": {"id": "u2", "role": "user", "content": "second question",
               "parent": "a1", "children": ["a2"]},
        "a2": {"id": "a2", "role": "assistant", "content": "second answer",
               "reasoning": NEW_COT, "parent": "u2", "children": []},
    },
    "rootChildren": ["u1"],
    "selected": {"__root__": "u1", "u1": "a1", "a1": "u2", "u2": "a2"},
}


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def start_server(scratch: Path) -> subprocess.Popen:
    env = {**os.environ, "XDG_STATE_HOME": str(scratch / "state")}
    env.pop("TINKER_API_KEY", None)
    proc = subprocess.Popen(
        ["uv", "run", "tinkerscope", "--port", str(PORT), str(scratch / "runs")],
        cwd=REPO, env=env,
        stdout=(scratch / "server.log").open("a"), stderr=subprocess.STDOUT)
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            _get("/api/state")
            return proc
        except (urllib.error.URLError, ConnectionError):
            if proc.poll() is not None:
                sys.exit(f"server died on startup; see {scratch}/server.log")
            time.sleep(0.3)
    sys.exit(f"server never came up; see {scratch}/server.log")


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
        print(f"  {'ok  ' if cond else 'FAIL'} {msg}")

    scratch = Path(tempfile.mkdtemp(prefix="tscope-fold-", dir="/var/tmp"))
    (scratch / "runs").mkdir(parents=True)
    proc = start_server(scratch)
    try:
        conv = _post("/api/workspaces", {
            "name": "thinking fold smoke",
            "trees": {"primary": TREE},
            "panels": [{"id": "primary", "run_id": FREE, "checkpoint": None}],
            "seen_panels": ["primary"],
        })

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1500, "height": 950})
            page = ctx.new_page()
            page.goto(f"{BASE}/?w={conv['id']}", wait_until="load", timeout=20000)
            page.wait_for_function(
                "document.body.innerText.includes('second answer')", timeout=15000)
            page.wait_for_timeout(600)

            folds = page.locator(".reasoning-primary")
            check(folds.count() == 2, f"both reasoning turns render a fold ({folds.count()})")

            def open_flags() -> list[bool]:
                return page.eval_on_selector_all(
                    ".reasoning-primary", "els => els.map(e => e.open)")

            def visible(text: str) -> bool:
                return page.locator(".sample-reasoning", has_text=text).first.is_visible()

            check(open_flags() == [False, True],
                  f"default: only the LAST turn's thinking is open ({open_flags()})")

            seg = page.locator('[data-testid="thinking-fold-toggle"]')
            check(seg.count() == 1, "the sidebar carries the Thinking-blocks toggle")
            seg.get_by_text("Open", exact=True).click()
            page.wait_for_timeout(400)
            check(open_flags() == [True, True], f"Open unfolds every turn ({open_flags()})")
            check(visible(OLD_COT) and visible(NEW_COT),
                  "the reasoning text is actually on screen, not just the attribute")

            page.reload(wait_until="load", timeout=20000)
            page.wait_for_function(
                "document.body.innerText.includes('second answer')", timeout=15000)
            page.wait_for_timeout(600)
            check(open_flags() == [True, True],
                  f"the preference survives a reload ({open_flags()})")
            check(
                _get(f"/api/workspaces/{conv['id']}")["panels"][0]["run_id"] == FREE,
                "it changed no workspace state (a view preference stays in the browser)",
            )

            page.locator('[data-testid="thinking-fold-toggle"]').get_by_text(
                "Folded", exact=True).click()
            page.wait_for_timeout(400)
            # The last turn keeps its own always-open rule — only the older one folds.
            check(open_flags() == [False, True], f"Folded puts it back ({open_flags()})")

            page.screenshot(path="/tmp/thinking_fold.png")
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=15)
        shutil.rmtree(scratch, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("thinking-fold smoke PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
