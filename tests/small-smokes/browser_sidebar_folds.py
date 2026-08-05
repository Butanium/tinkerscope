"""Sidebar section folds — all four of them, chevron-first. Token-free.

Models / Sampling params / View / Highlights each fold from their heading. Two
properties worth pinning, both of which broke or were missing once:

  - **Highlights folds too.** It was the one section that didn't (it lives in
    its own component, `HighlightRules.svelte`, so it never picked up +page's
    fold pattern), and folding it must keep the master Off/On + `+ new` in the
    header — `+ new` unfolds on its way to the editor, or it opens a draft
    nobody can see.
  - **The chevron leads the title.** It sat at the far right margin, where it
    reads as an unrelated icon button rather than as the heading's own
    disclosure control (Clément, 2026-08-05). Pinned geometrically — the
    chevron's box must start left of the label's — because the CSS that puts it
    there (`justify-content`, source order) is exactly the kind of thing a later
    layout edit undoes silently.

Every fold persists to localStorage, so a reload must restore it.

  uv run --with playwright python tests/small-smokes/browser_sidebar_folds.py [BASE]
"""

import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8791"
SECTIONS = ["Models", "Sampling params", "View", "Highlights"]


def heading(page, name):
    """The fold toggle whose label is `name`. `textContent` skips the <svg>, so
    this keeps working whichever side the chevron is on."""
    return page.locator(".sidebar-section-toggle").filter(has_text=name).first


def expanded(page, name):
    return heading(page, name).get_attribute("aria-expanded") == "true"


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(viewport={"width": 1500, "height": 950}).new_page()
        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_selector("aside.sidebar", timeout=15000)

        # The two properties are checked INDEPENDENTLY and reported together: an
        # eager assert on the first would mask whether the second can fail at all,
        # which is the whole point of a --baseline run.
        problems = []
        found = [s for s in SECTIONS if heading(page, s).count()]
        if found != SECTIONS:
            problems.append(f"missing fold heading(s): {set(SECTIONS) - set(found)}")
        print(f"foldable sections: {', '.join(found) or '(none)'}")

        # ── the chevron leads the title ──────────────────────────────────────
        for name in found:
            h = heading(page, name)
            chev = h.locator(".section-chevron").bounding_box()
            label = h.locator("span").first.bounding_box()
            if not (chev and label):
                problems.append(f"{name}: no chevron or no label box")
                continue
            if chev["x"] + chev["width"] > label["x"] + 1:
                problems.append(
                    f"{name}: chevron at x={chev['x']:.0f}..{chev['x'] + chev['width']:.0f} "
                    f"is not left of the label at x={label['x']:.0f}"
                )
        assert not problems, "; ".join(problems)
        print("chevron leads the title in all four")

        # ── each heading folds and unfolds its own body ──────────────────────
        for name in SECTIONS:
            if not expanded(page, name):
                heading(page, name).click()
                page.wait_for_timeout(120)
            assert expanded(page, name), f"{name} would not open"
            heading(page, name).click()
            page.wait_for_timeout(120)
            assert not expanded(page, name), f"{name} would not fold"
        print("all four fold from their heading")

        # Folded Highlights keeps the two controls that are worth one click.
        hl = page.locator(".hr-header")
        assert hl.locator(".seg-toggle").count() == 1, "folded Highlights dropped the master Off/On"
        assert hl.locator(".hr-new").count() == 1, "folded Highlights dropped '+ new'"
        assert page.locator(".hr-rule").count() == 0, "folded Highlights still lists rules"
        print("folded Highlights keeps Off/On + '+ new'")

        # ── the folds survive a reload ───────────────────────────────────────
        page.reload(wait_until="load")
        page.wait_for_selector("aside.sidebar", timeout=15000)
        page.wait_for_timeout(250)
        still_folded = [s for s in SECTIONS if not expanded(page, s)]
        assert still_folded == SECTIONS, f"folds lost on reload; still folded: {still_folded}"
        print("all four folds survive a reload")

        # ── '+ new' unfolds Highlights rather than editing out of sight ──────
        page.locator(".hr-new").click()
        page.wait_for_timeout(200)
        assert expanded(page, "Highlights"), "'+ new' left Highlights folded"
        assert page.locator(".hr-newrule-banner").count() == 1, "'+ new' opened no draft"
        print("'+ new' unfolds Highlights and opens the draft")

        # Leave the sidebar as we found it — smokes share an instance's state dir.
        page.locator(".hr-editor-actions .hr-cancel").click()
        for name in SECTIONS:
            if not expanded(page, name):
                heading(page, name).click()
                page.wait_for_timeout(80)

        b.close()
    print("OK")


if __name__ == "__main__":
    main()
