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
     the browser's native text undo;
  7. undo after NEWER work landed (a CLI-style fold via the ops route) puts the
     deleted branch back AND keeps the newer node — on the server too. Undo used
     to ship the pre-delete snapshot as a whole-panel replace_tree, deleting it.

No model calls.

  uv run python tests/small-smokes/browser_undo.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from _console import attach

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
    """Click the delete button on the assistant row of one panel, unfolding the
    row's overflow menu first if the toolbar folded it away.

    RE-SAMPLING loop, not a one-shot is_visible() branch: right after a
    re-render (undo restore) OverflowRow re-measures, and the toolbar can flip
    folded→fits in the microseconds between a check and the click — a committed
    branch then waits 30s on a toggle that ended up acts-toggle-hidden while
    the delete button sits visible next to it (bit three times on a loaded box,
    2026-08-12; the transition window is invisible on an idle one)."""
    panel = page.locator(".chat-column").nth(panel_index)
    row = panel.locator(".message").nth(1)  # 0 = the user turn
    btn = row.locator("[data-testid=delete-msg]")
    deadline = time.time() + 6
    last = None
    while time.time() < deadline:
        try:
            if btn.is_visible():
                btn.click(modifiers=modifiers or [], timeout=1500)
                return
            tog = row.locator("[data-testid=acts-toggle]:not(.acts-toggle-hidden)")
            if tog.count() and tog.is_visible():
                tog.click(timeout=1500)  # unfold; next pass clicks the delete
        except Exception as e:  # transition landed mid-click — re-sample
            last = e
        page.wait_for_timeout(150)
    dump = page.evaluate("""(idx) => {
      const col = document.querySelectorAll('.chat-column')[idx];
      return [...col.querySelectorAll('.message')].map(r => ({
        txt: r.innerText.slice(0, 40),
        toolbar: [...r.querySelectorAll('[data-testid]')].map(b => {
          const rc = b.getBoundingClientRect();
          return b.dataset.testid + (rc.width && rc.height ? ':vis' : ':hid');
        }).join(' ')
      }));
    }""", panel_index)
    raise AssertionError(f"delete button never became clickable: {last}\nrows: {dump}")


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
        net = attach(page, errors)  # noqa: F841 — evidence for a console failure
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

        # ── 7. undo keeps work that landed after the delete ──
        delete_row(page, 0)
        page.wait_for_function(
            "!document.body.innerText.includes('REPLY-P0')", timeout=5000)
        _post(f"/api/workspaces/{conv['id']}/ops", {"ops": [{
            "op": "add_nodes", "panel": "primary", "select": True,
            "nodes": [{"id": "late-P0", "role": "assistant", "content": "LATE-P0",
                       "parent": "u-P0"}],
        }]})
        page.wait_for_function(
            "document.body.innerText.includes('LATE-P0')", timeout=5000)
        undo_btn(page).click()
        page.wait_for_function(
            "document.body.innerText.includes('REPLY-P0')", timeout=5000)
        nodes = {}
        deadline = time.time() + 6
        while time.time() < deadline:
            got = urllib.request.urlopen(f"{BASE}/api/workspaces/{conv['id']}", timeout=10)
            nodes = json.loads(got.read())["trees"]["primary"]["nodes"]
            if "a-P0" in nodes:
                break
            time.sleep(0.2)
        assert "a-P0" in nodes, f"the undo never reached the server: {sorted(nodes)}"
        assert "late-P0" in nodes, f"undo deleted the newer node on the server: {sorted(nodes)}"
        assert nodes["u-P0"]["children"] == ["a-P0", "late-P0"], nodes["u-P0"]["children"]

        assert not errors, f"console errors: {errors}"
        browser.close()
    print("browser_undo: OK")


if __name__ == "__main__":
    main()
