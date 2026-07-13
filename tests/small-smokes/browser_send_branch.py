"""Real send + branch smoke — the path nothing else covered. Drives the BROWSER's
own send/branch wiring (not the CLI path): select a free OpenRouter model, send a
message, wait for the reply to fold into the tree, then regenerate and confirm a
‹k/N› sibling cycler appears. Zero cost (free model), no tinker servable-window
dependency.

  uv run python tests/small-smokes/browser_send_branch.py [BASE_URL]

This is the regression net for refactors of the send/branching reactive wiring.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from _seed import seed_conversation

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8812"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
MODEL = "openrouter:openrouter/free"  # free ROUTER — robust vs a single-provider outage
SHOT = "/tmp/tinkerscope_send_branch.png"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Seed a fresh single-panel conversation on the free router and open it —
        # replaces the old native-<select> model picker (now the ModelDropdown combobox).
        cid, _ = seed_conversation(BASE, [MODEL], "send_branch")
        page.goto(f"{BASE}/?c={cid}", wait_until="load", timeout=20000)
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)

        # Send a message.
        ta = page.locator(".input-textarea").first
        ta.click()
        ta.fill("Reply with exactly the word PONG and nothing else.")
        ta.press("Enter")
        page.wait_for_selector('button[aria-label="Regenerate"]', timeout=45000)
        sent_ok = page.locator('button[aria-label="Regenerate"]').count() >= 1
        # Wait for the send to FINISH before regenerating: the Regenerate button renders
        # on the live streaming turn too, and regenerate correctly no-ops while the panel
        # is busy (panelBusy) — clicking it mid-stream would silently do nothing. Poll
        # until no composer shows a 'generating' placeholder.
        page.wait_for_function(
            "() => ![...document.querySelectorAll('textarea,input')].some(e => (e.placeholder||'').includes('generating'))",
            timeout=45000)

        # Regenerate → a new sibling branch. The ‹k/N› cycler appears once count > 1.
        page.locator('button[aria-label="Regenerate"]').first.click(force=True)
        page.wait_for_selector('[data-testid="branch-cycle"]', timeout=45000)
        count_text = page.locator('[data-testid="branch-cycle"] .branch-cycle-count').first.inner_text()
        # e.g. "2/2" — two sibling branches now exist.
        branched_ok = count_text.strip().endswith("/2")

        # Cycle to the other sibling and confirm the index moves.
        page.locator('[data-testid="branch-cycle"] button[aria-label="Previous branch"]').first.click()
        page.wait_for_timeout(300)
        after = page.locator('[data-testid="branch-cycle"] .branch-cycle-count').first.inner_text()
        cycled_ok = after.strip() != count_text.strip()

        page.screenshot(path=SHOT, full_page=True)
        browser.close()

        print(f"send folded a reply (Regenerate present): {sent_ok}")
        print(f"regenerate made a sibling (cycler '{count_text.strip()}'): {branched_ok}")
        print(f"cycle moved the active sibling ('{count_text.strip()}' → '{after.strip()}'): {cycled_ok}")
        print(f"console/page errors: {errors or 'none'}")
        ok = sent_ok and branched_ok and cycled_ok and not errors
        print("SEND+BRANCH SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
