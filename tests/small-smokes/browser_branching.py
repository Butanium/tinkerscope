"""Browser smoke for workspace branching — fork / cycle / delete / edit-leak.

100% TOKEN-FREE: seeds a 2-turn branch tree via POST /api/workspaces (the
modern panels[] API — one tree per panel), opens it with ?w=<id>, then exercises
SHIFT+CLICK edit (fork + copy the downstream workspace, no generation), ‹k/N›
cycling, delete (prune a branch), and the edit-leak guard (cycling to a sibling
under an open editor must drop the draft) — none of which call the model.

Oracle: the DOM (active path + the .branch-cycle control) plus GET
/api/workspaces (the persisted per-panel tree, `trees.primary`) — the prune is
round-tripped as the persisted tree's SHAPE. It deliberately no longer asserts
GET /api/state.panels[0].messages; see the note at that oracle.

Non-destructive: creates its own workspace and deletes only that one, so it's
safe against an instance that already has (fixture) workspaces.

  uv run python tests/small-smokes/browser_branching.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from _console import attach

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

DRAFT = "LEAKED DRAFT MUST NOT LAND"


def seed_tree():
    """u0 -> a1 (linear, 2 rows)."""
    nodes = {
        "s0": {"id": "s0", "role": "user", "content": "U1 original question",
               "parent": None, "children": ["s1"]},
        "s1": {"id": "s1", "role": "assistant", "content": "A1 original answer",
               "parent": "s0", "children": []},
    }
    return {"nodes": nodes, "rootChildren": ["s0"], "selected": {}}


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def main():
    # Seed a fresh workspace with the branch tree (panel model unset — none of
    # these ops call the model, so run_id can be null). Take the primary panel id
    # from shared state so the seeded panel matches what the UI shows.
    primary = _get("/api/state")["panels"][0]
    conv = _post("/api/workspaces", {
        "name": "branching smoke",
        "trees": {"primary": seed_tree()},
        "panels": [{"id": "primary", "run_id": primary.get("run_id"),
                    "checkpoint": primary.get("checkpoint")}],
    })

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors = []
        net = attach(page, errors)  # noqa: F841 — evidence for a console failure
        page.goto(f"{BASE}/?w={conv['id']}", wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('A1 original answer')", timeout=15000)
        assert page.locator(".message").count() == 2, "seed should render 2 turns"

        # ── 1. SHIFT+CLICK edit on the user turn = fork + copy downstream, NO gen ──
        page.locator(".message").nth(0).get_by_role("button", name="Edit").click(modifiers=["Shift"])
        # Scoped to the CONTENT editor: an open editor also renders the thread-system
        # textarea (and, on an assistant turn with CoT, a thinking one), all three
        # sharing .edit-textarea — the bare selector is a strict-mode violation and
        # is what made this smoke "stale" rather than any product change.
        ta = page.locator("textarea.edit-textarea:not(.edit-system):not(.edit-reasoning)")
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
        ta = page.locator("textarea.edit-textarea:not(.edit-system):not(.edit-reasoning)")
        ta.wait_for(timeout=4000)
        ta.fill(DRAFT)
        assert page.locator("textarea.edit-textarea:not(.edit-system):not(.edit-reasoning)").count() == 1
        page.locator(".message").nth(0).get_by_role("button", name="Next branch").click()
        page.wait_for_function("document.body.innerText.includes('U1 EDITED question')", timeout=5000)
        n_editors = page.locator("textarea.edit-textarea:not(.edit-system):not(.edit-reasoning)").count()
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
        ours = _get(f"/api/workspaces/{conv['id']}")  # v2: list is summaries-only
        tree = ours["trees"]["primary"]
        contents = [n["content"] for n in tree["nodes"].values()]
        assert "U1 EDITED question" not in contents, "pruned node still on disk"
        assert "U1 original question" in contents and "A1 original answer" in contents
        # Round-trip the prune from the DURABLE, workspace-scoped side: after the
        # delete the tree is a single linear chain, so assert that SHAPE.
        #
        # Two things this deliberately does NOT do, both learned 2026-08-12:
        #  - it does not read /api/state's panel echo (what it used to do). The bus
        #    describes exactly ONE workspace at a time (api/state.py), so in a full
        #    sweep it may be another smoke's and the comparison returns a foreign
        #    transcript. That only ever passed standalone; the strict-mode repair
        #    was what first let this smoke survive far enough to expose it.
        #  - it does not walk `selected` as a chain. With one root and one child
        #    there is no sibling choice to record, so `selected` is legitimately
        #    empty and lib/tree.ts's selectedChildId falls back to the LAST child;
        #    walking it alone yields [].
        assert len(tree["rootChildren"]) == 1, tree["rootChildren"]
        root_id = tree["rootChildren"][0]
        assert tree["nodes"][root_id]["content"] == "U1 original question", tree["nodes"][root_id]
        kids = tree["nodes"][root_id]["children"]
        assert len(kids) == 1, kids
        assert tree["nodes"][kids[0]]["content"] == "A1 original answer", tree["nodes"][kids[0]]
        # The /api/state panel echo is deliberately NOT asserted here any more.
        # Under P1 it is a CLI-visible echo that tree-only mutations do not write
        # (see CLAUDE.md on `live.state.panels`), so after any earlier smoke fires
        # a real generation the bus keeps ITS messages while our page restamps
        # `workspace_id` to ours — the read comes back stamped-us / echoing-them.
        # Reproduced 2026-08-12 in three consecutive sweeps and reported as a P1
        # follow-up; asserting it from here would just pin someone else's turns.
        # The durable oracle above is the one that means something.
        print("persisted oracle: pruned tree shape OK (echo deliberately not asserted)")
        assert DRAFT not in json.dumps(tree), "leaked draft persisted"
        assert not errors, f"console/page errors: {errors}"

        browser.close()

    # cleanup: remove only the seeded workspace
    urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/api/workspaces/{conv['id']}", method="DELETE"),
        timeout=10).read()
    print("BRANCHING SMOKE PASS")


if __name__ == "__main__":
    main()
