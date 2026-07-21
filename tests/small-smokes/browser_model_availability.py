"""Browser smoke for the UNAVAILABLE-model treatment in the model picker.

Verifies the three product surfaces of the availability feature end-to-end
(discovery → capabilities + servable-paths probe → /api/models → UI):

  1. GREY + ⚠, still PICKABLE — a run tinker can't sample right now (base gone
     OR sampler weights gone) renders `.typeahead-row.unavailable`
     (muted) with a ⚠ in its label, but is NOT `[disabled]` (a warning, not a block).
  2. DEMOTED — available runs rank before unavailable ones in the filtered list
     (only asserted when the live data has both classes; the ordering itself is
     exhaustively unit-tested in web/src/lib/fuzzy.test.ts).
  3. WARN-ON-SELECT, NO BLOCK — selecting an unavailable run shows the sidebar
     `.unavailable-warn` banner AND leaves the composer textarea enabled.
  4. SEND SURFACES THE 404 — sending to a dead run (base served, weights
     gone) renders the backend error VISIBLY as an assistant bubble
     ("Error: sampler weights no longer exist…"), not a silent no-op. This is the
     check that justifies keeping the send allowed instead of hard-gated. Makes a
     real (fast-failing, token-free) tinker call, so needs TINKER_API_KEY set.

DATA-DRIVEN (not seeded): availability is inherently live state, so the smoke
reads /api/models and asserts against whatever the probe returns. The
negation_neglect runs are on `Qwen/Qwen3-30B-A3B-Base` (a base tinker no longer
serves) → a deterministic supply of UNAVAILABLE runs regardless of which sampler
weights still exist. Point it at an isolated instance scanning BOTH real roots:

  scripts/dev-isolated.sh --port 8812 \\
      ~/projects2/negation_neglect/datasets/training_datasets/ ~/projects2/weird-personas/

  uv run python tests/small-smokes/browser_model_availability.py [BASE_URL] [SCREENSHOT_PATH]
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = args[0] if len(args) > 0 else "http://127.0.0.1:8812"
SHOT = args[1] if len(args) > 1 else None
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))


def search_token(run: dict) -> str:
    """A distinctive filter token from a run's name/id (longest alnum word)."""
    words = re.findall(r"[A-Za-z0-9]{4,}", f"{run.get('name','')} {run.get('id','')}")
    words.sort(key=len, reverse=True)
    return words[0] if words else run["id"][:8]


def mixed_token(avail: list, unavail: list) -> str | None:
    """A base-model token that matches BOTH an available and an unavailable run
    (the typeahead search field includes base_model), so a single filter yields a
    mixed list to check demotion order on. e.g. 'deepseek' when a served base has
    both live and weights-gone runs. None if no base spans both classes."""
    a_bases = {r.get("base_model") for r in avail}
    u_bases = {r.get("base_model") for r in unavail}
    for bm in a_bases & u_bases:
        if bm:
            toks = re.findall(r"[A-Za-z0-9]{4,}", bm.lower())
            if toks:
                return max(toks, key=len)
    return None


