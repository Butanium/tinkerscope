"""The chart inspector must survive a LIVE chart update — needs real sampling.

The bug this pins (Clément, 2026-07-29): "as the chart gets updated live it
resets the state of the widgets, like thinking folded or not, in the response
distribution bottom viewer". Two leaks, both the viewer not being isolated from
the plot's re-derivation:

  1. the per-sample `thinking` fold lived only in the DOM (`<details open={…}>`),
     so any moment the inspector's DOM was recreated it snapped back to the
     scope default;
  2. the inspected bar was addressed by its INDEX in `data.bars` — a list rebuilt
     on every streamed sample, with bars appearing and vanishing mid-batch. A
     shifted index silently re-points the inspector at a DIFFERENT bucket, or
     misses and takes the whole panel down with it.

The scenario here isolates (2)'s trigger with ONE free-router sample, and gets
(1) checked along the way. Two panels: `primary` on the free router with an EMPTY
tree (so it contributes NO bar — `+page.buildChartSources` skips panels with no
turns), `compare` carrying hand-authored samples with a CoT. The chart therefore
opens with a single bar, at index 0, belonging to COMPARE. Inspect its bucket,
open one sample's thinking, then fire a foreign chat into `primary`: it gains a
streaming turn, so a bar is INSERTED at index 0 and compare's bar slides to 1.

Under the old index addressing the inspector then reads bars[0] = primary — a
different panel, whose 'calm' bucket is empty — so the panel vanishes (and with
it the fold). It must instead stay on compare's bucket, with the fold as left.

Needs OPENROUTER_API_KEY (free router). Cleans up its rule + workspace.

  uv run python tests/small-smokes/browser_chart_live_inspect.py [BASE_URL]
"""
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8812"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
FREE = "openrouter:openrouter/free"
# compare's samples are hand-authored, so its model never gets called — any
# label distinct from the free router's will do.
OTHER = "base:Qwen/Qwen3-8B"
RULE = "smoke-live-inspect-calm"


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"null")


def seed() -> str:
    """A rule + 2 panels: primary EMPTY (no bar yet), compare with 3 CoT samples."""
    api("PUT", f"/api/highlights/{RULE}", {
        "id": RULE, "name": "calm", "enabled": True, "patterns": ["calm"],
        "combinator": "or", "is_regex": False, "case_sensitive": False,
        "color": "#60a5fa", "scope_role": None, "sort_order": 190,
    })
    answers = ["calm", "calm", "restless"]
    nodes = {
        "u1": {"id": "u1", "role": "user", "content": "Give me a one-word mood.",
               "parent": None, "children": [f"a{i}" for i in range(len(answers))]},
    }
    for i, a in enumerate(answers):
        nodes[f"a{i}"] = {
            "id": f"a{i}", "role": "assistant", "content": a, "parent": "u1", "children": [],
            "reasoning": f"Weighing the options, take {i}.",
        }
    conv = api("POST", "/api/workspaces", {
        "title": "chart-live-inspect",
        "panels": [{"id": "primary", "run_id": FREE, "checkpoint": None},
                   {"id": "compare", "run_id": OTHER, "checkpoint": None}],
        "trees": {
            "primary": {"nodes": {}, "rootChildren": [], "selected": {}},
            "compare": {"nodes": nodes, "rootChildren": ["u1"],
                        "selected": {"__root__": "u1", "u1": "a0"}},
        },
        "reduced_panels": [], "send_targets": ["primary"],
        "seen_panels": ["primary", "compare"],
    })
    return conv["id"]


def cleanup(cid: str | None) -> None:
    if cid:
        api("DELETE", f"/api/workspaces/{cid}")
    try:
        api("DELETE", f"/api/highlights/{RULE}")
    except Exception:
        pass


