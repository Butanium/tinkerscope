"""Browser smoke for the UNDO stack — the sidebar ↺ button + Ctrl/⌘+Z.

100% TOKEN-FREE: seeds a 2-panel workspace via POST /api/workspaces (one user
root + one assistant reply per panel), opens it with ?w=<id>, then:

  1. the button starts DISABLED — a fresh workspace has nothing to undo;
  2. delete an assistant branch → the reply is gone and the button enables,
     its tooltip naming the op;
  3. clicking undo puts the branch back, and the button disables again;
  4. Ctrl+Z does the same thing (the button is only the discoverable twin);
  5. a Ctrl-click delete (all panels) is ONE undo, not one per panel;
  6. Ctrl+Z inside the composer does NOT reach the workspace — a textarea keeps
     the browser's native text undo.

No model calls.

  uv run python tests/small-smokes/browser_undo.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

# Any non-null run_id: the load-time phantom-panel self-heal DROPS panels with
# run_id == null, which would collapse the 2-panel seed to one.
FREE = "openrouter:openrouter/free"


def seed_tree(tag):
    """user root + one assistant child, both carrying `tag` so panels are told apart."""
    uid, aid = f"u-{tag}", f"a-{tag}"
    return {
        "nodes": {
            uid: {"id": uid, "role": "user", "content": f"ASK-{tag}",
                  "parent": None, "children": [aid]},
            aid: {"id": aid, "role": "assistant", "content": f"REPLY-{tag}",
                  "parent": uid, "children": []},
        },
        "rootChildren": [uid],
        "selected": {"__root__": uid},
    }


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def undo_btn(page):
    return page.locator("button[aria-label='Undo last delete']")


def body_has(page, text):
    return text in page.inner_text("body")


def delete_row(page, panel_index, modifiers=None):
    """Click the delete button on the assistant row of one panel, expanding the
    row's overflow menu first if the toolbar folded it away."""
    panel = page.locator(".chat-column").nth(panel_index)
    row = panel.locator(".message").nth(1)  # 0 = the user turn
    btn = row.locator("[data-testid=delete-msg]")
    if not btn.is_visible():
        row.locator("[data-testid=acts-toggle]").click()
    btn.click(modifiers=modifiers or [])


def main():
    conv = _post("/api/workspaces", {
        "name": "undo smoke",
        "trees": {"primary": seed_tree("P0"), "compare": seed_tree("P1")},
        "panels": [
            {"id": "primary", "run_id": FREE, "checkpoint": None},
            {"id": "compare", "run_id": FREE, "checkpoint": None},
        ],
        "seen_panels": ["primary", "compare"],
    })

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/?w={conv['id']}", wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('REPLY-P0')", timeout=15000)

        # ── 1. nothing to undo yet ──
        assert undo_btn(page).count() == 1, "undo button should render"
        assert undo_btn(page).is_disabled(), "a fresh workspace has nothing to undo"

        # ── 2. delete enables it, and names the op ──
        delete_row(page, 0)
        page.wait_for_function(
            "!document.body.innerText.includes('REPLY-P0')", timeout=5000)
        assert body_has(page, "REPLY-P1"), "the other panel must be untouched"
        assert not undo_btn(page).is_disabled(), "a delete should enable undo"
        tip = undo_btn(page).get_attribute("data-tooltip") or ""
        assert "delete branch" in tip, f"tooltip should name the op: {tip!r}"

        # ── 3. the button puts it back ──
        undo_btn(page).click()
        page.wait_for_function(
            "document.body.innerText.includes('REPLY-P0')", timeout=5000)
        assert undo_btn(page).is_disabled(), "stack should be empty again"

        # ── 4. Ctrl+Z does the same ──
        delete_row(page, 0)
        page.wait_for_function(
            "!document.body.innerText.includes('REPLY-P0')", timeout=5000)
        page.keyboard.press("Control+z")
        page.wait_for_function(
            "document.body.innerText.includes('REPLY-P0')", timeout=5000)
        assert undo_btn(page).is_disabled(), "Ctrl+Z should consume the entry"

        # ── 5. a cross-panel delete is ONE undo ──
        delete_row(page, 0, modifiers=["Control"])
        page.wait_for_function(
            "!document.body.innerText.includes('REPLY-P0') && "
            "!document.body.innerText.includes('REPLY-P1')", timeout=5000)
        undo_btn(page).click()
        page.wait_for_function(
            "document.body.innerText.includes('REPLY-P0') && "
            "document.body.innerText.includes('REPLY-P1')", timeout=5000)
        assert undo_btn(page).is_disabled(), \
            "a cross-panel delete must be ONE entry, not one per panel"

        # ── 6. a textarea keeps its own Ctrl+Z ──
        delete_row(page, 0)
        page.wait_for_function(
            "!document.body.innerText.includes('REPLY-P0')", timeout=5000)
        composer = page.locator("textarea").first
        composer.click()
        composer.type("typed into the composer")
        page.keyboard.press("Control+z")
        assert not body_has(page, "REPLY-P0"), \
            "Ctrl+Z in a text box must not reach the workspace undo"
        assert not undo_btn(page).is_disabled(), "the entry should still be there"
        undo_btn(page).click()  # and the button still works
        page.wait_for_function(
            "document.body.innerText.includes('REPLY-P0')", timeout=5000)

        assert not errors, f"console errors: {errors}"
        browser.close()
    print("browser_undo: OK")


if __name__ == "__main__":
    main()
