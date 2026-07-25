"""Smoke: the `?` help modal opens, both tabs render, and it closes.

Token-free — no model calls, no workspace seeding. Just proves the button is
reachable, the Guide/Keys tabs each render their content, Escape closes, and
(the reason this smoke exists) that opening it does NOT let the keyboard
row-navigation handler steal keys: `anyModalOpen()` is DOM-based, so a new
modal is only covered if it actually renders `.modal-overlay`.

    uv run --with playwright python tests/small-smokes/browser_help_modal.py [BASE]
"""

import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8791"


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(viewport={"width": 1500, "height": 950}).new_page()
        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_selector("aside.sidebar", timeout=15000)

        assert page.locator(".modal-overlay").count() == 0, "a modal is open before we clicked anything"

        page.locator("button[aria-label='Help']").click()
        page.wait_for_selector(".modal-overlay", timeout=5000)
        title = page.locator(".modal-header h2").inner_text()
        assert "tinkerscope" in title, f"unexpected modal title {title!r}"

        body = page.locator(".modal-body").inner_text()
        for probe in ("Workspaces and panels", "sibling branch", "Distribution chart"):
            assert probe in body, f"Guide tab missing {probe!r}"

        # Every button the modal NAMES is drawn with its real glyph (lib/Icon.svelte).
        # Text probes alone would pass with every chip rendering blank — a name the
        # dispatch chain doesn't know emits nothing, and `npm run build` doesn't
        # typecheck, so only this assertion stands between a typo and a modal full
        # of empty boxes.
        chips = page.locator(".help-chip").count()
        glyphs = page.locator(".help-chip svg").count()
        legend_rows = page.locator(".help-btns li").count()
        assert legend_rows >= 15, f"expected the three button legends, got {legend_rows} rows"
        assert chips >= legend_rows, f"{chips} chips for {legend_rows} legend rows"
        assert glyphs == chips, f"{chips - glyphs} chip(s) rendered no glyph"
        print(f"guide tab OK ({len(body)} chars, {glyphs} button glyphs)")

        # Keys tab: the shortcut table, incl. the two undiscoverable axes.
        page.locator("button.help-tab", has_text="Keys").click()
        page.wait_for_selector(".help-keys", timeout=5000)
        # NB: inner_text() applies text-transform, so the group headers come back
        # UPPERCASED — compare case-insensitively.
        keys = page.locator(".modal-body").inner_text().lower()
        for probe in ("composer", "ctrl / ⌘", "all panels", "‹k/n›"):
            assert probe in keys, f"Keys tab missing {probe!r}"
        rows = page.locator(".help-keys tr").count()
        assert rows >= 15, f"expected a populated shortcut table, got {rows} rows"
        # The modifier rows show WHICH button they apply to, drawn, not just named.
        key_glyphs = page.locator(".help-keys .help-chip svg").count()
        assert key_glyphs >= 6, f"expected drawn buttons next to the modifiers, got {key_glyphs}"
        print(f"keys tab OK ({rows} rows, {key_glyphs} button glyphs)")

        # Escape closes (Modal chrome), and the row-nav handler must have stayed
        # out of the way while it was open.
        page.keyboard.press("Escape")
        page.wait_for_selector(".modal-overlay", state="detached", timeout=5000)
        assert page.locator(".modal-body").count() == 0

        # Re-open + click-outside close, the other Modal affordance.
        page.locator("button[aria-label='Help']").click()
        page.wait_for_selector(".modal-overlay", timeout=5000)
        page.locator(".modal-overlay").click(position={"x": 5, "y": 5})
        page.wait_for_selector(".modal-overlay", state="detached", timeout=5000)

        print("HELP MODAL OK")
        b.close()


main()
