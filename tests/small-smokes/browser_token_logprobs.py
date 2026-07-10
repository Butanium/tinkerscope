"""Token-logprob feature smoke — fully deterministic (no sampling).

Seeds a conversation whose assistant siblings carry `token_logprobs` (the shape
the native tinker path emits — see docs/API_CONTRACT.md), then drives both
display surfaces:

  token-view toggle:
  - sidebar "Token probs" On → the active assistant turn renders as raw token
    spans (.tok), surprising tokens visibly tinted
  - hovering a token opens the popover with its probability + the top-K
    alternatives as bars (the sampled alternative highlighted)
  - a turn WITHOUT logprobs shows the "no token data" pill instead
  - Off → back to the normal markdown render

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


def seed() -> str:
    """One turn with 3 logprob-carrying siblings + a follow-up turn without."""
    # Clear the shared panel echo first: a stale transcript from a previous chat
    # would otherwise be grafted into the freshly-opened conversation by the
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
    conv = api("POST", "/api/conversations", {
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

            page.goto(f"{BASE}/?c={conv_id}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-select", timeout=15000)
            page.wait_for_function(
                "document.body.innerText.includes('What color is the sky?')", timeout=15000
            )

            # ── token-view toggle ────────────────────────────────────────
            checks.append(("no .tok spans before toggle", page.query_selector(".tok") is None))
            page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("On")')
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
            legend = [el.inner_text() for el in page.query_selector_all(".chart-legend-label")]
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
        try:
            api("DELETE", f"/api/conversations/{conv_id}")
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
