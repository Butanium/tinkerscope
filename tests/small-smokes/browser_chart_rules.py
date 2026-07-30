"""Distribution-chart × highlight-rules smoke — fully deterministic (no sampling).

Seeds two highlight rules (red / yellow) and a TWO-turn workspace: turn 1 has
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
  - the thinking filter: turn 1 mixes one CoT sample (a0) with four without, so
    the filter select appears there (and NOT on mix-free turn 2); "with
    thinking" charts just a0, "without thinking" the other four, "split think /
    no-think" draws BOTH as adjacent bars (own sub-label, own n=1 / n=4, group
    n=5, inspect scoped to the clicked population), and the filter also applies
    upstream of the exact-answers mode
  - the "exact answers" mode still gives the legacy per-answer histogram (and
    hides the rule chips)
  - view persistence (lib/chart-view): close→reopen and a full page reload both
    restore the mode / match scope / thinking filter (global) AND the charted
    turn + excluded rule chips (per workspace); a second, never-charted
    workspace inherits the global picks with none of the per-question tweaks

Cleans up its rules + workspaces afterwards. Run against the vite dev server
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


def seed() -> tuple[str, str]:
    """Two rules + TWO identical workspaces of 5 assistant siblings.

    The second one is never charted until the very end — it is the fixture for
    "a fresh workspace inherits the global view picks with a clean slate of
    per-question tweaks". Returns both ids.
    """
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
        # a0 carries a CoT so turn 1 mixes thinking / no-thinking samples
        nodes[f"a{i}"] = {"id": f"a{i}", "role": "assistant", "content": a,
                          "parent": "u1", "children": (["u2"] if i == 0 else []),
                          **({"reasoning": "I will simply pick one."} if i == 0 else {})}
    for i, a in enumerate(turn2):
        nodes[f"b{i}"] = {"id": f"b{i}", "role": "assistant", "content": a,
                          "parent": "u2", "children": []}
    ids = []
    for name in ("chart-rules-smoke", "chart-rules-smoke-2"):
        conv = api("POST", "/api/workspaces", {
            "name": name,
            "trees": {"primary": {"nodes": nodes, "rootChildren": ["u1"],
                                  "selected": {"__root__": "u1", "u1": "a0",
                                               "a0": "u2", "u2": "b0"}}},
        })
        ids.append(conv["id"])
    return ids[0], ids[1]


def cleanup(*conv_ids: str | None) -> None:
    for conv_id in conv_ids:
        if conv_id:
            api("DELETE", f"/api/workspaces/{conv_id}")
    for rid in (RULE_RED, RULE_YEL):
        try:
            api("DELETE", f"/api/highlights/{rid}")
        except Exception:
            pass


def main() -> None:
    conv_id, conv2_id = seed()
    checks: list[tuple[str, bool]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(f"{BASE}/?w={conv_id}", wait_until="load", timeout=20000)
            # .model-slot-select is a div since the PickerDropdown rework — this
            # wait only means "sidebar booted", so don't pin the element kind
            page.wait_for_selector(".model-slot-select", timeout=15000)
            # the seeded workspace's user turn is on screen ⇒ tree loaded
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
            checks.append(("mix-free turn hides the thinking filter",
                           page.query_selector("select.chart-think") is None))

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

            # per-rule chart toggles: exclude "yellow" from the bucketing.
            # Chips cover ALL applicable rules — the user's own rules included —
            # so assert ours are among them, not an exact list (same robustness
            # stance as the legend checks: foreign rules don't match our texts).
            chips = [el.inner_text() for el in page.query_selector_all(".chart-rule-chip")]
            checks.append(("seeded rules have chips", {"red", "yellow"} <= set(chips)))
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

            # thinking filter: turn 1 mixes a0 (with CoT) and four without
            checks.append(("mixed turn shows the thinking filter",
                           page.query_selector("select.chart-think") is not None))
            page.select_option("select.chart-think", value="thinking")
            page.wait_for_timeout(200)
            legend_think = [el.inner_text() for el in page.query_selector_all(".chart-legend-label")]
            svg_think = page.text_content(".chart-svg") or ""
            checks.append(("with thinking: only the CoT sample (red, n=1)",
                           legend_think == ["red"] and "n=1" in svg_think))
            page.select_option("select.chart-think", value="no-thinking")
            page.wait_for_timeout(200)
            legend_nothink = [el.inner_text() for el in page.query_selector_all(".chart-legend-label")]
            svg_nothink = page.text_content(".chart-svg") or ""
            checks.append(("without thinking: the other 4, yellow at 50%",
                           legend_nothink == ["yellow", "red + yellow", "no match"]
                           and "n=4" in svg_nothink and "50%" in svg_nothink))
            # split: one bar per population, side by side under the model name.
            # Both sub-labels + both per-bar n, and 5 bar-worth of segments over
            # the union legend (the CoT sample's "red" is back).
            page.select_option("select.chart-think", value="split")
            page.wait_for_timeout(200)
            svg_split = page.text_content(".chart-svg") or ""
            legend_split = [el.inner_text() for el in page.query_selector_all(".chart-legend-label")]
            checks.append(("split: legend is the union of both populations",
                           legend_split == ["red", "yellow", "red + yellow", "no match"]))
            checks.append(("split: both sub-labels rendered",
                           "think" in svg_split and "no-think" in svg_split))
            checks.append(("split: per-bar n=1 / n=4 (disjoint populations)",
                           "n=1" in svg_split and "n=4" in svg_split))
            checks.append(("split: group n=5 under the model name", "n=5" in svg_split))
            checks.append(("split: one sub-label block per bar",
                           len(page.query_selector_all('.chart-svg text[font-style="italic"]')) == 2))
            # the think bar is 100% red (its single sample), the no-think bar 50% yellow
            checks.append(("split: think bar at 100%, no-think yellow at 50%",
                           "100%" in svg_split and "50%" in svg_split))
            # clicking a split bar's segment inspects THAT population only
            page.click('rect[data-tooltip^="red —"]')
            page.wait_for_selector(".chart-inspect", timeout=3000)
            head_split = page.inner_text(".chart-inspect-head")
            checks.append(("split: inspector names the population and its n",
                           "1/1" in head_split and "think" in head_split))
            page.click(".chart-inspect-close")
            page.select_option("select.chart-think", value="all")
            page.wait_for_timeout(200)

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

            # the thinking filter is upstream of both modes — answers mode too
            page.select_option("select.chart-think", value="thinking")
            page.wait_for_timeout(200)
            legend3 = [el.inner_text() for el in page.query_selector_all(".chart-legend-label")]
            checks.append(("answers mode + with thinking: just 'red'", legend3 == ["red"]))

            # the whole VIEW persists (lib/chart-view, localStorage) — the global
            # picks (mode / match scope / thinking filter) and the per-workspace
            # ones (charted turn, excluded rule chips) both survive close→reopen
            # AND a full reload. They used to die with the modal every time.
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
            page.click('button[data-tooltip^="View response distribution chart"]')
            page.wait_for_selector(".modal-overlay", timeout=5000)
            checks.append(("reopen keeps the bucketing mode",
                           page.inner_text('.chart-mode[aria-label="Bucketing mode"] .chart-mode-btn.active')
                           == "exact answers"))
            checks.append(("reopen keeps the charted turn (not back to latest)",
                           "Say a color." in page.inner_text(".chart-question")))
            checks.append(("reopen keeps the thinking filter",
                           page.input_value("select.chart-think") == "thinking"))
            # now: rules mode, BOTH splits, and a rule chip excluded — then reload
            page.click('.chart-mode[aria-label="Bucketing mode"] .chart-mode-btn:has-text("highlight rules")')
            page.wait_for_timeout(150)
            page.click('.chart-mode[aria-label="Match scope"] .chart-mode-btn:has-text("split")')
            page.select_option("select.chart-think", value="split")
            page.click('.chart-rule-chip:has-text("yellow")')
            page.wait_for_timeout(200)
            page.reload(wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-select", timeout=15000)
            page.click('button[data-tooltip^="View response distribution chart"]')
            page.wait_for_selector(".modal-overlay", timeout=5000)
            page.wait_for_timeout(300)
            scope_active = [el.inner_text() for el in
                            page.query_selector_all('.chart-mode[aria-label="Match scope"] .chart-mode-btn.active')]
            checks.append(("reload keeps mode + match scope",
                           page.inner_text('.chart-mode[aria-label="Bucketing mode"] .chart-mode-btn.active')
                           == "highlight rules" and scope_active == ["split"]))
            checks.append(("reload keeps the thinking filter",
                           page.input_value("select.chart-think") == "split"))
            checks.append(("reload keeps the per-workspace turn",
                           "Say a color." in page.inner_text(".chart-question")))
            checks.append(("reload keeps the per-workspace rule exclusion",
                           page.query_selector('.chart-rule-chip.off:has-text("yellow")') is not None))
            # both splits compose: think·response, think·thinking, no-think·response
            # (no vacuous thinking bar for the no-CoT population)
            svg_both = page.text_content(".chart-svg") or ""
            checks.append(("both splits: 3 bars, per-bar n=1 / n=4",
                           len(page.query_selector_all('.chart-svg text[font-style="italic"]')) == 3
                           and "n=1" in svg_both and "n=4" in svg_both))
            # a workspace never charted before inherits the global picks with a
            # clean slate of per-question tweaks (no stale exclusion, turn=latest)
            page.goto(f"{BASE}/?w={conv2_id}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-select", timeout=15000)
            page.wait_for_function(
                "document.body.innerText.includes('Say a color.')", timeout=15000
            )
            page.click('button[data-tooltip^="View response distribution chart"]')
            page.wait_for_selector(".modal-overlay", timeout=5000)
            page.wait_for_timeout(300)
            scope2 = [el.inner_text() for el in
                      page.query_selector_all('.chart-mode[aria-label="Match scope"] .chart-mode-btn.active')]
            checks.append(("fresh workspace inherits the global picks", scope2 == ["split"]))
            checks.append(("fresh workspace has no inherited rule exclusion",
                           page.query_selector(".chart-rule-chip.off") is None))

            checks.append(("no console/page errors", not errors))
            if errors:
                print("errors:", errors)
            browser.close()
    finally:
        cleanup(conv_id, conv2_id)

    for name, passed_ in checks:
        print(f"  {'✓' if passed_ else '✗'} {name}")
    ok = all(c for _, c in checks)
    print("CHART RULES SMOKE", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
