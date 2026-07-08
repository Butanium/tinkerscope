"""Distribution-chart × highlight-rules smoke — fully deterministic (no sampling).

Seeds two highlight rules (red / yellow) and a TWO-turn conversation: turn 1 has
5 hand-authored assistant siblings ("red", "yellow" x2, "red and yellow",
"nothing here at all"), turn 2 has 2 ("red", "no color here"). Then opens the
chart modal and asserts the whole new flow:

  - rule mode is the default when rules exist
  - the chart defaults to the LATEST turn (turn 2: n=2, its question captioned)
  - the turn picker switches back to turn 1 (n=5), where the legend shows the
    single-rule buckets + the striped red+yellow combo + grey no-match, the
    combo segment is a striped <pattern> fill, and clicking it opens the
    inspector with the sample text highlight-painted
  - the per-rule chart toggles: clicking the "yellow" chip drops that rule from
    the bucketing (legend collapses to red / no-match, the red+yellow sample
    re-buckets as red, the open inspector closes), clicking again restores it
  - the "exact answers" mode still gives the legacy per-answer histogram (and
    hides the rule chips)

Cleans up its rules + conversation afterwards. Run against the vite dev server
(live source) or a built instance:

  uv run python tests/small-smokes/browser_chart_rules.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT_RULES = "/tmp/tinkerscope_chart_rules.png"
SHOT_ANSWERS = "/tmp/tinkerscope_chart_answers.png"

RULE_RED = "smoke-chart-red"
RULE_YEL = "smoke-chart-yel"


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def seed() -> str:
    """Two rules + a conversation with 5 assistant siblings. Returns conv id."""
    for i, (rid, name, pat, color) in enumerate(
        [(RULE_RED, "red", "red", "#f87171"), (RULE_YEL, "yellow", "yellow", "#fde047")]
    ):
        api("PUT", f"/api/highlights/{rid}", {
            "id": rid, "name": name, "enabled": True, "patterns": [pat],
            "combinator": "or", "is_regex": False, "case_sensitive": False,
            "color": color, "scope_role": None, "sort_order": 100 + i,
        })

    turn1 = ["red", "yellow", "red and yellow", "nothing here at all", "yellow"]
    turn2 = ["red", "no color here"]
    nodes = {
        "u1": {"id": "u1", "role": "user", "content": "Say a color.",
               "parent": None, "children": [f"a{i}" for i in range(len(turn1))]},
        "u2": {"id": "u2", "role": "user", "content": "Say another color.",
               "parent": "a0", "children": [f"b{i}" for i in range(len(turn2))]},
    }
    for i, a in enumerate(turn1):
        nodes[f"a{i}"] = {"id": f"a{i}", "role": "assistant", "content": a,
                          "parent": "u1", "children": (["u2"] if i == 0 else [])}
    for i, a in enumerate(turn2):
        nodes[f"b{i}"] = {"id": f"b{i}", "role": "assistant", "content": a,
                          "parent": "u2", "children": []}
    conv = api("POST", "/api/conversations", {
        "name": "chart-rules-smoke",
        "trees": {"primary": {"nodes": nodes, "rootChildren": ["u1"],
                              "selected": {"__root__": "u1", "u1": "a0",
                                           "a0": "u2", "u2": "b0"}}},
    })
    return conv["id"]


def cleanup(conv_id: str | None) -> None:
    if conv_id:
        api("DELETE", f"/api/conversations/{conv_id}")
    for rid in (RULE_RED, RULE_YEL):
        try:
            api("DELETE", f"/api/highlights/{rid}")
        except Exception:
            pass


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
            page.wait_for_selector("select.model-slot-select", timeout=15000)
            # the seeded conversation's user turn is on screen ⇒ tree loaded
            page.wait_for_function(
                "document.body.innerText.includes('Say a color.')", timeout=15000
            )

            page.click('button[data-tooltip^="View response distribution chart"]')
            page.wait_for_selector(".modal-overlay", timeout=5000)

            # rule mode is the default when rules exist
            active_mode = page.inner_text(".chart-mode-btn.active")
            checks.append(("default mode = highlight rules", active_mode == "highlight rules"))

            # defaults to the LATEST turn: turn 2's question + n=2
            caption = page.inner_text(".chart-question")
            checks.append(("defaults to latest turn (caption)", "Say another color." in caption))
            checks.append(("latest turn n=2", "n=2" in (page.text_content(".chart-svg") or "")))
            legend_t2 = [el.inner_text() for el in page.query_selector_all(".chart-legend-label")]
            checks.append(("latest turn legend = red / no match", legend_t2 == ["red", "no match"]))

            # the turn picker switches back to turn 1
            page.select_option("select.chart-turn", value="0")
            page.wait_for_timeout(200)
            checks.append(
                ("turn picker → turn 1 caption", "Say a color." in page.inner_text(".chart-question"))
            )
            legend = [el.inner_text() for el in page.query_selector_all(".chart-legend-label")]
            checks.append(
                ("legend = red / yellow / combo / no match",
                 legend == ["red", "yellow", "red + yellow", "no match"])
            )
            checks.append(("striped pattern def present", page.query_selector("svg pattern") is not None))
            svg_text = page.text_content(".chart-svg") or ""  # SVG has no innerText
            checks.append(("n=5 under the bar", "n=5" in svg_text))
            checks.append(("yellow segment at 40%", "40%" in svg_text))

            # click the striped combo segment → inspector with painted sample
            page.click('rect[data-tooltip^="red + yellow"]')
            page.wait_for_selector(".chart-inspect", timeout=3000)
            head = page.inner_text(".chart-inspect-head")
            checks.append(("inspector head names the bucket", "red + yellow" in head and "1/5" in head))
            marks = page.query_selector_all(".chart-inspect-sample mark")
            checks.append(("inspected sample is highlight-painted", len(marks) >= 2))
            page.screenshot(path=SHOT_RULES)

            # per-rule chart toggles: exclude "yellow" from the bucketing
            chips = [el.inner_text() for el in page.query_selector_all(".chart-rule-chip")]
            checks.append(("one chip per applicable rule", chips == ["red", "yellow"]))
            page.click('.chart-rule-chip:has-text("yellow")')
            page.wait_for_timeout(200)
            legend_off = [el.inner_text() for el in page.query_selector_all(".chart-legend-label")]
            checks.append(("yellow off: legend = red / no match", legend_off == ["red", "no match"]))
            svg_off = page.text_content(".chart-svg") or ""
            # red + "red and yellow" re-bucket together: 2/5 red, 3/5 no match
            checks.append(("yellow off: red at 40%, no match at 60%",
                           "40%" in svg_off and "60%" in svg_off))
            checks.append(("toggling closes the stale inspector",
                           page.query_selector(".chart-inspect") is None))
            checks.append(("excluded chip is marked off",
                           page.query_selector('.chart-rule-chip.off:has-text("yellow")') is not None))
            page.click('.chart-rule-chip:has-text("yellow")')
            page.wait_for_timeout(200)
            legend_back = [el.inner_text() for el in page.query_selector_all(".chart-legend-label")]
            checks.append(("re-including restores the full legend",
                           legend_back == ["red", "yellow", "red + yellow", "no match"]))

            # exact-answers mode still works (legacy histogram)
            page.click('.chart-mode-btn:has-text("exact answers")')
            page.wait_for_timeout(200)
            legend2 = [el.inner_text() for el in page.query_selector_all(".chart-legend-label")]
            checks.append(
                ("answers mode: 4 exact buckets",
                 sorted(legend2) == sorted(["red", "yellow", "red and yellow", "nothing here at all"]))
            )
            checks.append(("answers mode: yellow at 40%", "40%" in (page.text_content(".chart-svg") or "")))
            checks.append(("answers mode hides the rule chips",
                           page.query_selector(".chart-rule-chip") is None))
            page.screenshot(path=SHOT_ANSWERS)

            checks.append(("no console/page errors", not errors))
            if errors:
                print("errors:", errors)
            browser.close()
    finally:
        cleanup(conv_id)

    for name, passed_ in checks:
        print(f"  {'✓' if passed_ else '✗'} {name}")
    ok = all(c for _, c in checks)
    print("CHART RULES SMOKE", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
