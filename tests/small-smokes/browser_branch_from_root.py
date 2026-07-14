"""Branch-from-start composer toggle smoke — while the '⑂ branch from start' chip
is ON, a send creates a NEW root-level branch (a sibling first message) instead of
appending to the active thread; toggling it OFF restores plain append. Seeds a
conversation with an existing thread, so only the two toggled sends hit the model
(free router, zero cost).

  uv run python tests/small-smokes/browser_branch_from_root.py [BASE_URL]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from _seed import seed_thread

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8812"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_branch_from_root.png"

SEED_Q = "SEEDQ marker question"
SEED_A = "SEEDA marker answer"


def chat_text(page) -> str:
    return " ".join(el.inner_text() for el in page.locator(".message-content").all())


def wait_idle(page) -> None:
    page.wait_for_function(
        "() => ![...document.querySelectorAll('textarea,input')].some(e => (e.placeholder||'').includes('generating'))",
        timeout=45000)


def send(page, text: str) -> None:
    ta = page.locator(".input-textarea").first
    ta.click()
    ta.fill(text)
    ta.press("Enter")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        cid = seed_thread(
            BASE,
            [{"role": "user", "content": SEED_Q}, {"role": "assistant", "content": SEED_A}],
            title="branch_from_root",
        )
        page.goto(f"{BASE}/?c={cid}", wait_until="load", timeout=20000)
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)
        page.wait_for_selector(".message-content", timeout=15000)
        assert SEED_Q in chat_text(page), "seeded thread should be visible before branching"

        # Toggle branch-from-start ON and send → a sibling FIRST message.
        toggle = page.locator('[data-testid="branch-root-toggle"]')
        toggle.click()
        toggled_on = "branching from start" in toggle.inner_text()
        send(page, "Reply with exactly the word PONG and nothing else.")
        page.wait_for_selector('button[aria-label="Regenerate"]', timeout=45000)
        wait_idle(page)

        after_branch = chat_text(page)
        branch_hides_old = SEED_Q not in after_branch and "PONG" in after_branch
        cycler = page.locator('[data-testid="branch-cycle"]').first
        count_text = cycler.locator(".branch-cycle-count").inner_text().strip()
        branch_count_ok = count_text == "2/2"  # two sibling first messages

        # Cycle back to the original first message → the old thread restores.
        cycler.locator('button[aria-label="Previous branch"]').click()
        page.wait_for_timeout(300)
        back = chat_text(page)
        cycle_restores_old = SEED_Q in back and SEED_A in back and "PONG" not in back

        # Toggle OFF and send → plain append onto the restored thread.
        toggle.click()
        toggled_off = "branching from start" not in toggle.inner_text()
        send(page, "Reply with exactly the word DING and nothing else.")
        page.wait_for_function(
            "() => [...document.querySelectorAll('.message-content')].some(e => e.innerText.includes('DING'))",
            timeout=45000)
        wait_idle(page)
        after_append = chat_text(page)
        append_extends = SEED_Q in after_append and "DING" in after_append

        page.screenshot(path=SHOT, full_page=True)
        browser.close()

        print(f"toggle turned on: {toggled_on}")
        print(f"branched send shows ONLY the new thread: {branch_hides_old}")
        print(f"first-row cycler reads 2/2 (got '{count_text}'): {branch_count_ok}")
        print(f"cycling back restores the seeded thread: {cycle_restores_old}")
        print(f"toggle turned off: {toggled_off}")
        print(f"append send extends the current thread: {append_extends}")
        print(f"console/page errors: {errors or 'none'}")
        ok = all([toggled_on, branch_hides_old, branch_count_ok, cycle_restores_old,
                  toggled_off, append_extends]) and not errors
        print("BRANCH-FROM-ROOT SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
