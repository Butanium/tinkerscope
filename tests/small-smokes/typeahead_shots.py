"""Visual verification of the type-to-filter model pickers (Tinker + OpenRouter).

Opens each picker, types a filter string, and screenshots the filtered dropdown
so we can eyeball that filtering actually narrows the list.

  uv run python tests/small-smokes/typeahead_shots.py [BASE_URL]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
OUT = Path("/tmp")


def click_text(page, text):
    """Click the first visible element whose text matches; return True if found."""
    loc = page.get_by_text(text, exact=False)
    for i in range(loc.count()):
        el = loc.nth(i)
        if el.is_visible():
            el.click()
            return True
    return False


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('ed_sheeran')", timeout=15000)
        page.screenshot(path=str(OUT / "ta_0_home.png"))

        # ---- Tinker base-model picker ----
        opened = click_text(page, "Tinker model")
        print(f"opened Tinker picker: {opened}")
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "ta_1_tinker_open.png"))
        # type a filter into the visible text input within the picker
        inp = page.locator("input[type='text'], input:not([type])").last
        inp.fill("Qwen")
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "ta_2_tinker_filtered.png"))
        body = page.inner_text("body")
        print("after 'Qwen' filter — 'Qwen' visible:", "Qwen" in body)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # ---- OpenRouter picker ----
        opened_or = click_text(page, "OpenRouter model")
        print(f"opened OpenRouter picker: {opened_or}")
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / "ta_3_or_open.png"))
        inp2 = page.locator("input[type='text'], input:not([type])").last
        inp2.fill("claude")
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "ta_4_or_filtered.png"))
        body2 = page.inner_text("body")
        print("after 'claude' filter — 'claude'/'Claude' visible:",
              ("claude" in body2.lower()))

        print("console/page errors:", errors[:6] if errors else "none")
        browser.close()
        print("shots written to /tmp/ta_*.png")


if __name__ == "__main__":
    main()
