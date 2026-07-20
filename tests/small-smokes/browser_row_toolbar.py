"""Browser smoke for the row toolbar — bounded inline set + the ⋯ overflow menu.

100% TOKEN-FREE: seeds a 2-panel workspace with ready-made branch trees via
POST /api/conversations, opens it with ?c=<id> at a viewport narrow enough
that the columns sit at their 280px min-width, then checks:

  1. committed rows keep the core actions INLINE (regen / continue / edit /
     delete / tag) and no row toolbar overflows its bubble at min panel width;
  2. the ⋯ menu holds the long tail (copy message / conversation, raw toggle,
     send-branch→panel, copy node id) and closes on outside-click / Escape;
  3. "Copy node id" puts the row's EXACT tree-node id on the clipboard — the
     CLI's addressing currency (`tinkpg samples/continue --node <id>`); the
     clipboard is monkeypatched so the assert is deterministic and headless-safe.

No model calls. The n>1 sample-card kebab (same ActionMenu component) is
covered by browser_n_samples.py, which really samples.

  uv run python tests/small-smokes/browser_row_toolbar.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

PAD = "lorem ipsum dolor sit amet " * 6


def seed_tree(prefix):
    """u0 → a1 (with raw_text) → u2 → [a3A | a3B]: an edit/regen-able user row,
    an assistant row with siblings (cycler) + raw output, all with known ids."""
    nodes = {
        f"{prefix}u0": {"id": f"{prefix}u0", "role": "user", "content": f"{prefix} QUESTION-0\n\n{PAD}",
                        "parent": None, "children": [f"{prefix}a1"]},
        f"{prefix}a1": {"id": f"{prefix}a1", "role": "assistant", "content": f"{prefix} ANSWER-1\n\n{PAD}",
                        "raw_text": f"<raw>{prefix} ANSWER-1</raw>",
                        "parent": f"{prefix}u0", "children": [f"{prefix}u2"]},
        f"{prefix}u2": {"id": f"{prefix}u2", "role": "user", "content": f"{prefix} QUESTION-2\n\n{PAD}",
                        "parent": f"{prefix}a1", "children": [f"{prefix}a3a", f"{prefix}a3b"]},
        f"{prefix}a3a": {"id": f"{prefix}a3a", "role": "assistant", "content": f"{prefix} ANSWER-3-A\n\n{PAD}",
                         "parent": f"{prefix}u2", "children": []},
        f"{prefix}a3b": {"id": f"{prefix}a3b", "role": "assistant", "content": f"{prefix} ANSWER-3-B\n\n{PAD}",
                         "parent": f"{prefix}u2", "children": []},
    }
    return {"nodes": nodes, "rootChildren": [f"{prefix}u0"], "selected": {f"{prefix}u2": f"{prefix}a3a"}}


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


# Every rendered toolbar/bubble must fit its box: any horizontal overflow at
# min panel width is exactly the bug this redesign removes.
OVERFLOW_PROBE = """() => {
  const bad = [];
  for (const el of document.querySelectorAll('.message, .message-actions, .sample-card')) {
    if (el.scrollWidth > el.clientWidth + 1)
      bad.push(`${el.className}: ${el.scrollWidth}>${el.clientWidth}`);
  }
  return bad;
}"""


def main():
    # A REAL run id for both panels: the conversation-open self-heal drops panels
    # whose run_id is null (the phantom-panel fix), which would silently collapse
    # the seeded compare panel — and with it the send-to menu items under test.
    runs = _get("/api/models")
    assert runs, "isolated instance discovered no runs — seed needs a scan root with runs"
    rid = runs[0]["id"]
    conv = _post("/api/conversations", {
        "name": "row toolbar smoke",
        "trees": {"primary": seed_tree("P"), "compare": seed_tree("C")},
        "panels": [
            {"id": "primary", "run_id": rid, "checkpoint": None},
            {"id": "compare", "run_id": rid, "checkpoint": None},
        ],
    })

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        # ~840px: sidebar + 2 columns pins the columns at/near their 280px floor.
        page = browser.new_page(viewport={"width": 840, "height": 900})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        # Deterministic clipboard: capture writes instead of relying on headless
        # clipboard permissions.
        page.add_init_script(
            "Object.defineProperty(navigator, 'clipboard', {value: {writeText: (t) => "
            "{ window.__copied = t; return Promise.resolve(); }}, configurable: true});")
        page.goto(f"{BASE}/?c={conv['id']}", wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('ANSWER-3-A')", timeout=15000)

        panel = page.locator(".chat-column").first
        asst_row = panel.locator(".message").nth(1)  # a1: assistant with raw_text
        user_row = panel.locator(".message").nth(0)  # u0: user

        # ── 1. inline core set (assistant row) + no inline copy button ──
        for name in ("Regenerate", "Continue this message", "Edit", "Delete", "More actions"):
            assert asst_row.get_by_role("button", name=name, exact=True).count() == 1, \
                f"assistant row should keep '{name}' inline"
        assert asst_row.locator(".btn-tag").count() == 1, "assistant row keeps the bookmark inline"
        assert asst_row.get_by_role("button", name="Copy this message").count() == 0, \
            "copy moved into the ⋯ menu"
        for name in ("Regenerate", "Edit", "Delete", "More actions"):
            assert user_row.get_by_role("button", name=name, exact=True).count() == 1, \
                f"user row should keep '{name}' inline"
        assert user_row.get_by_role("button", name="Continue this message").count() == 0

        # ── 2. nothing overflows at min panel width ──
        bad = page.evaluate(OVERFLOW_PROBE)
        assert not bad, f"horizontal overflow at min panel width: {bad}"

        # ── 3. the ⋯ menu: expected items, send-to absorbed, node id shown ──
        asst_row.locator("[data-testid=row-menu]").click()
        menu = page.locator("[data-testid=row-menu-panel]")
        menu.wait_for(timeout=4000)
        items = menu.locator(".row-menu-item").all_inner_texts()
        labels = " | ".join(items)
        for expect in ("Copy message", "Copy conversation", "Show raw output", "Send branch →", "Copy node id"):
            assert any(expect in it for it in items), f"menu should offer '{expect}': {labels}"
        assert any("Pa1" in it for it in items), f"node id Pa1 should be visible in the menu: {labels}"

        # ── 4. Copy node id → the exact tree-node id lands on the clipboard ──
        menu.locator("[data-testid=copy-node-id]").click()
        copied = page.evaluate("() => window.__copied")
        assert copied == "Pa1", f"copied reference should be the bare node id 'Pa1', got {copied!r}"
        assert "✓ copied" in menu.inner_text(), "copy item should flash confirmation"
        page.wait_for_function("!document.querySelector('[data-testid=row-menu-panel]')", timeout=4000)

        # ── 5. close behaviors: Escape and outside-click ──
        asst_row.locator("[data-testid=row-menu]").click()
        menu.wait_for(timeout=4000)
        page.keyboard.press("Escape")
        page.wait_for_function("!document.querySelector('[data-testid=row-menu-panel]')", timeout=4000)
        asst_row.locator("[data-testid=row-menu]").click()
        menu.wait_for(timeout=4000)
        page.locator(".input-bar").click()
        page.wait_for_function("!document.querySelector('[data-testid=row-menu-panel]')", timeout=4000)

        # ── 6. raw toggle via the menu still works ──
        asst_row.locator("[data-testid=row-menu]").click()
        menu.wait_for(timeout=4000)
        menu.locator(".row-menu-item", has_text="Show raw output").click()
        asst_row.locator("pre.raw-text-view").wait_for(timeout=4000)

        assert not errors, f"console errors: {errors}"
        browser.close()

    urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/api/conversations/{conv['id']}", method="DELETE"),
        timeout=10).read()

    print("browser_row_toolbar: OK — inline core fits at min width, ⋯ menu items, copy node id, close behaviors")


if __name__ == "__main__":
    main()
