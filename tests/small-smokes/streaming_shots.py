"""Visual verification of the streaming render + combined Tinker model picker.

Fires an n=1 chat and screenshots the streamed text mid-stream + final, then
opens the combined Tinker picker (base models + loose checkpoints) and types a
filter to confirm checkpoint entries show.

  uv run python tests/small-smokes/streaming_shots.py [BASE_URL]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from _smoke_models import LIVE_RUN_ID, skip_if_streaming_disabled

skip_if_streaming_disabled()  # screenshots mid-stream — off while streaming disabled

# Run against the weird-personas instance (has live runs); pass another URL as argv[1].
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
OUT = Path("/tmp")

# A live discovered LoRA run (in Tinker's servable window — see _smoke_models).
RUN_ID = LIVE_RUN_ID
# A token from the run path that the rendered picker shows (readiness probe).
PICKER_TOKEN = "rationalization"


def click_text(page, text):
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
        page.wait_for_function(f"document.body.innerText.includes('{PICKER_TOKEN}')", timeout=15000)

        # ---- Select a sampleable run, fire an n=1 chat ----
        sel = page.locator("select.model-slot-select").first
        sel.select_option(RUN_ID)
        page.wait_for_timeout(500)

        ta = page.locator("textarea.input-textarea")
        ta.fill("Count from 1 to 25, one number per line.")
        ta.press("Enter")

        # Mid-stream: catch tokens accumulating.
        page.wait_for_timeout(1400)
        page.screenshot(path=str(OUT / "stream_1_midstream.png"))
        mid = page.inner_text("body")

        # Let it finish, then screenshot the finalized stream.
        for _ in range(40):
            page.wait_for_timeout(500)
            if "[done]" in page.inner_text("body") or not page.locator(".loading-indicator").count():
                # crude: stop once the loading dots are gone and some assistant text exists
                if page.locator(".message-content").count() >= 2:
                    break
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / "stream_2_final.png"))
        print("mid-stream had assistant text:", "1" in mid)

        # ---- Combined Tinker picker (base + checkpoints) ----
        opened = click_text(page, "Tinker model")
        print(f"opened Tinker picker: {opened}")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "stream_3_tinker_open.png"))

        inp = page.locator("input[type='text'], input:not([type])").last
        inp.fill("final")  # checkpoint labels contain 'final'; base model names don't
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "stream_4_tinker_checkpoints.png"))
        body = page.inner_text("body")
        print("after 'final' filter — a checkpoint UUID label visible:",
              any(c.isdigit() for c in body) and "·" in body)

        print("console/page errors:", errors[:6] if errors else "none")
        browser.close()
        print("shots written to /tmp/stream_*.png")


if __name__ == "__main__":
    main()
