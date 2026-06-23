"""Browser smoke for conversation branching — fork / cycle / delete / edit-leak.

100% TOKEN-FREE: it seeds a 2-turn transcript via /api/state (the on-load
reconcile folds it into the conversation tree), then exercises SHIFT+CLICK edit
(fork + copy the downstream conversation, no generation), ‹k/N› cycling, delete
(prune a branch), and the edit-leak guard (cycling to a sibling under an open
editor must drop the draft) — none of which call the model.

Oracle: the DOM (active path + the .branch-cycle control) plus GET
/api/conversations (the persisted tree). The active path also round-trips through
GET /api/state.messages.

  uv run python tests/small-smokes/browser_branching.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

SEED = {
    "mode": "single",
    "messages": [
        {"role": "user", "content": "U1 original question"},
        {"role": "assistant", "content": "A1 original answer"},
    ],
    "compare_messages": [],
}
DRAFT = "LEAKED DRAFT MUST NOT LAND"


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body, method="POST"):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method=method)
    return urllib.request.urlopen(req, timeout=10).read()


def _clean_conversations():
    for c in _get("/api/conversations"):
        urllib.request.urlopen(
            urllib.request.Request(f"{BASE}/api/conversations/{c['id']}", method="DELETE"),
            timeout=10,
        ).read()


def main():
    _clean_conversations()
    _post("/api/state", SEED)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="load", timeout=20000)
        # The seeded transcript is folded into the tree on load and rendered.
        page.wait_for_function("document.body.innerText.includes('A1 original answer')", timeout=15000)
        assert page.locator(".message").count() == 2, "seed should render 2 turns"

        # ── 1. SHIFT+CLICK edit on the user turn = fork + copy downstream, NO gen ──
        page.locator(".message").nth(0).get_by_role("button", name="Edit").click(modifiers=["Shift"])
        ta = page.locator("textarea.edit-textarea")
        ta.wait_for(timeout=4000)
        ta.fill("U1 EDITED question")
        page.locator("button.btn-edit-save").click()
        # The downstream answer was COPIED (no generation), and the first message
        # now has a sibling → a ‹k/N› control reading 2.
        page.wait_for_function("document.body.innerText.includes('U1 EDITED question')", timeout=8000)
        page.wait_for_function(
            "!!document.querySelector('[data-testid=branch-cycle]')", timeout=5000)
        assert page.locator(".message").count() == 2, "forked branch keeps the copied answer"
        assert "A1 original answer" in page.inner_text("body"), "downstream answer was copied"
        cyc = page.locator("[data-testid=branch-cycle]").first.inner_text()
        assert "/2" in cyc, f"expected 2 root branches, cycle shows {cyc!r}"
        print("fork+copy (shift-edit): OK —", cyc.replace("\n", ""))

        # ── 2. CYCLE the first message back to the original branch ──
        page.locator(".message").nth(0).get_by_role("button", name="Previous branch").click()
        page.wait_for_function("document.body.innerText.includes('U1 original question')", timeout=5000)
        assert "U1 EDITED question" not in page.inner_text("body"), "cycle prev should show the original"
        print("cycle ‹k/N›: OK (active branch toggled, path re-derived)")

        # ── 3. EDIT-LEAK: open an editor on the (original) first message, then
        #        cycle to the sibling → the editor must drop its draft. ──
        page.locator(".message").nth(0).get_by_role("button", name="Edit").first.click()
        ta = page.locator("textarea.edit-textarea")
        ta.wait_for(timeout=4000)
        ta.fill(DRAFT)
        assert page.locator("textarea.edit-textarea").count() == 1
        page.locator(".message").nth(0).get_by_role("button", name="Next branch").click()
        page.wait_for_function("document.body.innerText.includes('U1 EDITED question')", timeout=5000)
        n_editors = page.locator("textarea.edit-textarea").count()
        assert n_editors == 0, f"editor leaked open across a cycle ({n_editors}) — nodeId guard regressed"
        print("edit-leak guard: OK (editor closed on sibling cycle)")

        # ── 4. DELETE the edited branch (now active) → prune it, fall back to one root ──
        page.locator(".message").nth(0).get_by_role("button", name="Delete").click()
        page.wait_for_function("document.body.innerText.includes('U1 original question')", timeout=5000)
        page.wait_for_timeout(300)
        assert page.locator("[data-testid=branch-cycle]").count() == 0, "one root left → no cycler"
        assert "U1 EDITED question" not in page.inner_text("body"), "edited branch pruned"
        print("delete (prune branch): OK")

        # ── 5. Oracles: persisted tree + active-path round-trip + no console errors ──
        page.wait_for_timeout(600)  # let the debounced save flush
        convs = _get("/api/conversations")
        assert len(convs) == 1, f"expected 1 conversation, got {len(convs)}"
        tree = convs[0]["tree"]
        contents = [n["content"] for n in tree["nodes"].values()]
        assert "U1 EDITED question" not in contents, "pruned node still on disk"
        assert "U1 original question" in contents and "A1 original answer" in contents
        st = _get("/api/state")
        bodies = [m["content"] for m in st["messages"]]
        assert bodies == ["U1 original question", "A1 original answer"], bodies
        assert DRAFT not in json.dumps(tree), "leaked draft persisted"
        assert not errors, f"console/page errors: {errors}"

        browser.close()
        print("messages oracle:", bodies)
        print("BRANCHING SMOKE PASS")


if __name__ == "__main__":
    main()
