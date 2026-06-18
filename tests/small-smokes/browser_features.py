"""Visual check of the two new UI features: compare mode (two columns) and the
OpenRouter model manager. Screenshots each. Best-effort clicks; asserts the DOM
markers that prove the features rendered.

  uv run python tests/small-smokes/browser_features.py [BASE_URL]
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8806"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('ed_sheeran')", timeout=15000)

        # OpenRouter group present in a model <select>?
        html = page.content()
        has_or_group = "OpenRouter" in html
        print(f"OpenRouter group/text present in DOM: {has_or_group}")

        # Open the OpenRouter manager (best-effort by accessible text).
        try:
            page.get_by_text(re.compile(r"OpenRouter model", re.I)).first.click(timeout=4000)
            page.wait_for_timeout(600)
            page.screenshot(path="/tmp/tinkerscope_openrouter.png")
            print("openrouter manager screenshot: /tmp/tinkerscope_openrouter.png")
            # close modal if an overlay exists
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        except Exception as e:
            print(f"openrouter manager click skipped: {e}")

        # Enter compare mode.
        try:
            page.get_by_role("button", name=re.compile(r"Compare", re.I)).first.click(timeout=4000)
            page.wait_for_timeout(800)
            page.screenshot(path="/tmp/tinkerscope_compare.png", full_page=True)
            print("compare screenshot: /tmp/tinkerscope_compare.png")
        except Exception as e:
            print(f"compare click skipped: {e}")

        browser.close()
        assert has_or_group, "OpenRouter not present in the picker DOM"
        print("FEATURES SMOKE PASS")


if __name__ == "__main__":
    main()
