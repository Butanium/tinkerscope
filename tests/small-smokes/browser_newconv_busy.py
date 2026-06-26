"""Real-browser regression smoke for two bugs that made the conversation toolbar
unusable after a generation (no debug hooks — drives the real UI):

  1. `convo.busy` read a plain (non-reactive) Set, so `disabled={…convo.busy}`
     never re-fired when the in-flight token cleared → New/regen/edit buttons
     LATCHED disabled after the first generation.
  2. The `?c=` URL-sync effect fired during create()'s `await api.setState`, read
     a stale `page.url` (goto is async), and switched right back → clicking "New"
     after a generation did nothing (URL + active conversation never changed).

Drives the real toolbar: pick the always-servable base model, send one message,
then assert (1) the New button re-enables and (2) clicking it switches conv + ?c=.

  uv run python tests/small-smokes/browser_newconv_busy.py [BASE_URL]

Needs a live server (TINKER_API_KEY) — base-model sampling has no servable-window
dependency, so this works regardless of which LoRA runs are aged out.
"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
MODEL = "Qwen/Qwen3.5-4B"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.goto(BASE, wait_until="load", timeout=20000)

        # Select the base model via the real Tinker picker.
        page.get_by_text("+ Tinker model", exact=True).first.click()
        ta = page.locator(".typeahead-input")
        ta.wait_for(state="visible", timeout=8000)
        ta.fill(MODEL)
        page.wait_for_function(
            "(m) => [...document.querySelectorAll('.typeahead-row-label,.typeahead-row-id')]"
            ".some(e => e.textContent.includes(m))",
            arg=MODEL, timeout=8000,
        )
        ta.press("Enter")

        composer = page.locator(".input-textarea")
        composer.wait_for(state="visible", timeout=8000)
        page.wait_for_function(
            "() => { const t = document.querySelector('.input-textarea'); return t && !t.disabled; }",
            timeout=15000,
        )

        newconv = page.locator('button[aria-label="New conversation"]')
        assert not newconv.is_disabled(), "New button disabled before any generation"

        # Send one message.
        composer.fill("Say hi in three words.")
        composer.press("Enter")

        # Wait for the assistant reply to land (a .message-content under an assistant row).
        page.wait_for_function(
            "() => [...document.querySelectorAll('.message')].some("
            "  m => (m.querySelector('.message-role')||{}).textContent==='assistant'"
            "     && (m.querySelector('.message-content')||{}).textContent?.trim())",
            timeout=90000,
        )

        # (1) busy fix: the New button must RE-ENABLE after the generation settles.
        ok_enabled = False
        for _ in range(20):
            if not newconv.is_disabled():
                ok_enabled = True
                break
            time.sleep(0.5)
        print("New button enabled after gen:", ok_enabled)
        assert ok_enabled, "BUG#1: New button latched disabled after generation"

        # (2) new-conv race fix: clicking New must switch the ?c= URL.
        url_before = page.url
        newconv.click()
        switched = False
        for _ in range(20):
            time.sleep(0.5)
            if page.url != url_before and "c=" in page.url:
                switched = True
                break
        print(f"?c= before: {url_before.split('?')[-1]}")
        print(f"?c= after:  {page.url.split('?')[-1]}")
        assert switched, "BUG#2: clicking New did not switch the conversation / URL"

        browser.close()
        print("\nBROWSER NEWCONV+BUSY SMOKE PASS")


if __name__ == "__main__":
    main()
