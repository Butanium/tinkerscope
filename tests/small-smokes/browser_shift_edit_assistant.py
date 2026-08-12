"""Shift+edit on an ASSISTANT row — fully deterministic (no sampling).

A plain assistant edit forks a manual sibling with NO children, so everything
below the edited turn stays behind on the branch you just left. That's right for
"put words in its mouth and continue", and wrong for "rewrite this one reply in
the middle of a thread" — which is what Shift means on a user row (fork a full
editable copy, generate nothing). This smoke pins that Shift now means the same
thing on an assistant row:

  1. the edit button shows its Shift variant (edit-copy) on an ASSISTANT row,
     not just a user one, and the editor shows the shift-edit hint;
  2. saving a shift-edit keeps the turns BELOW on the new branch;
  3. the original assistant turn (with its own downstream) is still there behind
     the row's ‹k/N› cycler, downstream intact;
  4. a plain (no-shift) edit still forks a leaf — the old behavior is unchanged.

No model calls.

  uv run python tests/small-smokes/browser_shift_edit_assistant.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from _console import attach

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def seed_tree():
    """A linear thread: U1 -> A1 -> U2 -> A2. The shift-edit target is A1."""
    nodes = {
        "u1": {"id": "u1", "role": "user", "content": "QUESTION-ONE",
               "parent": None, "children": ["a1"]},
        "a1": {"id": "a1", "role": "assistant", "content": "ANSWER-ONE",
               "parent": "u1", "children": ["u2"]},
        "u2": {"id": "u2", "role": "user", "content": "QUESTION-TWO",
               "parent": "a1", "children": ["a2"]},
        "a2": {"id": "a2", "role": "assistant", "content": "ANSWER-TWO",
               "parent": "u2", "children": []},
    }
    return {"nodes": nodes, "rootChildren": ["u1"], "selected": {"u1": "a1"}}


def texts(panel):
    return [t.strip() for t in panel.locator(".message .message-content").all_inner_texts()]


def main():
    runs = _get("/api/models")
    assert runs, "isolated instance discovered no runs"
    # The shared panel echo can graft a stale transcript into a freshly opened
    # workspace — clear it, like the other tree-seeding smokes do.
    _post("/api/state", {"panel_messages": {"primary": []}})
    conv = _post("/api/workspaces", {
        "name": "shift edit assistant smoke",
        "trees": {"primary": seed_tree()},
        "panels": [{"id": "primary", "run_id": runs[0]["id"], "checkpoint": None}],
    })

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1300, "height": 950})
        errors = []
        net = attach(page, errors)  # noqa: F841 — evidence for a console failure
        page.goto(f"{BASE}/?w={conv['id']}", wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('ANSWER-TWO')", timeout=15000)

        panel = page.locator(".chat-column").first
        rows = panel.locator(".message")
        assert texts(panel) == ["QUESTION-ONE", "ANSWER-ONE", "QUESTION-TWO", "ANSWER-TWO"], \
            f"seeded thread should render in order: {texts(panel)}"

        # -- 1. the Shift variant is offered on an ASSISTANT row --
        a1_row = rows.nth(1)
        a1_row.hover()
        page.keyboard.down("Shift")
        # The icon swaps to edit-copy while shift is held; the tooltip promises
        # the fork. Assert the tooltip (the icon has no stable name in the DOM).
        tip = a1_row.locator("[aria-label='Edit']").get_attribute("data-tooltip")
        assert tip and "fork a full editable copy" in tip, \
            f"assistant row should offer the shift fork: {tip!r}"
        page.keyboard.up("Shift")

        # -- 2. shift-edit keeps the turns below --
        a1_row.locator("[aria-label='Edit']").click(modifiers=["Shift"])
        assert panel.locator(".edit-hint").count() == 1, "shift-edit must show its hint"
        ta = panel.locator("textarea.edit-textarea").last
        ta.fill("ANSWER-ONE-EDITED")
        panel.locator(".btn-edit-save").click()
        page.wait_for_function(
            "document.body.innerText.includes('ANSWER-ONE-EDITED')", timeout=5000)
        assert texts(panel) == ["QUESTION-ONE", "ANSWER-ONE-EDITED", "QUESTION-TWO", "ANSWER-TWO"], \
            f"shift-edit must carry the downstream turns: {texts(panel)}"

        # -- 3. the original branch is intact behind the cycler --
        cycler = rows.nth(1).locator("[data-testid='branch-cycle']")
        assert cycler.count() == 1 and "2/2" in cycler.inner_text(), \
            f"the edit should be sibling 2 of 2: {cycler.all_inner_texts()}"
        cycler.locator("button[aria-label='Previous branch']").click()
        page.wait_for_function(
            "!document.body.innerText.includes('ANSWER-ONE-EDITED')", timeout=5000)
        assert texts(panel) == ["QUESTION-ONE", "ANSWER-ONE", "QUESTION-TWO", "ANSWER-TWO"], \
            f"the original branch keeps its own downstream: {texts(panel)}"

        # -- 4. a PLAIN edit still forks a leaf (unchanged behavior) --
        rows.nth(1).locator("[aria-label='Edit']").click()
        assert panel.locator(".edit-hint").count() == 0, "plain edit shows no shift hint"
        ta = panel.locator("textarea.edit-textarea").last
        ta.fill("ANSWER-ONE-PLAIN")
        panel.locator(".btn-edit-save").click()
        page.wait_for_function(
            "document.body.innerText.includes('ANSWER-ONE-PLAIN')", timeout=5000)
        assert texts(panel) == ["QUESTION-ONE", "ANSWER-ONE-PLAIN"], \
            f"a plain assistant edit still dead-ends at the edit: {texts(panel)}"

        real_errors = [e for e in errors if "favicon" not in e]
        assert not real_errors, f"console errors: {real_errors}"
        browser.close()

    print("browser_shift_edit_assistant: all checks passed")


if __name__ == "__main__":
    main()
