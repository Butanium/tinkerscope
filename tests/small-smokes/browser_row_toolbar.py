"""Browser smoke for the row toolbar — adaptive fold + unfold-below (OverflowRow).

100% TOKEN-FREE: seeds a 2-panel workspace with ready-made branch trees via
POST /api/conversations, opens it with ?c=<id> at a viewport narrow enough
that the columns sit at their 280px min-width, then checks the fold behavior:

  1. a narrow assistant row (10 actions) FOLDS: the tail buttons wrap to
     clipped lines, a chevron toggle appears, nothing overflows horizontally
     and nothing below the first button line is visible;
  2. expanding reveals the tail BELOW as real tool buttons (1+ extra lines) —
     among them "Copy node id", whose click puts the row's EXACT tree-node id
     on the clipboard (the CLI's `--node` addressing currency; the clipboard is
     monkeypatched so the assert is deterministic and headless-safe) — and the
     send-branch-to-panel popover still opens/closes;
  3. at a WIDE viewport everything fits on one line and the toggle disappears
     (fold is adaptive, not a fixed split).

No model calls. The n>1 sample-card row (same OverflowRow) is covered by
browser_n_samples.py, which really samples.

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


# One toolbar's fold state, measured off the DOM. Line-1 membership = starts
# above the first child's bottom (the same rule OverflowRow uses — a plain
# offsetTop comparison would misread center-aligned shorter buttons as line 2).
ROW_STATE = """(row) => {
  const wrap = row.querySelector('.acts-wrap');
  const kids = [...wrap.children];
  const lineBottom = kids[0].offsetTop + kids[0].offsetHeight;
  const firstLine = kids.filter((k) => k.offsetTop < lineBottom);
  return {
    folded: wrap.classList.contains('folded'),
    wrapH: wrap.clientHeight,
    rowH: Math.max(...firstLine.map((k) => k.offsetHeight)),
    buttons: kids.length,
    beyond: kids.filter((k) => k.offsetTop >= lineBottom)
                .map((k) => k.getAttribute('aria-label') || (k.textContent || '').trim()),
    hasToggle: !!row.querySelector('[data-testid=acts-toggle]'),
  };
}"""

# Nothing may bleed horizontally, folded or expanded.
OVERFLOW_PROBE = """() => {
  const bad = [];
  for (const el of document.querySelectorAll('.message, .message-actions, .acts-wrap, .sample-card')) {
    if (el.scrollWidth > el.clientWidth + 1)
      bad.push(`${el.className}: ${el.scrollWidth}>${el.clientWidth}`);
  }
  return bad;
}"""


def main():
    # A REAL run id for both panels: the conversation-open self-heal drops panels
    # whose run_id is null (the phantom-panel fix), which would silently collapse
    # the seeded compare panel — and with it the send-to picker under test.
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

        # ── 1. narrow ⇒ folded: toggle up, tail clipped, only line 1 visible ──
        s = asst_row.evaluate(ROW_STATE)
        assert s["hasToggle"], f"narrow assistant row should show the fold toggle: {s}"
        assert s["folded"], f"fold is the default: {s}"
        assert s["beyond"], f"a narrow row must have wrapped (hidden) buttons: {s}"
        assert s["wrapH"] < 2 * s["rowH"], f"folded wrap must clip to one button line: {s}"
        assert "Copy node id" in s["beyond"], f"copy-node-id folds first (lowest priority): {s}"
        for name in ("Regenerate", "Continue this message", "Edit"):
            assert asst_row.get_by_role("button", name=name, exact=True).count() == 1
            assert name not in s["beyond"], f"core action '{name}' must stay on line 1: {s}"
        bad = page.evaluate(OVERFLOW_PROBE)
        assert not bad, f"horizontal overflow while folded: {bad}"

        # ── 2. expand ⇒ the tail appears BELOW as real buttons ──
        asst_row.locator("[data-testid=acts-toggle]").click()
        s = asst_row.evaluate(ROW_STATE)
        assert not s["folded"] and s["wrapH"] > s["rowH"], f"expanded wrap should show extra lines: {s}"
        bad = page.evaluate(OVERFLOW_PROBE)
        assert not bad, f"horizontal overflow while expanded: {bad}"

        # copy node id → the exact tree-node id lands on the clipboard
        asst_row.locator("[data-testid=copy-node-id]").click()
        copied = page.evaluate("() => window.__copied")
        assert copied == "Pa1", f"copied reference should be the bare node id 'Pa1', got {copied!r}"

        # send-branch-to-panel popover (ActionMenu) still works from the fold
        asst_row.locator("[data-testid=send-to]").click()
        menu = page.locator("[data-testid=send-to-panel]")
        menu.wait_for(timeout=4000)
        items = menu.locator(".row-menu-item").all_inner_texts()
        assert items and all(it.startswith("→") for it in items), f"send-to should list panels: {items}"
        page.keyboard.press("Escape")
        page.wait_for_function("!document.querySelector('[data-testid=send-to-panel]')", timeout=4000)

        # raw toggle button (in the unfolded tail) still works
        asst_row.locator("button.btn-raw").click()
        asst_row.locator("pre.raw-text-view").wait_for(timeout=4000)

        # collapse back
        asst_row.locator("[data-testid=acts-toggle]").click()
        assert asst_row.evaluate(ROW_STATE)["folded"], "toggle should fold the row again"

        # ── 3. wide ⇒ everything fits, toggle disappears (adaptive) ──
        page.set_viewport_size({"width": 1600, "height": 900})
        page.wait_for_function(
            "document.querySelectorAll('[data-testid=acts-toggle]').length === 0", timeout=4000)
        s = asst_row.evaluate(ROW_STATE)
        assert not s["beyond"], f"wide row should hold every button on one line: {s}"
        assert asst_row.locator("[data-testid=copy-node-id]").count() == 1, \
            "copy-node-id is a plain inline button when there's room"

        assert not errors, f"console errors: {errors}"
        browser.close()

    urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/api/conversations/{conv['id']}", method="DELETE"),
        timeout=10).read()

    print("browser_row_toolbar: OK — adaptive fold, unfold-below buttons, copy node id, send-to popover, wide=no toggle")


if __name__ == "__main__":
    main()
