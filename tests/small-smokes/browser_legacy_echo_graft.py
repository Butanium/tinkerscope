"""Legacy echo-graft regression smoke — the layout-less-open echo-clear fix.

THE BUG (fixed in `conversations.svelte.ts` #loadTrees, storage-v2 era): opening
a conversation with NO stored panel layout (a migrated legacy {tree,
compare_tree} conv, or a bare API-created one) kept the PREVIOUS conversation's
transcript echoes on the state bus — the layout-less branch never sent the
`messages: []` reset a layout-carrying open does. #afterLoad (whose contract is
"echoes are cleared before it runs") then reconciled those FOREIGN turns into
the freshly-loaded trees, and the next save persisted the graft durably.

This smoke POISONS both panel echoes with a marker transcript, opens a legacy
conversation, makes one structural edit (shift-edit fork, 0 tokens), and
asserts: the marker never renders, the open cleared the echoes server-side,
nothing foreign persisted, and the compare panel survives byte-identical.

REPRO SUBTLETIES (each cost a false result once — do not simplify them away):
  - The poisoned panels MUST carry a run_id. With run_id null everywhere, the
    browser's boot-time restoreSession treats server state as fresh and
    REPLACES the panels (echoes cleared) before load() runs — the graft
    precondition silently evaporates and an UNFIXED build looks green.
  - The legacy fixture CONVERTS to `trees` on its first structural save, so
    this smoke works once per dev-isolated instance; it SKIPs (exit 0) with an
    explanation on a converted fixture — restart dev-isolated to re-snapshot.
  - Point at an ISOLATED instance only (scripts/dev-isolated.sh): the smoke
    mutates the legacy conversation.

  uv run python tests/small-smokes/browser_legacy_echo_graft.py [BASE_URL]
"""
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8917"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
FOREIGN_U = "FOREIGN-ECHO-USER-TURN do not graft me"
FOREIGN_A = "FOREIGN-ECHO-ASSISTANT-TURN do not graft me"


def api(method, path, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def content_multiset(tree):
    return Counter((n["role"], n["content"]) for n in (tree or {}).get("nodes", {}).values())


def find_legacy_conv():
    """First conversation whose body is still legacy-shaped ({tree,...}, no trees)."""
    for c in api("GET", "/api/conversations"):
        body = api("GET", f"/api/conversations/{c['id']}")
        if "trees" not in body and body.get("tree"):
            return body
    return None


def main() -> int:
    pre = find_legacy_conv()
    if pre is None:
        print("SKIP: no legacy {tree, compare_tree} conversation in this instance's "
              "state (already converted by a prior run? restart dev-isolated to "
              "re-snapshot, or run against a snapshot that still has one)")
        return 0
    legacy_id = pre["id"]
    pre_primary = content_multiset(pre["tree"])
    pre_compare = content_multiset(pre.get("compare_tree"))
    pre_compare_roots = len((pre.get("compare_tree") or {}).get("rootChildren", []))
    print(f"legacy fixture {legacy_id[:8]}: primary={sum(pre_primary.values())} nodes, "
          f"compare={sum(pre_compare.values())} nodes / {pre_compare_roots} root(s)")

    # ── POISON: both panel echoes carry a foreign transcript (run_id SET — see
    # the docstring; null run_ids let restoreSession clear the poison pre-load).
    api("POST", "/api/state", {"panels": [
        {"id": "primary", "run_id": "openrouter:openrouter/free", "checkpoint": None,
         "messages": [{"role": "user", "content": FOREIGN_U},
                      {"role": "assistant", "content": FOREIGN_A}]},
        {"id": "compare", "run_id": "openrouter:openrouter/free", "checkpoint": None,
         "messages": [{"role": "user", "content": FOREIGN_U},
                      {"role": "assistant", "content": FOREIGN_A}]},
    ]})
    assert any(p["messages"] for p in api("GET", "/api/state")["panels"]), "poison did not take"

    checks: list[tuple[str, bool]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/?c={legacy_id}", wait_until="load", timeout=20000)
        page.wait_for_selector(".message", timeout=20000)
        page.wait_for_timeout(800)  # let the load-time patch + any (wrong) graft settle
        checks.append(("foreign echo does not render in the workspace",
                       FOREIGN_U not in page.inner_text("body")))
        # one structural edit → the debounced save (would persist a graft)
        page.locator(".message").nth(0).get_by_role("button", name="Edit").click(modifiers=["Shift"])
        ta = page.locator("textarea.edit-textarea")
        ta.wait_for(timeout=5000)
        ta.fill("echo-graft smoke probe (edited fork)")
        page.locator("button.btn-edit-save").click()
        page.wait_for_timeout(2000)
        checks.append((f"no page errors ({len(errors)})", not errors))
        if errors:
            print("page errors:", errors[:5])
        browser.close()

    # the layout-less open itself must have cleared the echoes server-side
    leftover = [p["id"] for p in api("GET", "/api/state")["panels"]
                if any(m.get("content", "").startswith("FOREIGN-ECHO") for m in p["messages"])]
    checks.append((f"foreign echoes cleared from the bus ({leftover})", not leftover))

    post = api("GET", f"/api/conversations/{legacy_id}")
    trees = post.get("trees") or {}
    all_contents = [c for t in trees.values() for (_, c) in content_multiset(t)]
    checks.append(("no foreign turn persisted into ANY tree",
                   not any(c.startswith("FOREIGN-ECHO") for c in all_contents)))
    checks.append((f"both panels present after first save ({sorted(trees)})",
                   sorted(trees) == ["compare", "primary"]))
    post_compare_roots = len((trees.get("compare") or {}).get("rootChildren", []))
    checks.append(("compare content EXACTLY preserved (no loss, no duplication)",
                   content_multiset(trees.get("compare")) == pre_compare))
    checks.append((f"compare roots unchanged ({pre_compare_roots} → {post_compare_roots})",
                   post_compare_roots == pre_compare_roots))
    missing = pre_primary - content_multiset(trees.get("primary"))
    checks.append((f"primary kept every original node (missing: {sum(missing.values())})",
                   not missing))

    ok = all(c for _, c in checks)
    for name, passed in checks:
        print(("PASS " if passed else "FAIL ") + name)
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
