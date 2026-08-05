"""Browser smoke for the per-turn "view all samples" eye + its think filter.

100% TOKEN-FREE: seeds a workspace whose second turn has THREE sibling branches
(two with reasoning, one without) and a continuation below the active one, then:

  1. the eye button ([data-testid=samples-view]) shows on the sibling-bearing
     assistant row ONLY (not on user rows / sibling-less assistant rows);
  2. clicking it expands the turn into 3 sample cards, HIDES the later turns,
     marks the active branch, and shows the "2 later turns hidden" exit strip;
  3. the think / no-think filter (top-right, shown because the samples mix both
     modes) narrows the cards: think -> 2, no think -> 1, all -> 3;
  4. selecting a different sample (btn-use) keeps the view OPEN (state is keyed
     on the turn's parent, not the active node) and moves the active mark;
  5. both exits work — the strip and the (now active) eye — restoring the rows
     below; a reload comes back collapsed (view state is session-local).

No model calls.

  uv run python tests/small-smokes/browser_samples_view.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

PAD = "lorem ipsum dolor sit amet " * 4


def seed_tree():
    """u0 -> [a1a* | a1b | a1c] -> u2 -> a3 (active path through a1a).
    a1a + a1c carry reasoning, a1b doesn't -> the turn MIXES think/no-think."""
    nodes = {
        "u0": {"id": "u0", "role": "user", "content": f"QUESTION-0\n\n{PAD}",
               "parent": None, "children": ["a1a", "a1b", "a1c"]},
        "a1a": {"id": "a1a", "role": "assistant", "content": f"ANSWER-A\n\n{PAD}",
                "reasoning": "COT-A thinking here", "parent": "u0", "children": ["u2"]},
        "a1b": {"id": "a1b", "role": "assistant", "content": f"ANSWER-B\n\n{PAD}",
                "parent": "u0", "children": []},
        "a1c": {"id": "a1c", "role": "assistant", "content": f"ANSWER-C\n\n{PAD}",
                "reasoning": "COT-C thinking here", "parent": "u0", "children": []},
        "u2": {"id": "u2", "role": "user", "content": f"QUESTION-2\n\n{PAD}",
               "parent": "a1a", "children": ["a3"]},
        "a3": {"id": "a3", "role": "assistant", "content": f"ANSWER-3\n\n{PAD}",
               "parent": "u2", "children": []},
    }
    return {"nodes": nodes, "rootChildren": ["u0"], "selected": {"u0": "a1a"}}


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def card_texts(panel):
    return panel.locator(".sample-card").all_inner_texts()


def main():
    runs = _get("/api/models")
    assert runs, "isolated instance discovered no runs — seed needs a scan root with runs"
    rid = runs[0]["id"]
    conv = _post("/api/workspaces", {
        "name": "samples view smoke",
        "trees": {"primary": seed_tree()},
        "panels": [{"id": "primary", "run_id": rid, "checkpoint": None}],
    })

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/?w={conv['id']}", wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('ANSWER-3')", timeout=15000)

        panel = page.locator(".chat-column").first
        rows = panel.locator(".message")
        assert rows.count() == 4, f"expected 4 rows [u0, a1a, u2, a3], got {rows.count()}"

        # -- 1. the eye lives on the sibling-bearing assistant row only --
        eyes = panel.locator("[data-testid=samples-view]")
        assert eyes.count() == 1, f"exactly one row should carry the eye, got {eyes.count()}"
        a1_row = rows.nth(1)
        assert a1_row.locator("[data-testid=samples-view]").count() == 1, \
            "the eye should sit on the 3-sibling assistant row"

        # -- 2. expand: cards up, later turns hidden, exit strip present --
        a1_row.locator("[data-testid=samples-view]").click()
        page.wait_for_selector(".sample-card", timeout=5000)
        assert panel.locator(".sample-card").count() == 3, "3 sibling cards expected"
        assert rows.count() == 2, f"later rows must hide while expanded, got {rows.count()}"
        assert "ANSWER-3" not in page.inner_text("body"), "the downstream turn must be hidden"
        strip = panel.locator("[data-testid=hidden-below]")
        assert strip.count() == 1 and "2 later turns hidden" in strip.inner_text(), \
            f"exit strip should count the hidden rows: {strip.all_inner_texts()}"
        active = panel.locator(".sample-card.active-sample")
        assert active.count() == 1 and "ANSWER-A" in active.inner_text(), \
            "the active-path sibling must wear the active mark"
        assert panel.locator("[data-testid=samples-view].active").count() == 1, \
            "the eye should render as active while the view is open"

        # -- 3. think / no-think filter (the samples mix both modes) --
        filt = panel.locator("[data-testid=think-filter]")
        assert filt.count() == 1, "mixed think/no-think samples should show the filter"
        filt.get_by_role("button", name="think 2", exact=True).click()
        texts = card_texts(panel)
        assert len(texts) == 2 and all("ANSWER-B" not in t for t in texts), \
            f"think filter should keep only the 2 CoT samples: {len(texts)}"
        filt.get_by_role("button", name="no think 1", exact=True).click()
        texts = card_texts(panel)
        assert len(texts) == 1 and "ANSWER-B" in texts[0], \
            "no-think filter should keep only the CoT-less sample"
        filt.get_by_role("button", name="all", exact=True).click()
        assert panel.locator(".sample-card").count() == 3, "'all' restores every card"

        # -- 4. selecting another sample keeps the view open --
        cards = panel.locator(".sample-card")
        for i in range(3):
            if "ANSWER-B" in cards.nth(i).inner_text():
                cards.nth(i).locator(".btn-use").click()
                break
        page.wait_for_function(
            "document.querySelector('.sample-card.active-sample')?.innerText.includes('ANSWER-B')",
            timeout=5000)
        assert panel.locator(".sample-card").count() == 3, \
            "picking an active branch must not collapse the sample view"
        # back to A so the downstream turns return on exit
        for i in range(3):
            if "ANSWER-A" in cards.nth(i).inner_text():
                cards.nth(i).locator(".btn-use").click()
                break
        page.wait_for_function(
            "document.querySelector('.sample-card.active-sample')?.innerText.includes('ANSWER-A')",
            timeout=5000)

        # -- 5a. exit via the strip --
        panel.locator("[data-testid=hidden-below]").click()
        page.wait_for_function("document.body.innerText.includes('ANSWER-3')", timeout=5000)
        assert rows.count() == 4, "exit must restore the hidden rows"
        assert panel.locator(".sample-card").count() == 0

        # -- 5b. exit via the active eye --
        rows.nth(1).locator("[data-testid=samples-view]").click()
        page.wait_for_selector(".sample-card", timeout=5000)
        panel.locator("[data-testid=samples-view]").click()
        page.wait_for_function("document.body.innerText.includes('ANSWER-3')", timeout=5000)
        assert rows.count() == 4, "the active eye must also exit"

        # -- 5c. a reload comes back collapsed (view state is session-local) --
        rows.nth(1).locator("[data-testid=samples-view]").click()
        page.wait_for_selector(".sample-card", timeout=5000)
        page.reload(wait_until="load")
        page.wait_for_function("document.body.innerText.includes('ANSWER-3')", timeout=15000)
        assert panel.locator(".sample-card").count() == 0, "expansion must not persist"

        real_errors = [e for e in errors if "favicon" not in e]
        assert not real_errors, f"console errors: {real_errors}"
        browser.close()

    print("browser_samples_view: all checks passed")


if __name__ == "__main__":
    main()
