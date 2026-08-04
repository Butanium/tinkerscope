"""Token-logprob feature smoke — fully deterministic (no sampling).

Seeds a workspace whose assistant siblings carry `token_logprobs` (the shape
the native tinker path emits — see docs/API_CONTRACT.md), then drives both
display surfaces:

  token-view toggle:
  - sidebar "Token probs" Tokens → the active assistant turn renders as raw token
    spans (.tok), surprising tokens visibly tinted
  - hovering a token opens the popover with its probability + the top-K
    alternatives as bars (the sampled alternative highlighted)
  - a turn WITHOUT logprobs shows the "no token data" pill instead
  - Off → back to the normal markdown render

  "Color by match" (the Off/On toggle under Token probs):
  - hidden until a highlight rule exists; defaults Off
  - On adopts the first enabled rule and re-tints tokens by match probability
    (the rule's hue) instead of surprisal
  - the Contrast slider warps prob → opacity (0 linear · 0.5 √ · 1 step)
  - Off restores the surprisal tint but KEEPS the picked rule

  first-token chart mode:
  - the "first token" mode button is enabled (data present) and produces
    model-probability bars: legend = tokens + the grey rest-of-distribution
  - clicking a sampled token's segment opens the inspector with those samples

The capture path itself (real sampling → token_logprobs on the SSE) is covered
by tests/test_token_logprobs.py + a live probe; this smoke pins the UI.

  uv run python tests/small-smokes/browser_token_logprobs.py [BASE_URL]
"""
import json
import math
import re
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT_TOKENS = "/tmp/tinkerscope_token_logprobs.png"
SHOT_CHART = "/tmp/tinkerscope_chart_firsttoken.png"


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


LN = math.log
# Shared reference top-3 at position 0 (all siblings share the prompt).
TOP0 = [["Blue", 11, LN(0.6)], ["Gray", 12, LN(0.25)], ["The", 13, LN(0.1)]]


def tlp(entries):
    return [
        {"t": t, "tid": tid, "lp": lp, **({"top": top} if top else {})}
        for t, tid, lp, top in entries
    ]


RULE_ID = "smoke-tlp-blue"


def seed() -> str:
    """One turn with 3 logprob-carrying siblings + a follow-up turn without.

    Also installs one highlight rule: "Color by match" only renders when at
    least one enabled rule exists, and position 0's top-3 (Blue/Gray/The) is
    exactly the distribution the match tint reads.
    """
    api("PUT", f"/api/highlights/{RULE_ID}", {
        "id": RULE_ID, "name": "blue", "enabled": True, "patterns": ["Blue"],
        "combinator": "or", "is_regex": False, "case_sensitive": False,
        "color": "#3b82f6", "scope_role": None, "sort_order": 100,
    })
    # Clear the shared panel echo first: a stale transcript from a previous chat
    # would otherwise be grafted into the freshly-opened workspace by the
    # external-fold reconcile and shunt the seeded branch to a sibling.
    api("POST", "/api/state", {"panel_messages": {"primary": []}})
    nodes = {
        "u1": {"id": "u1", "role": "user", "content": "What color is the sky?",
               "parent": None, "children": ["a0", "a1", "a2"]},
        # active sibling: 'Blue.' — '.' is deliberately SURPRISING (p=.05) so the
        # heat tint is visibly set on it
        "a0": {"id": "a0", "role": "assistant", "content": "Blue.", "parent": "u1",
               "children": ["u2"],
               "token_logprobs": tlp([
                   ("Blue", 11, LN(0.6), TOP0),
                   (".", 20, LN(0.05), [[".", 20, LN(0.05)], ["!", 21, LN(0.7)]]),
               ])},
        "a1": {"id": "a1", "role": "assistant", "content": "Blue!", "parent": "u1",
               "children": [],
               "token_logprobs": tlp([
                   ("Blue", 11, LN(0.6), TOP0),
                   ("!", 21, LN(0.7), [["!", 21, LN(0.7)], [".", 20, LN(0.05)]]),
               ])},
        "a2": {"id": "a2", "role": "assistant", "content": "Gray.", "parent": "u1",
               "children": [],
               "token_logprobs": tlp([
                   ("Gray", 12, LN(0.25), TOP0),
                   (".", 20, LN(0.9), [[".", 20, LN(0.9)]]),
               ])},
        # follow-up turn WITHOUT token data (e.g. an OpenRouter regen)
        "u2": {"id": "u2", "role": "user", "content": "And at night?",
               "parent": "a0", "children": ["b0"]},
        "b0": {"id": "b0", "role": "assistant", "content": "Dark, mostly.",
               "parent": "u2", "children": []},
    }
    conv = api("POST", "/api/workspaces", {
        "name": "token-logprobs-smoke",
        "trees": {"primary": {"nodes": nodes, "rootChildren": ["u1"],
                              "selected": {"__root__": "u1", "u1": "a0",
                                           "a0": "u2", "u2": "b0"}}},
    })
    return conv["id"]


