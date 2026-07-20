"""Per-panel bubble ALWAYS continues its panel — regression guard for the bug where
the ⑂ 'branch from start' toggle (a main-composer-only affordance) leaked into the
per-panel '＋ continue this panel' bubble, so typing there started a NEW root thread
instead of extending the panel's conversation.

Seeds a 2-panel conversation with a pre-existing thread in the primary panel, turns
⑂ branch-from-start ON, then sends via the primary panel's bubble. The seeded thread
must stay visible and the send must append under it (no new root sibling) — even with
the toggle on. Only the one bubble send hits the model (free router, zero cost).

  uv run python tests/small-smokes/browser_panel_bubble_continues.py [BASE_URL]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from _seed import _post

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8812"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_panel_bubble_continues.png"

SEED_Q = "SEEDQ marker question"
SEED_A = "SEEDA marker answer"
MODEL = "openrouter:openrouter/free"


def seed_two_panel_thread(base):
    """A 2-panel conversation: 'primary' carries a linear SEED_Q/SEED_A thread,
    'compare' is empty. Returns (conversation_id, primary_panel_id)."""
    nodes = {
        "n0": {"id": "n0", "role": "user", "content": SEED_Q, "parent": None, "children": ["n1"]},
        "n1": {"id": "n1", "role": "assistant", "content": SEED_A, "parent": "n0", "children": []},
    }
    primary_tree = {"nodes": nodes, "rootChildren": ["n0"], "selected": {}}
    empty_tree = {"nodes": {}, "rootChildren": [], "selected": {}}
    cid = _post(base, "/api/conversations", {
        "title": "panel_bubble_continues",
        "panels": [
            {"id": "primary", "run_id": MODEL, "checkpoint": None},
            {"id": "compare", "run_id": MODEL, "checkpoint": None},
        ],
        "trees": {"primary": primary_tree, "compare": empty_tree},
        "reduced_panels": [], "send_targets": ["primary", "compare"],
        "seen_panels": ["primary", "compare"],
    })["id"]
    return cid, "primary"


def chat_text(page) -> str:
    return " ".join(el.inner_text() for el in page.locator(".message-content").all())


def wait_idle(page) -> None:
    page.wait_for_function(
        "() => ![...document.querySelectorAll('textarea,input')].some(e => (e.placeholder||'').includes('generating'))",
        timeout=45000)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        cid, _ = seed_two_panel_thread(BASE)
        page.goto(f"{BASE}/?c={cid}", wait_until="load", timeout=20000)
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)
        page.wait_for_selector(".message-content", timeout=15000)
        assert SEED_Q in chat_text(page), "seeded thread should be visible before sending"

        # Turn ⑂ branch-from-start ON (the state that used to leak into the bubble).
        toggle = page.locator('[data-testid="branch-root-toggle"]')
        toggle.click()
        toggled_on = "branching from start" in toggle.inner_text()

        # Send via the FIRST panel's per-panel bubble (primary panel).
        bubble = page.locator(".panel-send-input").first
        bubble.click()
        bubble.fill("Reply with exactly the word PONG and nothing else.")
        bubble.press("Enter")
        page.wait_for_function(
            "() => [...document.querySelectorAll('.message-content')].some(e => e.innerText.includes('PONG'))",
            timeout=45000)
        wait_idle(page)

        after = chat_text(page)
        # Continued, not branched: seeded turns still present AND new reply appended.
        seed_still_visible = SEED_Q in after and SEED_A in after
        reply_appended = "PONG" in after
        # No new root sibling was created: the first-row branch cycler must NOT read 2/…
        # (a root-level branch would make the seeded first message one of two siblings).
        primary_col = page.locator(".chat-column").first
        cyclers = primary_col.locator('[data-testid="branch-cycle"]')
        no_root_sibling = True
        if cyclers.count() > 0:
            first_count = cyclers.first.locator(".branch-cycle-count").inner_text().strip()
            no_root_sibling = not first_count.startswith("2/") and not first_count.endswith("/2")

        page.screenshot(path=SHOT, full_page=True)
        browser.close()

        print(f"toggle turned on: {toggled_on}")
        print(f"seeded thread still visible after bubble send: {seed_still_visible}")
        print(f"bubble reply appended: {reply_appended}")
        print(f"no new root sibling created: {no_root_sibling}")
        print(f"console/page errors: {errors or 'none'}")
        ok = all([toggled_on, seed_still_visible, reply_appended, no_root_sibling]) and not errors
        print("PANEL-BUBBLE-CONTINUES SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