def fire_foreign(cid: str, out: dict) -> None:
    """Stream a chat into `primary` as an EXTERNAL actor (token not the browser's):
    the shared bus carries it, so the open chart re-derives live."""
    api("POST", "/api/state", {"workspace_id": cid})
    body = {
        "openrouter_model": "openrouter/free",
        "messages": [{"role": "user", "content": "Give me a one-word mood."}],
        # broadcast mirrors the per-sample stream onto the SHARED bus — without
        # it the browser never sees the samples and the chart never re-derives.
        "panel": "primary", "broadcast": True,
        "client_token": "external-not-owned-by-browser",
        "max_tokens": 24, "n_samples": 1,
    }
    req = urllib.request.Request(f"{BASE}/api/chat", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        out["done"] = "event: done" in r.read().decode()


# The inspector's state, as the DOM sees it.
INSPECT_STATE = """() => {
  const box = document.querySelector('.chart-inspect');
  if (!box) return null;
  return {
    head: box.querySelector('.chart-inspect-title')?.textContent ?? '',
    samples: box.querySelectorAll('.chart-inspect-sample').length,
    folds: [...box.querySelectorAll('details.chart-inspect-think')].map((d) => d.open),
  };
}"""
# One `n=` tspan per model group ⇒ how many panels the chart is drawing.
GROUPS = ("() => [...document.querySelectorAll('.chart-svg text')]"
          ".filter((t) => (t.textContent || '').includes('n=')).length")
# compare's label is `base:Qwen/Qwen3-8B`; wrapLabel renders it as "Qwen Qwen38B",
# so match on a fragment that survives the wrap, never the raw id.
COMPARE_LABEL = "Qwen"


def main() -> None:
    cid = seed()
    checks: list[tuple[str, bool]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(f"{BASE}/?w={cid}", wait_until="load", timeout=20000)
            page.wait_for_selector(".chat-column", timeout=15000)
            page.wait_for_function(
                "document.body.innerText.includes('Give me a one-word mood.')", timeout=15000)

            page.click('button[data-tooltip^="View response distribution chart"]')
            page.wait_for_selector(".modal-overlay", timeout=5000)
            page.click('.chart-mode[aria-label="Bucketing mode"] .chart-mode-btn:has-text("highlight rules")')
            page.click('.chart-mode[aria-label="Match scope"] .chart-mode-btn:has-text("response")')
            page.select_option("select.chart-think", value="all") if page.query_selector(
                "select.chart-think") else None
            page.wait_for_timeout(250)
            groups0 = page.evaluate(GROUPS)
            svg0 = page.text_content(".chart-svg") or ""
            checks.append(("only the seeded panel has a bar (the empty one is skipped)",
                           groups0 == 1 and COMPARE_LABEL in svg0))

            page.click('rect[data-tooltip^="calm —"]')
            page.wait_for_selector(".chart-inspect", timeout=3000)
            before = page.evaluate(INSPECT_STATE)
            checks.append(("inspector on compare's 'calm' bucket (2 samples)",
                           before is not None and before["samples"] == 2
                           and "calm" in before["head"] and COMPARE_LABEL in before["head"]))
            checks.append(("folds start closed under the response scope",
                           before["folds"] == [False, False]))
            # the user's own choice: open the FIRST sample's thinking
            page.locator("details.chart-inspect-think").first.locator("summary").click()
            page.wait_for_timeout(150)
            opened = page.evaluate(INSPECT_STATE)
            checks.append(("opening one fold sticks", opened["folds"] == [True, False]))

            # ── the live update: primary gains its first samples → a bar is
            # INSERTED before compare's, shifting every index ──
            out: dict = {}
            t = threading.Thread(target=fire_foreign, args=(cid, out), daemon=True)
            t.start()
            # catch it MID-stream: primary's streaming pseudo-turn is on the chart
            shifted = False
            for _ in range(120):
                if page.evaluate(GROUPS) > 1:
                    shifted = True
                    break
                page.wait_for_timeout(250)
            checks.append(("primary's bar appeared mid-stream (the index shift happened)", shifted))
            mid = page.evaluate(INSPECT_STATE)
            checks.append(("mid-stream: inspector still on COMPARE's bucket",
                           mid is not None and COMPARE_LABEL in (mid["head"] or "")
                           and mid["samples"] == 2))
            checks.append(("mid-stream: the opened fold is still open",
                           mid is not None and mid["folds"] == [True, False]))

            t.join(timeout=150)
            time.sleep(3.0)  # let the foreign fold + debounced save land
            after = page.evaluate(INSPECT_STATE)
            checks.append(("after the fold: inspector still on COMPARE's bucket",
                           after is not None and COMPARE_LABEL in (after["head"] or "")
                           and after["samples"] == 2))
            checks.append(("after the fold: the user's fold is still open",
                           after is not None and after["folds"] == [True, False]))
            checks.append(("the stream actually completed", out.get("done") is True))

            checks.append(("no page errors", not errors))
            if errors:
                print("errors:", errors)
            browser.close()
    finally:
        cleanup(cid)

    for name, passed_ in checks:
        print(f"  {'✓' if passed_ else '✗'} {name}")
    ok = all(c for _, c in checks)
    print("CHART LIVE INSPECT SMOKE", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
