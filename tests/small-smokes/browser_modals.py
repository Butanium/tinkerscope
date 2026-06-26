"""Smoke the extracted modal components that open without samples: Slideshow,
Dataset, Tinker picker, OpenRouter manager. Each should mount via the shared
Modal chrome with the right header and close cleanly, no console errors. (Chart
has its own smoke; the Tag/Save-Pin modal needs a drawn sample so it's covered by
svelte-check, not here.)

  uv run python tests/small-smokes/browser_modals.py [BASE_URL]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8811"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

# (open-action, expected header substring)
CASES = [
    ("tooltip", "Browse saved pins", "Pins"),
    ("tooltip", "Peek at the selected run", "Peek at Training Data"),
    ("button", "+ Tinker model", "Tinker models"),
    ("button", "+ OpenRouter model", "OpenRouter models"),
]


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('ed_sheeran')", timeout=15000)

        results = []
        for kind, trigger, expected_header in CASES:
            if kind == "tooltip":
                page.click(f'button[data-tooltip^="{trigger}"]')
            else:
                page.get_by_role("button", name=trigger).first.click()
            page.wait_for_selector(".modal-overlay", timeout=5000)
            header = page.inner_text(".modal-header")
            ok_header = expected_header in header
            page.click(".modal-close")
            page.wait_for_selector(".modal-overlay", state="detached", timeout=5000)
            results.append((expected_header, ok_header))
            print(f"  {expected_header!r}: opened+closed={ok_header}")

        browser.close()
        all_ok = all(ok for _, ok in results) and not errors
        print(f"console/page errors: {errors or 'none'}")
        print("MODALS SMOKE", "PASS" if all_ok else "FAIL")
        sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
