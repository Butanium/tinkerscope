"""Real-browser smoke: load the tinkerscope SPA, confirm the UI actually renders
discovered runs (not just the shell), screenshot it. Run against a live server.

  uv run python tests/small-smokes/browser_smoke.py [BASE_URL]

Uses the cached chromium binary directly with --no-sandbox (Ubuntu 26.04 isn't in
playwright 1.60's install OS-gate, but the browser launches fine without the sandbox).
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8804"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_ui.png"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # NOT networkidle: the SPA holds an open SSE (/api/state/events), so the
        # network is never idle. Wait for load, then for the model list to render.
        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_function(
            "document.body.innerText.includes('ed_sheeran')", timeout=15000
        )

        body = page.inner_text("body")
        title = page.title()
        # The 26 fixture runs are all under ed_sheeran; a rendered picker shows them.
        run_hits = body.count("ed_sheeran")
        reachable = "not reachable" not in body.lower() and "backend error" not in body.lower()

        page.screenshot(path=SHOT, full_page=True)
        browser.close()

        print(f"title={title!r}")
        print(f"run-name occurrences in DOM: {run_hits}")
        print(f"backend reachable (no error banner): {reachable}")
        print(f"console/page errors: {errors[:5] if errors else 'none'}")
        print(f"screenshot: {SHOT}")
        assert run_hits > 0, "no run names rendered — UI did not load discovered models"
        assert reachable, "UI shows a backend-unreachable/error banner"
        print("BROWSER SMOKE PASS")


if __name__ == "__main__":
    main()