def main():
    runs = json.load(urllib.request.urlopen(f"{BASE}/api/models", timeout=20))
    health = json.load(urllib.request.urlopen(f"{BASE}/api/health", timeout=20))
    sup = set(health["supported_models"]) | {m.split(":peft")[0] for m in health["supported_models"]}
    unavail = [r for r in runs if r.get("sampleable") is False]
    avail = [r for r in runs if r.get("sampleable") is True]
    print(f"/api/models: {len(runs)} runs — {len(avail)} available, {len(unavail)} unavailable")
    assert unavail, (
        "expected >=1 unavailable run (the negation_neglect Qwen3-30B-A3B-Base runs "
        "are base-gone) — is the instance scanning that root?"
    )
    # Prefer a WEIGHTS-GONE run for the whole flow: base still served, weights
    # gone — the case the base-only check missed AND the one whose send produces a
    # meaningful weights-404 (not a base-load failure).
    false_green = [r for r in unavail if r.get("base_model") in sup and r.get("checkpoints")]
    target = false_green[0] if false_green else unavail[0]
    token = search_token(target)
    print(f"target unavailable run: {target['id']!r} (reason: {target.get('unsampleable_reason')!r}); filter token {token!r}")
    print(f"weights-gone-with-served-base run present: {bool(false_green)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="load", timeout=20000)

        page.wait_for_selector(".model-dropdown-trigger", timeout=15000)
        page.locator(".model-dropdown-trigger").first.click()
        page.wait_for_selector(".typeahead-input", timeout=5000)
        page.locator(".typeahead-input").first.fill(token)
        page.wait_for_function(
            "document.querySelectorAll('.typeahead-row').length >= 1", timeout=8000)

        # ── 1. the unavailable row: greyed + ⚠, but NOT disabled (pickable) ──
        probe = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('.typeahead-row')];
          const un = rows.find((r) => r.classList.contains('unavailable'));
          if (!un) return { found: false, n: rows.length };
          const lab = un.querySelector('.typeahead-row-label');
          return {
            found: true,
            disabled: un.hasAttribute('disabled'),
            warnGlyph: (lab?.textContent ?? '').includes('⚠'),
            muted: getComputedStyle(lab).color,
            title: un.getAttribute('title') ?? '',
          };
        }""")
        assert probe["found"], f"no .typeahead-row.unavailable after filtering {token!r} (rows={probe.get('n')})"
        assert not probe["disabled"], "unavailable row must stay PICKABLE (no [disabled]) — warning, not a block"
        assert probe["warnGlyph"], "unavailable row label must carry the ⚠ warning glyph"
        assert any(s in probe["title"] for s in ("no longer exist", "serve sampling", "sampleable")), \
            f"expected a warning title naming the constraint, got {probe['title']!r}"
        print(f"row OK: greyed+pickable, ⚠ present, color={probe['muted']}")

        # ── 2. demotion: filter by a base spanning both classes → available first ──
        mtok = mixed_token(avail, unavail)
        if mtok:
            page.locator(".typeahead-input").first.fill(mtok)
            page.wait_for_function(
                "document.querySelectorAll('.typeahead-row').length >= 2", timeout=8000)
            order = page.evaluate("""() => [...document.querySelectorAll('.typeahead-row')]
              .map((r) => r.classList.contains('unavailable'))""")
            first_unavail = next((i for i, u in enumerate(order) if u), None)
            last_avail = next((i for i in range(len(order) - 1, -1, -1) if not order[i]), None)
            if first_unavail is not None and last_avail is not None:
                assert last_avail < first_unavail, \
                    f"available rows must precede unavailable ones (filter {mtok!r}: avail@{last_avail} !< unavail@{first_unavail})"
                print(f"demotion OK: filter {mtok!r} → available rows all before unavailable (avail<{first_unavail})")
            else:
                print(f"demotion: filter {mtok!r} wasn't mixed in the visible rows — skipped (unit-tested)")
            page.locator(".typeahead-input").first.fill(token)  # restore for step 3
            page.wait_for_function(
                "document.querySelectorAll('.typeahead-row.unavailable').length >= 1", timeout=8000)
        else:
            print("demotion: no base model spans both classes in live data — order check skipped (unit-tested)")

        # ── 3. select it → sidebar warns, composer NOT blocked ──
        page.locator(".typeahead-row.unavailable").first.click()
        page.wait_for_timeout(300)
        state = page.evaluate("""() => {
          const warn = document.querySelector('.unavailable-warn');
          const ta = document.querySelector('textarea');
          return {
            warnText: (warn?.textContent ?? '').trim(),
            composerDisabled: ta ? ta.disabled : null,
          };
        }""")
        assert state["warnText"] and "⚠" in state["warnText"], \
            f"selecting an unavailable run must show the ⚠ sidebar warning, got {state['warnText']!r}"
        assert state["composerDisabled"] is False, \
            f"composer must stay ENABLED for an unavailable pick (warning, not block), got disabled={state['composerDisabled']}"
        print(f"select OK: warn={state['warnText'][:60]!r}…, composer enabled")

        # ── 4. SEND to the (weights-gone) run → the 404 renders as an error bubble ──
        # This is what justifies keeping the send allowed: the failure is VISIBLE.
        ta = page.locator(".input-textarea").first
        ta.wait_for(state="visible", timeout=5000)
        assert not ta.is_disabled(), "composer must be sendable for the unavailable pick"
        ta.fill("test")
        ta.press("Enter")
        # The 404 fires fast at create_sampling_client → an assistant "Error: …" bubble.
        page.wait_for_function(
            """() => [...document.querySelectorAll('.message')].some(
                 (m) => /Error:/.test(m.querySelector('.message-content')?.textContent || ''))""",
            timeout=30000)
        errtext = page.evaluate("""() => {
          const m = [...document.querySelectorAll('.message')].find(
            (x) => /Error:/.test(x.querySelector('.message-content')?.textContent || ''));
          return m?.querySelector('.message-content')?.textContent?.trim() ?? null;
        }""")
        assert errtext and errtext.startswith("Error:"), \
            f"a send to an unavailable run must render a visible error bubble, got {errtext!r}"
        if false_green:
            assert "no longer exist" in errtext, \
                f"weights-gone send should surface the weights-gone reason, got {errtext!r}"
        print(f"send OK: error surfaced visibly → {errtext[:70]!r}")

        if SHOT:
            Path(SHOT).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=SHOT)
            print(f"screenshot -> {SHOT}")

        assert not errors, f"console errors: {errors}"
        browser.close()

    print("browser_model_availability: OK")


if __name__ == "__main__":
    main()
