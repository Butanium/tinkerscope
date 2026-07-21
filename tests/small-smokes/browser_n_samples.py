"""n_samples smoke — Samples(n)=4 in the sidebar, send a message, assert 4 sample
cards render in the n>1 distribution view, each a selectable branch that folds into
the tree (the 4 become ‹k/N› siblings once one is made active). Exercises the
n_samples → request → multi-sample fold/render path. Zero cost (free OpenRouter
model), no tinker servable-window dependency.

  uv run python tests/small-smokes/browser_n_samples.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from _seed import seed_conversation

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8820"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
MODEL = "openrouter:openrouter/free"  # free ROUTER (saved OR list) — survives single-provider outages
N = 4
SHOT = "/tmp/tinkerscope_n_samples.png"


def backend_n_samples() -> int | None:
    """Read n_samples off the shared server state (confirms the sidebar input's
    debounced POST round-tripped before we send — the send path reads live.state)."""
    with urllib.request.urlopen(f"{BASE}/api/state", timeout=5) as r:
        return json.load(r).get("n_samples")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Seed a fresh conversation with one free-router panel and open it directly —
        # replaces the old native-<select> model picker (now the ModelDropdown combobox).
        # Also avoids inheriting a prior run's on-disk branch state.
        cid, _ = seed_conversation(BASE, [MODEL], "n_samples")
        page.goto(f"{BASE}/?c={cid}", wait_until="load", timeout=20000)
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)

        # Force the 'All' sample view so every card renders stacked (not one-at-a-time).
        page.locator(".seg-btn", has_text="All").first.click()

        # Set Samples(n)=4. The Samples input is the sidebar number input bounded
        # [1,200] (Max tokens is [1,32000]; top_k [-1,200] lives in the closed popup).
        n_input = page.locator('input.sidebar-input[min="1"][max="200"]').first
        n_input.fill(str(N))
        # The change is a debounced POST → SSE round-trip; the send path reads the
        # mirrored state, so wait until the server actually holds n_samples=4.
        deadline = time.time() + 8
        while time.time() < deadline and backend_n_samples() != N:
            time.sleep(0.1)
        n_confirmed = backend_n_samples()

        # Send.
        ta = page.locator(".input-textarea").first
        ta.click()
        ta.fill("Reply with exactly the word PONG and nothing else.")
        ta.press("Enter")

        # The n>1 distribution renders one .sample-card per completed sample; reaching
        # N cards means all N samples landed (N is the ceiling). Wait for the fold to
        # finish too — the per-card "Make active" button is disabled until the bucket
        # is committed into the tree (sampleNodeIds set).
        page.wait_for_function(
            "document.querySelectorAll('.sample-card').length === %d" % N, timeout=90000
        )
        page.wait_for_selector(
            'button[data-tooltip^="Make this the active branch"]:not([disabled])',
            timeout=30000,
        )

        card_count = page.locator(".sample-card").count()
        cards_ok = card_count == N
        # Each card is a selectable branch: it carries a "Make active" button.
        selectable = page.locator(
            '.sample-card button[data-tooltip^="Make this the active branch"]'
        ).count()
        selectable_ok = selectable == N

        page.screenshot(path=SHOT, full_page=True)

        # Card action row (OverflowRow): at this wide viewport every card action is
        # a plain inline button — discard-others + the card's own node id (the CLI's
        # `--node` handle) included, with no fold toggle in sight. ERROR samples
        # (the free router flakes sometimes) render a card but never fold into the
        # tree, so they carry no node id — expect copy-id only on FOLDED cards,
        # i.e. those whose make-active button is enabled.
        n_discard = page.locator('.sample-card button[aria-label="Discard others"]').count()
        n_folded = page.locator(
            '.sample-card button[data-tooltip^="Make this the active branch"]:not([disabled])'
        ).count()
        n_copyid = page.locator(".sample-card [data-testid=copy-node-id]").count()
        # The toggle element always exists (reserved slot); count only SHOWN ones.
        n_toggle = page.evaluate(
            """[...document.querySelectorAll('.sample-card [data-testid=acts-toggle]')]
               .filter((t) => getComputedStyle(t).visibility !== 'hidden').length"""
        )
        card_menu_ok = n_discard == N and n_copyid == n_folded > 0 and n_toggle == 0

        # Make a card the active branch → the distribution collapses to a single
        # reply and the other folded samples become ‹k/n_folded› siblings: a strong
        # check that the multi-sample fold created real tree nodes. Click an ENABLED
        # make-active (an error card's is disabled — force-clicking it would no-op
        # and the collapse would never come) and expect /n_folded, not /N: error
        # samples never fold, so a router flake shrinks the sibling fan-out.
        page.locator(
            '.sample-card button[data-tooltip^="Make this the active branch"]:not([disabled])'
        ).first.click(force=True)
        page.wait_for_selector('[data-testid="branch-cycle"]', timeout=15000)
        # A fresh conversation has exactly one cycler (the folded assistant siblings),
        # but read the one reporting /n_folded rather than blindly taking .first, so a
        # stray cycler (e.g. a future user-edit branch) can't shadow the real assertion.
        counts = [c.strip() for c in page.locator('[data-testid="branch-cycle"] .branch-cycle-count').all_inner_texts()]
        cycle_text = next((c for c in counts if c.endswith("/%d" % n_folded)), counts[0] if counts else "")
        folded_ok = cycle_text.endswith("/%d" % n_folded)
        collapsed_ok = page.locator(".sample-card").count() == 0

        browser.close()

        print(f"backend n_samples confirmed: {n_confirmed} (want {N})")
        print(f"sample cards rendered: {card_count} (want {N}) -> {cards_ok}")
        print(f"selectable 'Make active' buttons: {selectable} (want {N}) -> {selectable_ok}")
        print(f"card actions (discard {n_discard}/{N}, copy-id {n_copyid}/{n_folded} folded, toggles {n_toggle}/0): {card_menu_ok}")
        print(f"collapsed to single reply after select: {collapsed_ok}")
        print(f"fold made {n_folded} siblings (cycler {cycle_text!r}): {folded_ok}")
        print(f"console/page errors: {errors or 'none'}")
        ok = (
            n_confirmed == N
            and cards_ok
            and selectable_ok
            and card_menu_ok
            and collapsed_ok
            and folded_ok
            and not errors
        )
        print("N_SAMPLES SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
