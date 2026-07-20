"""First-token chart ops smoke — fully deterministic (no sampling).

Seeds a conversation whose assistant siblings carry `token_logprobs`, then drives
the three first-token-mode operations that ride on the ALREADY-RECORDED logprobs:

  exclude + renormalize:
  - click a token chip → it greys out, its mass leaves, survivors renormalize to
    100%, and the "renormalized over NN% of original mass" honesty note appears

  add a recorded-but-hidden token:
  - a token recorded only in an OLDER sibling's top-K (not in the reference top-K,
    never sampled here) is hidden in the grey rest; the search box finds it and
    "add" pulls it into its own colored segment (no model call)

  merge into one color:
  - drag one token chip onto another → they fuse into one group segment (prob =
    sum, one color, a chip naming its members) that composes with exclude

The pure math is unit-tested in web/src/lib/chart.test.ts; this pins the UI.

  uv run python tests/small-smokes/browser_chart_firsttoken_ops.py [BASE_URL]
"""
import json
import math
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5199"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_ft_ops.png"
LN = math.log


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def tlp(entries):
    return [{"t": t, "tid": tid, "lp": lp, **({"top": top} if top else {})} for t, tid, lp, top in entries]


# Reference top-K (Blue .5, Gray .3) shared by the two NEWER siblings; the OLDER
# sibling's top-K instead lists Green .2 — recorded, but hidden from the chart.
REF = [["Blue", 11, LN(0.5)], ["Gray", 12, LN(0.3)]]
OLD = [["Blue", 11, LN(0.5)], ["Green", 14, LN(0.2)]]


def seed() -> str:
    api("POST", "/api/state", {"panel_messages": {"primary": []}})
    nodes = {
        "u1": {"id": "u1", "role": "user", "content": "Say a color.",
               "parent": None, "children": ["ag", "ab1", "ab2"]},
        # OLDER sibling (first child) — carries Green in its top-K
        "ag": {"id": "ag", "role": "assistant", "content": "Blue", "parent": "u1", "children": [],
               "token_logprobs": tlp([("Blue", 11, LN(0.5), OLD)])},
        # NEWER siblings — their (shared) top-K is the reference: Blue, Gray
        "ab1": {"id": "ab1", "role": "assistant", "content": "Blue", "parent": "u1", "children": [],
                "token_logprobs": tlp([("Blue", 11, LN(0.5), REF)])},
        "ab2": {"id": "ab2", "role": "assistant", "content": "Gray", "parent": "u1", "children": ["u2"],
                "token_logprobs": tlp([("Gray", 12, LN(0.3), REF)])},
        "u2": {"id": "u2", "role": "user", "content": "next", "parent": "ab2", "children": []},
    }
    conv = api("POST", "/api/conversations", {
        "name": "ft-ops-smoke",
        "trees": {"primary": {"nodes": nodes, "rootChildren": ["u1"],
                              "selected": {"__root__": "u1", "u1": "ab2", "ab2": "u2"}}},
    })
    return conv["id"]


# Dispatch a real HTML5 drag src→tgt by LABEL (Playwright's drag_to is flaky for
# native DnD; passing ElementHandles into evaluate doesn't deserialize — resolve
# the chips inside the page instead).
DRAG_JS = """
([srcL, dstL]) => {
  const chips = [...document.querySelectorAll('.ft-chip')];
  const byLabel = (l) => chips.find((c) => c.querySelector('.ft-chip-label')?.textContent === l);
  const src = byLabel(srcL), tgt = byLabel(dstL);
  const fire = (el, ev) => el.dispatchEvent(new DragEvent(ev, { dataTransfer: new DataTransfer(), bubbles: true, cancelable: true }));
  fire(src, 'dragstart'); fire(tgt, 'dragover'); fire(tgt, 'drop'); fire(src, 'dragend');
}
"""


def chip(page, label):
    """The .ft-chip whose label is EXACTLY `label` (avoids 'Blue' matching 'Blue + Gray')."""
    return page.query_selector(f'.ft-chip:has(.ft-chip-label:text-is("{label}"))')


