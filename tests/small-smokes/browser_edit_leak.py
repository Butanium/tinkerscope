"""Regression test for the edit-mode-leak bug (found by the chat-refactor review):
the message list is positionally keyed and edit state is local to ChatMessage, so
reshaping the transcript while an editor is open could hand that instance a
DIFFERENT message and let Save write the stale draft onto the wrong row.

The fix is a $effect in ChatMessage that drops in-progress edit/raw state when the
bound message changes. This test opens an editor, deletes an earlier row, and
asserts the editor auto-closed and the stale draft never landed anywhere.

  uv run python tests/small-smokes/browser_edit_leak.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
DRAFT = "STALE DRAFT SHOULD NOT LAND"

STATE = {
    "mode": "single",
    "messages": [
        {"role": "user", "content": "U1 first user turn"},
        {"role": "assistant", "content": "A1 assistant being edited"},
        {"role": "user", "content": "U2 second user turn"},
        {"role": "assistant", "content": "A2 last assistant"},
    ],
    "compare_messages": [],
}


def post_state(state):
    req = urllib.request.Request(
        f"{BASE}/api/state", data=json.dumps(state).encode(),
        headers={"content-type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=10).read()


def main():
    post_state(STATE)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('A1 assistant being edited')", timeout=15000)

        # Open the editor on A1 (the 2nd message) and type a draft — but DON'T save.
        page.locator(".message").nth(1).get_by_role("button", name="Edit").click()
        ta = page.locator("textarea.edit-textarea")
        ta.wait_for(timeout=4000)
        ta.fill(DRAFT)
        assert page.locator("textarea.edit-textarea").count() == 1, "editor did not open"

        # Reshape the transcript underneath the open editor: delete U1 (1st message).
        page.locator(".message").nth(0).get_by_role("button", name="Delete").click()
        page.wait_for_function("document.querySelectorAll('.message').length === 3", timeout=5000)
        page.wait_for_timeout(300)

        # FIX assertion: the editor auto-closed (no orphaned textarea over a wrong row).
        n_editors = page.locator("textarea.edit-textarea").count()
        assert n_editors == 0, f"editor leaked open after reshape ({n_editors} textareas) — fix regressed"

        # And the stale draft must not have been written anywhere.
        st = json.load(urllib.request.urlopen(f"{BASE}/api/state", timeout=10))
        bodies = [m["content"] for m in st["messages"]]
        assert len(st["messages"]) == 3, st["messages"]
        assert all(DRAFT not in b for b in bodies), f"stale draft leaked into transcript: {bodies}"
        print("messages after:", bodies)
        print("edit-leak fix: OK (editor closed on reshape, no misdirected write)")

        browser.close()
        print("EDIT-LEAK SMOKE PASS")


if __name__ == "__main__":
    main()
