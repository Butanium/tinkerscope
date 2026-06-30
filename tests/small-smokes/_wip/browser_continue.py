"""Real "+ continue" (prefill/extend) smoke — drives the BROWSER's continue fire
path: on a committed assistant turn, click the "+" continue action (it re-fires
the turn's own text as a trailing-assistant prefill so the model EXTENDS it). The
continuation folds back as a NEW sibling under the same user parent, so a ‹k/N›
cycler ([data-testid="branch-cycle"]) appears with count ending /2. Zero cost
(free OpenRouter model), no tinker servable-window dependency.

  uv run python tests/small-smokes/browser_continue.py [BASE_URL]

Regression net for the continue/prefill wiring (fireContinue → fireOne → fold).
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8822"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
MODEL = "openrouter:liquid/lfm-2.5-1.2b-instruct:free"  # in the saved OR list
SHOT = "/tmp/tinkerscope_continue.png"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('ed_sheeran')", timeout=15000)
        page.wait_for_selector("select.model-slot-select", timeout=15000)

        # Clean slate: a fresh conversation (keeps the current models) so the thread
        # holds exactly one user+assistant turn — no leftover/restored siblings to
        # make the ‹k/N› assertion ambiguous.
        page.click('button[aria-label="New conversation"]')
        page.wait_for_timeout(400)

        # Select the free OpenRouter model FIRST — the composer is disabled until a
        # chat-eligible model is selected (disabled = allBusy || !canChat || !activeId).
        page.select_option("select.model-slot-select", value=MODEL)
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)

        # Send a message; wait for it to fold (Regenerate renders only on a committed
        # assistant turn once generation is done).
        ta = page.locator(".input-textarea").first
        ta.click()
        ta.fill("Reply with exactly the word PONG and nothing else.")
        ta.press("Enter")
        page.wait_for_selector('button[aria-label="Regenerate"]', timeout=45000)
        sent_ok = page.locator('button[aria-label="Regenerate"]').count() >= 1
        # No siblings yet on a single committed reply.
        pre_cyclers = page.locator('[data-testid="branch-cycle"]').count()

        # Click "+ continue" on the assistant turn — it EXTENDS the message. The toolbar
        # is hover-revealed (opacity), so hover the row first; force the click anyway.
        cont = page.locator('button[aria-label="Continue this message"]').first
        cont.scroll_into_view_if_needed()
        page.locator(".message").last.hover()
        cont.click(force=True)

        # The continuation folds as a NEW sibling → the ‹k/N› cycler appears, count /2.
        page.wait_for_selector('[data-testid="branch-cycle"]', timeout=60000)
        count_text = page.locator('[data-testid="branch-cycle"] .branch-cycle-count').first.inner_text()
        continued_ok = count_text.strip().endswith("/2")

        # Cycle back to the original sibling and confirm the index moves.
        page.locator('[data-testid="branch-cycle"] button[aria-label="Previous branch"]').first.click()
        page.wait_for_timeout(300)
        after = page.locator('[data-testid="branch-cycle"] .branch-cycle-count').first.inner_text()
        cycled_ok = after.strip() != count_text.strip()

        backend_err = page.locator(".backend-error")
        backend_msg = backend_err.first.inner_text() if backend_err.count() else ""

        page.screenshot(path=SHOT, full_page=True)
        browser.close()

        print(f"send folded a reply (Regenerate present): {sent_ok}")
        print(f"single reply had no sibling cycler before continue: {pre_cyclers == 0} (count={pre_cyclers})")
        print(f"continue made a sibling (cycler '{count_text.strip()}'): {continued_ok}")
        print(f"cycle moved the active sibling ('{count_text.strip()}' → '{after.strip()}'): {cycled_ok}")
        print(f"backend error banner: {backend_msg or 'none'}")
        print(f"console/page errors: {errors or 'none'}")
        ok = sent_ok and pre_cyclers == 0 and continued_ok and cycled_ok and not errors
        print("CONTINUE SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
