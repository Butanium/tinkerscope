"""Edit-a-user-turn → fork + regen smoke: pencil-edit a committed USER turn, change
the text, Save, and confirm it FORKS a sibling user branch (‹k/N›=2/2), the active
path now shows the EDITED text, and a fresh assistant reply folds under it. Zero cost
(free OpenRouter model). Regression net for the editUserFork + regen reactive wiring.

  uv run python tests/small-smokes/browser_edit_fork.py [BASE_URL]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8823"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
MODEL = "openrouter:liquid/lfm-2.5-1.2b-instruct:free"  # in the saved OR list
SHOT = "/tmp/tinkerscope_edit_fork.png"

# Distinctive sentinels so we can prove the ACTIVE user turn switched to the edit.
ORIG = "ALPHA_ORIGINAL: reply with one short word."
EDITED = "BETA_EDITED: reply with one short word."

USER_ROW = '.message:has(.message-role:text-is("user"))'


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

        # The server persists conversations per dir-set and auto-opens the last one, so a
        # prior smoke's thread can be loaded. Shift+click New conversation → a BLANK panel
        # (no model, no messages) for a deterministic single-user-turn starting point.
        page.locator('button[aria-label="New conversation"]').first.click(modifiers=["Shift"])
        page.wait_for_function(
            "document.querySelectorAll('.message').length === 0", timeout=10000
        )

        # Pick the free model FIRST (composer is disabled until a chat-eligible model
        # is selected), then send the original message and wait for it to fold.
        page.select_option("select.model-slot-select", value=MODEL)
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)
        ta = page.locator(".input-textarea").first
        ta.click()
        ta.fill(ORIG)
        ta.press("Enter")
        page.wait_for_selector('button[aria-label="Regenerate"]', timeout=45000)

        # Sanity: exactly one user row, showing the ORIGINAL text, no sibling cycler yet.
        user_text_before = page.locator(f"{USER_ROW} .message-content").first.inner_text()
        cyclers_before = page.locator(f'{USER_ROW} [data-testid="branch-cycle"]').count()

        # Open the inline editor on the USER turn: hover the row to reveal the toolbar,
        # then click the pencil (force — the toolbar is opacity-hidden until hover).
        page.locator(USER_ROW).first.hover()
        page.locator(f'{USER_ROW} button[aria-label="Edit"]').first.click(force=True)
        editor = page.locator(f"{USER_ROW} textarea.edit-textarea").first
        editor.wait_for(state="visible", timeout=10000)
        editor.fill(EDITED)
        page.locator(f"{USER_ROW} .btn-edit-save").first.click()

        # FORK: the user turn now has a sibling (orig + edit) → ‹k/N› = "2/2".
        page.wait_for_selector(f'{USER_ROW} [data-testid="branch-cycle"]', timeout=15000)
        fork_count = page.locator(
            f'{USER_ROW} [data-testid="branch-cycle"] .branch-cycle-count'
        ).first.inner_text().strip()
        forked_ok = fork_count.endswith("/2")

        # ACTIVE PATH reflects the edit: visible user turn shows EDITED, not ORIG.
        user_text_after = page.locator(f"{USER_ROW} .message-content").first.inner_text()
        active_ok = ("BETA_EDITED" in user_text_after) and ("ALPHA_ORIGINAL" not in user_text_after)

        # REGEN: a fresh assistant reply folds under the edited turn. After the fork the
        # old assistant unmounted (different branch), so the Regenerate button reappearing
        # signals a NEW committed assistant on the active (edited) path.
        page.wait_for_selector('button[aria-label="Regenerate"]', timeout=45000)
        # Wait for the folded assistant turn to carry real content (commit can land a tick
        # before the text flushes into the DOM).
        page.wait_for_function(
            """() => {
                const a = document.querySelector('.message:has(.message-role) .message-content');
                const rows = [...document.querySelectorAll('.message')].filter(
                    m => m.querySelector('.message-role')?.textContent.trim() === 'assistant');
                return rows.some(r => (r.querySelector('.message-content')?.innerText || '').trim().length > 0);
            }""",
            timeout=45000,
        )
        asst_text = ""
        asst = page.locator('.message:has(.message-role:text-is("assistant")) .message-content')
        if asst.count():
            asst_text = asst.first.inner_text().strip()
        regen_ok = page.locator('button[aria-label="Regenerate"]').count() >= 1 and bool(asst_text)

        page.screenshot(path=SHOT, full_page=True)
        browser.close()

        print(f"before edit — user='{user_text_before.strip()[:40]}', cyclers={cyclers_before}")
        print(f"edit forked a sibling user branch (‹{fork_count}›): {forked_ok}")
        print(f"active path shows the EDITED text ('{user_text_after.strip()[:40]}'): {active_ok}")
        print(f"fresh assistant reply folded under the edit ('{asst_text[:40]}'): {regen_ok}")
        print(f"console/page errors: {errors or 'none'}")
        ok = (
            cyclers_before == 0
            and "ALPHA_ORIGINAL" in user_text_before
            and forked_ok
            and active_ok
            and regen_ok
            and not errors
        )
        print("EDIT-FORK SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
