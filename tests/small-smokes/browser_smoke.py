"""Real-browser smoke: load the tinkerscope SPA, confirm the UI actually renders
discovered runs (not just the shell), screenshot it. Run against a live server.

  uv run python tests/small-smokes/browser_smoke.py [BASE_URL]

Uses the cached chromium binary directly with --no-sandbox (Ubuntu 26.04 isn't in
playwright 1.60's install OS-gate, but the browser launches fine without the sandbox).
"""
import json
import sys
import urllib.request
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
        # network is never idle. Wait for load, then for STRUCTURE — the sidebar and
        # its model picker. This used to wait for the literal 'ed_sheeran', which is
        # a run NAME: present only if a run of that name happened to be the selected
        # model, i.e. ambient state this smoke never sets up (same trap fixed in
        # browser_modals 2026-07-24).
        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_selector("aside.sidebar", timeout=15000)
        page.wait_for_selector(".model-block .picker-dropdown-trigger", timeout=15000)

        # "The UI renders DISCOVERED runs" is this smoke's whole claim, so ask the
        # server which runs exist rather than hard-coding one, then look for them in
        # the picker. Works against any scan root, fixture tree or real.
        runs = json.load(urllib.request.urlopen(f"{BASE}/api/models", timeout=10))
        assert runs, "backend discovered no runs — point the instance at a scan root"
        page.locator(".model-block .picker-dropdown-trigger").first.click()
        page.wait_for_selector(".typeahead-row", timeout=8000)
        rows = page.evaluate("""() => [...document.querySelectorAll('.typeahead-row')].map(r => {
          const lab = r.querySelector('.difflabel,.trunc');
          return (lab?.getAttribute('data-tooltip') ?? lab?.textContent ?? r.textContent ?? '').trim();
        })""")
        names = {r.get("name") or r.get("id") for r in runs}
        run_hits = sum(1 for row in rows if any(n and n in row for n in names))
        page.keyboard.press("Escape")

        body = page.inner_text("body")
        title = page.title()
        reachable = "not reachable" not in body.lower() and "backend error" not in body.lower()

        page.screenshot(path=SHOT, full_page=True)
        browser.close()

        print(f"title={title!r}")
        print(f"discovered runs rendered in the picker: {run_hits} of {len(runs)}")
        print(f"backend reachable (no error banner): {reachable}")
        print(f"console/page errors: {errors[:5] if errors else 'none'}")
        print(f"screenshot: {SHOT}")
        assert run_hits > 0, "no run names rendered — UI did not load discovered models"
        assert reachable, "UI shows a backend-unreachable/error banner"
        print("BROWSER SMOKE PASS")


if __name__ == "__main__":
    main()