def main() -> None:
    conv_id = seed()
    checks: list[tuple[str, bool]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(f"{BASE}/?w={conv_id}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-select", timeout=15000)
            page.wait_for_function(
                "document.body.innerText.includes('What color is the sky?')", timeout=15000
            )

            # ── token-view toggle ────────────────────────────────────────
            checks.append(("no .tok spans before toggle", page.query_selector(".tok") is None))
            page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("Tokens")')
            page.wait_for_selector(".tok", timeout=5000)
            toks = page.query_selector_all(".tok-stream >> nth=0 >> .tok")
            checks.append(("turn 1 renders 2 token spans", len(toks) == 2))
            checks.append(("token text preserved",
                           "".join(t.inner_text() for t in toks) == "Blue."))
            def alpha(el) -> float:
                style = el.get_attribute("style") or ""
                return float(style.rsplit(",", 1)[-1].strip(" );")) if "rgba" in style else 0.0

            checks.append(("surprising token tinted", alpha(toks[1]) > 0.15))
            checks.append(("tint ∝ surprisal (p=.05 ≫ p=.6)", alpha(toks[1]) > alpha(toks[0])))

            # hover → popover with prob + alternatives
            toks[0].hover()
            page.wait_for_selector(".tok-pop", timeout=3000)
            pop = page.inner_text(".tok-pop")
            checks.append(("popover: sampled token + prob", "Blue" in pop and "60%" in pop))
            checks.append(("popover: alternatives listed", "Gray" in pop and "25%" in pop))
            alts = page.query_selector_all(".tok-alt")
            checks.append(("popover: top-3 bars", len(alts) == 3))
            checks.append(("popover: sampled alternative highlighted",
                           page.query_selector(".tok-alt-sampled") is not None))
            page.screenshot(path=SHOT_TOKENS)

            # the data-less follow-up turn wears the pill
            checks.append(("no-token-data pill on the plain turn",
                           page.query_selector('.mode-tag:has-text("no token data")') is not None))
            # thinking fold: none of these carry reasoning, nothing to assert here.

            # ── "Color by match" toggle ──────────────────────────────────
            # A pre-toggle install had no on-flag, so the store infers ON from a
            # stored selection; this smoke's browser is fresh → Off with no picks.
            match_row = '.lp-hl .thinking-toggle-row:has-text("Color by match")'
            page.wait_for_selector(match_row, timeout=3000)
            checks.append(("match toggle defaults Off",
                           page.query_selector(f'{match_row} .seg-btn.active').inner_text() == "Off"))
            checks.append(("no rule chips while Off", page.query_selector(".lp-hl-chip") is None))

            surprisal_bg = toks[0].get_attribute("style") or ""
            page.click(f'{match_row} .seg-btn:has-text("On")')
            page.wait_for_selector(".lp-hl-chip.sel", timeout=3000)
            # "adopts the FIRST enabled rule" — which rule that is depends on the
            # instance's own rules (a state snapshot carries them), so assert the
            # invariant, not the name: exactly one chip, and it's the leading one.
            sel = page.query_selector_all(".lp-hl-chip.sel")
            first_chip = page.query_selector(".lp-hl-chip")
            checks.append(("On adopts the first enabled rule",
                           len(sel) == 1 and sel[0].inner_text() == first_chip.inner_text()))
            def core_style(i: int) -> str:
                # Match tint lives on the inner .tok-core — the edge whitespace of
                # a token stays unpainted (it would read as highlighting the gap).
                cores = page.query_selector_all(".tok-stream >> nth=0 >> .tok-core")
                return cores[i].get_attribute("style") or ""

            match_bg = core_style(0)
            checks.append(("tint switches off surprisal", match_bg != surprisal_bg))
            checks.append(("match tint excludes edge whitespace",
                           page.evaluate("""() => [...document.querySelectorAll('.tok-core')]
                             .every(c => c.textContent === c.textContent.trim())""")))
            # Pick OUR rule explicitly: 'Blue' holds 60% of position 0's mass and
            # the rule matches it → a blue band (59,130,246), never the surprisal hue.
            # The chip is a TOGGLE, so click it only if the auto-adopt didn't
            # already land on it — on an instance whose only rule is this one
            # (a fresh state dir), clicking would deselect it.
            blue_chip = page.locator('.lp-hl-chip:has-text("blue")').first
            if "sel" not in (blue_chip.get_attribute("class") or "").split():
                blue_chip.click()
                page.wait_for_timeout(150)
            match_bg = core_style(0)
            checks.append(("tinted with the matching rule's hue",
                           "59, 130, 246" in match_bg or "59,130,246" in match_bg))

            # ── Contrast slider (prob → opacity ramp) ────────────────────
            # 'Blue' holds 60% of position 0 → alpha = 0.6^(1-s) × 0.42:
            #   s=0 (linear) 0.252 · s=0.5 (√, default) 0.325 · s=1 (step) 0.42
            # Read the alpha NUMERICALLY: with two rules picked the tint is a
            # gradient, which Chrome re-serializes with alpha rounded to ~2dp
            # (0.252 → 0.25), so substring matching would be a false failure.
            def blue_alpha(style: str) -> float:
                a = [float(x) for x in re.findall(r"rgba\(59, 130, 246, ([0-9.]+)\)", style)]
                return max(a) if a else -1.0

            def set_ramp(v: float) -> float:
                page.eval_on_selector(
                    ".lp-hl input[type=range]",
                    "(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); }",
                    str(v),
                )
                page.wait_for_timeout(120)
                return blue_alpha(core_style(0))

            near = lambda got, want: abs(got - want) < 0.006  # noqa: E731 (2dp serialization)
            checks.append(("default ramp = the √ shape", near(blue_alpha(match_bg), 0.325)))
            checks.append(("ramp 0 → linear (opacity ∝ mass)", near(set_ramp(0), 0.252)))
            checks.append(("ramp 1 → step (full tint)", near(set_ramp(1), 0.42)))
            checks.append(("ramp readout follows the slider",
                           page.inner_text(".lp-hl-ramp .lp-hl-ramp-val") == "1.00"))
            set_ramp(0.5)

            page.click(f'{match_row} .seg-btn:has-text("Off")')
            page.wait_for_timeout(150)
            toks = page.query_selector_all(".tok-stream >> nth=0 >> .tok")
            checks.append(("Off restores the surprisal tint",
                           (toks[0].get_attribute("style") or "") == surprisal_bg))
            page.click(f'{match_row} .seg-btn:has-text("On")')
            page.wait_for_timeout(150)
            checks.append(("Off kept the picked rule",
                           page.query_selector(".lp-hl-chip.sel") is not None))
            page.click(f'{match_row} .seg-btn:has-text("Off")')

            # Off → normal render returns
            page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("Off")')
            page.wait_for_timeout(200)
            checks.append(("toggle Off restores markdown render",
                           page.query_selector(".tok") is None))

            # ── first-token chart mode ───────────────────────────────────
            page.click('button[data-tooltip^="View response distribution chart"]')
            page.wait_for_selector(".modal-overlay", timeout=5000)
            ft_btn = page.query_selector('.chart-mode-btn:has-text("first token")')
            checks.append(("first-token mode button enabled",
                           ft_btn is not None and ft_btn.get_attribute("disabled") is None))
            # the seeded conv's LATEST turn (b0) has no data; pick turn 1
            ft_btn.click()
            page.wait_for_timeout(200)
            if page.query_selector("select.chart-turn"):
                page.select_option("select.chart-turn", value="0")
                page.wait_for_timeout(200)
            # Since the first-token ops landing (4db0ccb) the token entries render
            # as interactive CHIPS (.ft-chip-label); only the grey rest keeps the
            # plain legend row — read both.
            legend = [el.inner_text() for el in
                      page.query_selector_all(".ft-chip-label, .ft-rest-legend .chart-legend-label")]
            checks.append(("legend = tokens + rest",
                           "Blue" in legend and "Gray" in legend
                           and "[rest of distribution]" in legend))
            # 'Blue' at p=0.6 must be the tallest segment; click it → inspector
            segs = page.query_selector_all("rect.chart-seg")
            checks.append(("segments rendered", len(segs) >= 3))
            heights = [(float(s.get_attribute("height")), s) for s in segs]
            heights.sort(key=lambda x: -x[0])
            heights[0][1].click()
            page.wait_for_selector(".chart-inspect", timeout=3000)
            inspect_txt = page.inner_text(".chart-inspect")
            checks.append(("tallest segment = Blue, inspects its 2 samples",
                           "Blue" in inspect_txt and "2/3" in inspect_txt))
            page.screenshot(path=SHOT_CHART)

            checks.append(("no console errors", not errors))
            if errors:
                print("console errors:", errors[:5])
            browser.close()
    finally:
        for path in (f"/api/workspaces/{conv_id}", f"/api/highlights/{RULE_ID}"):
            try:
                api("DELETE", path)
            except Exception:
                pass

    ok = all(c for _, c in checks)
    for name, c in checks:
        print(f"  {'✓' if c else '✗'} {name}")
    print(f"screenshots: {SHOT_TOKENS} {SHOT_CHART}")
    print("PASS" if ok else "FAIL")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
