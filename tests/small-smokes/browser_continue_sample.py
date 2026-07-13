"""Per-sample Continue + truncation-badge smoke — Samples(n)=2 + a LOW max_tokens
(so samples come back truncated mid-story and a continue genuinely extends), send,
then hit the Continue button ON ONE SPECIFIC sample card (not the turn-level
continue). The forced 'length' finish also exercises the truncated badge: every
n>1 card and the committed collapsed turn must carry the amber pill.

Asserts via the conversation tree (the real contract) that the continuations
landed as siblings carrying the CLICKED sample's text as their prefill — i.e.
the continue targeted THAT sample, not the active branch — then selects a
continuation in the UI and asserts the committed message renders its
prefill-tinted prefix (.prefill-portion) and a ‹k/4› cycler (2 originals + 2
continuations).

Why not assert the tint on the live cards: for the OpenRouter path the bucket
streams the CONTINUATION ONLY (the prefill is prepended at fold time), so the
live cards never show the tint — only the committed node does.

  uv run python tests/small-smokes/browser_continue_sample.py [BASE_URL] [MODEL]

MODEL defaults to `openrouter/free` — OpenRouter's free ROUTER, which picks any
currently-up free model (survives single-provider outages, unlike a pinned :free
model). The app sends reasoning effort:none (Thinking off), so routed thinking
models shouldn't burn the 40-token budget on CoT; if the router still lands on a
model that ignores that and the smoke flakes, pin a saved model instead, e.g.
`openrouter:deepseek/deepseek-chat-v3.1` (still sub-cent for these tiny sends).
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from _seed import seed_conversation

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8820"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
MODEL = sys.argv[2] if len(sys.argv) > 2 else "openrouter:openrouter/free"  # free ROUTER (saved OR list)
N = 2
MAX_TOKENS = 40  # low on purpose: truncated samples make the continuation non-empty
SHOT = "/tmp/tinkerscope_continue_sample.png"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def backend_state() -> dict:
    with urllib.request.urlopen(f"{BASE}/api/state", timeout=5) as r:
        return json.load(r)


def assistant_children(conv_id: str) -> list[dict]:
    """The assistant siblings under the (single) user node of a conversation."""
    with urllib.request.urlopen(f"{BASE}/api/conversations", timeout=5) as r:
        convs = json.load(r)
    conv = next(c for c in convs if c["id"] == conv_id)
    nodes = conv["trees"]["primary"]["nodes"]
    return [n for n in nodes.values() if n["role"] == "assistant"]


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Seed a fresh single-panel conversation on the model and open it — replaces
        # the old native-<select> model picker (now the ModelDropdown combobox).
        conv_id, _ = seed_conversation(BASE, [MODEL], "continue_sample")
        page.goto(f"{BASE}/?c={conv_id}", wait_until="load", timeout=20000)
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)

        # 'All' sample view so both cards render stacked.
        page.locator(".seg-btn", has_text="All").first.click()

        # Samples(n)=2 + Max tokens=40, confirmed via the shared server state
        # (debounced POSTs; the send path reads the mirrored state).
        page.locator('input.sidebar-input[min="1"][max="200"]').first.fill(str(N))
        page.locator('input.sidebar-input[min="1"][max="32000"]').first.fill(str(MAX_TOKENS))
        deadline = time.time() + 8
        while time.time() < deadline:
            s = backend_state()
            if s.get("n_samples") == N and s.get("max_tokens") == MAX_TOKENS:
                break
            time.sleep(0.1)
        s = backend_state()
        params_ok = s.get("n_samples") == N and s.get("max_tokens") == MAX_TOKENS

        ta = page.locator(".input-textarea").first
        ta.click()
        ta.fill("Tell me a story about a fox. Start immediately with the story.")
        ta.press("Enter")

        # Wait for both cards + the fold (per-card actions enable once committed).
        page.wait_for_function(
            "document.querySelectorAll('.sample-card').length === %d" % N, timeout=90000
        )
        page.wait_for_selector(
            '.sample-card button[aria-label="Continue this sample"]:not([disabled])',
            timeout=30000,
        )

        # Truncation badges: max_tokens=40 guarantees 'length' finish reasons, so
        # every card must carry the amber "truncated" pill (the badge feature).
        trunc_badges = page.locator(".sample-card .truncated-tag").count()
        badges_ok = trunc_badges == N

        # The target: the SECOND card (index 1) — deliberately not the active one
        # (the fold selects a default branch; clicking card 2's continue must target
        # card 2 regardless). Cards map to the user node's children in fold order,
        # so kids0[1] is the clicked sample's NODE — its raw content anchors the
        # tree assertions (raw-vs-raw; the rendered card text would drop markdown).
        # (Persistence to the server is debounced — poll until the fold lands.)
        deadline = time.time() + 15
        kids0 = assistant_children(conv_id)
        while time.time() < deadline and len(kids0) != N:
            time.sleep(0.5)
            kids0 = assistant_children(conv_id)
        assert len(kids0) == N, f"expected {N} folded samples, got {len(kids0)}"
        clicked_raw = kids0[1]["content"]
        target_card = page.locator(".sample-card").nth(1)
        target_text = norm(target_card.locator(".sample-content").inner_text())
        target_card.locator('button[aria-label="Continue this sample"]').click(force=True)

        # Wait for the continue fold: the user node grows to 2N assistant siblings.
        deadline = time.time() + 90
        kids: list[dict] = []
        while time.time() < deadline:
            kids = assistant_children(conv_id)
            if len(kids) == 2 * N:
                break
            time.sleep(0.5)
        siblings_ok = len(kids) == 2 * N

        # The tree contract: exactly N nodes carry the CLICKED sample's raw text as
        # their prefill, and their content starts with it (prefill + continuation).
        continued = [n for n in kids if n.get("prefill")]
        prefill_ok = len(continued) == N and all(
            n["prefill"] == clicked_raw and n["content"].startswith(clicked_raw)
            for n in continued
        )
        # Truncation made the continuations non-empty (content extends the prefill).
        extended_ok = all(len(norm(n["content"])) > len(norm(n["prefill"]))for n in continued)

        # UI: make a continuation card active → collapses to a committed message
        # whose prefill-tinted prefix is the clicked sample's text, with a ‹k/2N›
        # sibling cycler.
        page.wait_for_selector(
            '.sample-card button[data-tooltip^="Make this the active branch"]:not([disabled])',
            timeout=30000,
        )
        page.locator(
            '.sample-card button[data-tooltip^="Make this the active branch"]'
        ).first.click(force=True)
        page.wait_for_selector(".message-content .prefill-portion", timeout=15000)
        tint_text = norm(" ".join(page.locator(".message-content .prefill-portion").all_inner_texts()))
        tint_ok = tint_text == target_text
        # The continuation was ALSO cut at 40 tokens → the committed (collapsed)
        # message must carry the truncated badge too (persisted on the tree node).
        committed_badge_ok = page.locator(".message-head-left .truncated-tag").count() == 1
        counts = [c.strip() for c in page.locator('[data-testid="branch-cycle"] .branch-cycle-count').all_inner_texts()]
        want_total = 2 * N
        cycle_text = next((c for c in counts if c.endswith("/%d" % want_total)), counts[0] if counts else "")
        folded_ok = cycle_text.endswith("/%d" % want_total)

        page.screenshot(path=SHOT, full_page=True)
        browser.close()

        print(f"backend params confirmed (n={N}, max_tokens={MAX_TOKENS}): {params_ok}")
        print(f"clicked sample text: {target_text[:80]!r}...")
        print(f"assistant siblings after continue: {len(kids)} (want {2 * N}) -> {siblings_ok}")
        print(f"{N} continuations carry the clicked sample as prefill + prefix: {prefill_ok}")
        print(f"continuations actually extend the prefill (non-empty): {extended_ok}")
        print(f"truncated badges on the n>1 cards: {trunc_badges} (want {N}) -> {badges_ok}")
        print(f"committed tint == clicked sample's text after select: {tint_ok}")
        print(f"truncated badge on the committed collapsed turn: {committed_badge_ok}")
        print(f"cycler shows /{want_total} siblings ({cycle_text!r}): {folded_ok}")
        print(f"console/page errors: {errors or 'none'}")
        ok = (
            params_ok and siblings_ok and prefill_ok and extended_ok
            and badges_ok and tint_ok and committed_badge_ok and folded_ok and not errors
        )
        print("CONTINUE_SAMPLE SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