def seg_pct(page, label):
    """Approx pct of the first bar's segment for `label`, from its rendered height."""
    el = page.query_selector(f'rect.chart-seg[aria-label^="{label}:"]')
    return round(float(el.get_attribute("height")) / 300 * 100) if el else None


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
            page.wait_for_function("document.body.innerText.includes('Say a color?') "
                                   "|| document.body.innerText.includes('Say a color')", timeout=15000)

            # open chart → first-token mode → pick turn 1 (the seeded turn)
            page.click('button[data-tooltip^="View response distribution chart"]')
            page.wait_for_selector(".modal-overlay", timeout=5000)
            page.click('.chart-mode-btn:has-text("first token")')
            page.wait_for_timeout(150)
            if page.query_selector("select.chart-turn"):
                page.select_option("select.chart-turn", value="0")
                page.wait_for_timeout(150)
            page.wait_for_selector(".ft-chip", timeout=3000)

            labels = [c.inner_text() for c in page.query_selector_all(".ft-chip-label")]
            checks.append(("chips = Blue, Gray (Green hidden)", "Blue" in labels and "Gray" in labels and "Green" not in labels))
            checks.append(("Blue segment ≈ 50%", seg_pct(page, "Blue") == 50))

            # ── exclude folds into rest (no renormalization) ─────────────
            rest_before = seg_pct(page, "[rest of distribution]")
            chip(page, "Gray").click()
            page.wait_for_timeout(150)
            checks.append(("no renormalized note ever appears", page.query_selector(".chart-note:has-text('renormalized')") is None))
            checks.append(("Blue stays ≈50% (absolute, no renorm)", seg_pct(page, "Blue") == 50))
            checks.append(("Gray segment disappears", seg_pct(page, "Gray") is None))
            rest_after = seg_pct(page, "[rest of distribution]")
            checks.append(("rest grows by ≈30% (Gray's mass folds in)",
                           rest_before is not None and rest_after is not None
                           and abs((rest_after - rest_before) - 30) <= 1))
            # re-include Gray
            chip(page, "Gray").click()
            page.wait_for_timeout(150)
            checks.append(("re-include restores Blue to 50%", seg_pct(page, "Blue") == 50))
            checks.append(("re-include restores Gray segment", seg_pct(page, "Gray") == 30))

            # ── add a recorded-but-hidden token (Green) ──────────────────
            page.fill(".ft-add-input", "Green")
            page.wait_for_timeout(150)
            match = page.query_selector('.ft-match:has-text("Green")')
            checks.append(("search finds hidden Green", match is not None))
            if match:
                match.click()
                page.wait_for_timeout(150)
            new_labels = [c.inner_text() for c in page.query_selector_all(".ft-chip-label")]
            checks.append(("Green added as its own chip", "Green" in new_labels))
            checks.append(("Green segment ≈ 20%", seg_pct(page, "Green") == 20))

            # ── merge Blue + Gray into one color ─────────────────────────
            page.evaluate(DRAG_JS, ["Blue", "Gray"])
            page.wait_for_timeout(200)
            merged = page.query_selector(".ft-chip.merged")
            mlabel = merged.query_selector(".ft-chip-label").inner_text() if merged else ""
            checks.append(("drag merges Blue+Gray into one chip",
                           merged is not None and "Blue" in mlabel and "Gray" in mlabel))
            checks.append(("merged group ≈ 80%", seg_pct(page, mlabel) == 80))
            checks.append(("merged chip is dashed + has split ⊗", merged is not None and merged.query_selector(".ft-chip-x") is not None))
            # split it back
            if merged:
                merged.query_selector(".ft-chip-x").click()
                page.wait_for_timeout(150)
            checks.append(("split restores Blue + Gray singletons",
                           chip(page, "Blue") is not None and chip(page, "Gray") is not None
                           and page.query_selector('.ft-chip.merged') is None))

            page.screenshot(path=SHOT)
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
    print(f"screenshot: {SHOT}")
    print("PASS" if ok else "FAIL")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
