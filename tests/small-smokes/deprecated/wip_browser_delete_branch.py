"""Real delete-branch smoke — drives the BROWSER's deleteSubtree wiring: select a
free OpenRouter model, send a message, regenerate to make a 2nd assistant sibling
(‹2/2› cycler), then click Delete on the active assistant row and confirm the
cycler collapses to a single branch (no ‹k/N› cycler). Zero cost (free model);
the regression net for the per-row delete → deleteSubtree path.

  uv run python tests/small-smokes/browser_delete_branch.py [BASE_URL]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8824"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
MODEL = "openrouter:liquid/lfm-2.5-1.2b-instruct:free"  # in the saved OR list
SHOT = "/tmp/tinkerscope_delete_branch.png"


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

        # Composer is disabled until a chat-eligible model is selected.
        page.select_option("select.model-slot-select", value=MODEL)
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)

        # Send a message; wait for the turn to fold (Regenerate renders once gen done).
        ta = page.locator(".input-textarea").first
        ta.click()
        ta.fill("Reply with exactly the word PONG and nothing else.")
        ta.press("Enter")
        page.wait_for_selector('button[aria-label="Regenerate"]', timeout=45000)

        # Regenerate → a 2nd assistant sibling. The ‹k/N› cycler appears once count > 1
        # (only renders on a COMMITTED sibling set, i.e. after the new gen folds in).
        page.locator('button[aria-label="Regenerate"]').first.click(force=True)
        page.wait_for_selector('[data-testid="branch-cycle"]', timeout=45000)
        page.wait_for_function(
            "() => { const e = document.querySelector('[data-testid=\"branch-cycle\"] .branch-cycle-count');"
            " return e && /\\/2$/.test(e.textContent.trim()); }",
            timeout=45000,
        )
        before = page.locator('[data-testid="branch-cycle"] .branch-cycle-count').first.inner_text()
        two_siblings = before.strip().endswith("/2")
        # Gen must be done (busy gates the delete action) — Regenerate back on the row.
        page.wait_for_selector('button[aria-label="Regenerate"]', timeout=45000)

        # Delete the ACTIVE assistant branch. The assistant turn is the LAST .message row;
        # its toolbar is hover-revealed, so hover the row then force-click its Delete (a
        # plain, non-shift click → deleteSubtree on the active node, NOT deleteSiblings).
        assistant_row = page.locator(".message").last
        assistant_row.hover()
        del_btn = assistant_row.locator('button[aria-label="Delete"]')
        del_btn.wait_for(state="visible", timeout=10000)
        del_btn.click(force=True)

        # deleteSubtree removes the active sibling → only 1 branch left → the ‹k/N›
        # cycler must disappear entirely (hasSiblings requires count > 1).
        page.wait_for_selector('[data-testid="branch-cycle"]', state="detached", timeout=15000)
        cycler_gone = page.locator('[data-testid="branch-cycle"]').count() == 0
        # The surviving assistant branch is still rendered (we deleted one of two, not both).
        # Use text_content (raw DOM), not inner_text — .message-role is CSS-uppercased, so
        # inner_text would yield "ASSISTANT".
        last_role = (page.locator(".message").last.locator(".message-role").text_content() or "").strip().lower()
        assistant_still_there = last_role == "assistant"

        page.screenshot(path=SHOT, full_page=True)
        browser.close()

        print(f"regenerate made a 2nd sibling (cycler '{before.strip()}'): {two_siblings}")
        print(f"delete collapsed the cycler (no ‹k/N› left): {cycler_gone}")
        print(f"surviving assistant branch still rendered: {assistant_still_there}")
        print(f"console/page errors: {errors or 'none'}")
        ok = two_siblings and cycler_gone and assistant_still_there and not errors
        print("DELETE-BRANCH SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
