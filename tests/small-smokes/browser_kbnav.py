"""Browser smoke for keyboard row navigation — focus / arrows / guards.

100% TOKEN-FREE: it seeds a conversation with a ready-made branch tree via
POST /api/conversations (8-turn chain; the last assistant turn has 3 sibling
branches), opens it with ?c=<id>, then exercises the focused-row keyboard
layer: click a row → focus ring; ↑/↓ walk the panel's view (revealing
off-screen rows by scrolling ONLY the panel container, minimally); ←/→ drive
the ‹k/N› sibling cycler (wrapping, scroll position PRESERVED); Escape clears;
and the guards — keys must be inert while typing in the composer and while a
modal is open. No model calls.

Playwright trap this respects: locator.click() AUTO-SCROLLS off-screen targets
into view, fabricating scroll changes — every row click here is a programmatic
element.click() inside evaluate(), and keys go through page.keyboard.

  uv run python tests/small-smokes/browser_kbnav.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

PAD = "\n\n".join(f"paragraph {j}: " + "lorem ipsum dolor sit amet " * 8 for j in range(3))


def seed_tree():
    """8 rows: u0 a1 u2 a3 u4 a5 u6 [a7A | a7B | a7C] — row 7 has 3 siblings."""
    nodes = {}

    def node(nid, role, content, parent, children):
        nodes[nid] = {"id": nid, "role": role, "content": content,
                      "parent": parent, "children": children}

    chain = []
    for i in range(7):
        nid = f"s{i}"
        role = "user" if i % 2 == 0 else "assistant"
        node(nid, role, f"TURN-{i}\n\n{PAD}", f"s{i - 1}" if i else None,
             [f"s{i + 1}"] if i < 6 else ["s7a", "s7b", "s7c"])
        chain.append(nid)
    for suffix in ("a", "b", "c"):
        node(f"s7{suffix}", "assistant", f"TURN-7-{suffix.upper()}\n\n{PAD}", "s6", [])
    return {"nodes": nodes, "rootChildren": ["s0"], "selected": {"s6": "s7a"}}


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


# ── page-side probe (read-only) ──────────────────────────────────────
FOCUS_STATE = """() => {
  const c = document.querySelector('.messages');
  const rings = document.querySelectorAll('.message.kb-focused');
  const row = rings[0] ?? null;
  const cr = c.getBoundingClientRect();
  const rr = row ? row.getBoundingClientRect() : null;
  return {
    ringCount: rings.length,
    index: row ? Number(row.dataset.row) : null,
    scrollTop: c.scrollTop,
    // block:'nearest' invariant: after a reveal the row is fully inside the
    // container viewport, unless it's TALLER than the viewport (then its top is).
    visible: row ? (rr.top >= cr.top - 1 && rr.bottom <= cr.bottom + 1)
                   || (rr.height > c.clientHeight && rr.top >= cr.top - 9) : null,
    cycleText: row ? (row.querySelector('.branch-cycle-count')?.textContent ?? null) : null,
    marker: row ? (row.textContent.match(/TURN-7-[ABC]/)?.[0] ?? null) : null,
  };
}"""


def click_row(page, index):
    """Programmatic click (no Playwright auto-scroll) on .message[data-row=N]."""
    page.evaluate(
        "(i) => document.querySelector(`.messages .message[data-row='${i}']`).click()", index)


def main():
    primary = _get("/api/state")["panels"][0]
    conv = _post("/api/conversations", {
        "name": "kbnav smoke",
        "trees": {"primary": seed_tree()},
        "panels": [{"id": "primary", "run_id": primary.get("run_id"),
                    "checkpoint": primary.get("checkpoint")}],
    })

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 750})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/?c={conv['id']}", wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('TURN-7-A')", timeout=15000)
        assert page.locator(".message[data-row]").count() == 8, "seed should render 8 rows"

        # ── 1. click focuses (ONE ring per workspace); no scroll on click ──
        st0 = page.evaluate("() => document.querySelector('.messages').scrollTop")
        click_row(page, 5)
        s = page.evaluate(FOCUS_STATE)
        assert s["ringCount"] == 1 and s["index"] == 5, f"click should focus row 5: {s}"
        assert s["scrollTop"] == st0, "clicking a row must not scroll"
        click_row(page, 6)
        s = page.evaluate(FOCUS_STATE)
        assert s["ringCount"] == 1 and s["index"] == 6, "focus should MOVE, not accumulate"

        # ── 2. ↑ walks up, revealing each focused row (minimal, container-only) ──
        for expect in (5, 4, 3, 2, 1, 0):
            page.keyboard.press("ArrowUp")
            s = page.evaluate(FOCUS_STATE)
            assert s["index"] == expect, f"ArrowUp should land on {expect}: {s}"
            assert s["visible"], f"revealed row {expect} should be in view: {s}"
        assert s["scrollTop"] < st0, "walking to the top row must have scrolled up"
        page.keyboard.press("ArrowUp")  # clamp at the first row — no wrap
        assert page.evaluate(FOCUS_STATE)["index"] == 0, "ArrowUp at row 0 stays put"

        # ── 3. ↓ walks down; clamp at the last row ──
        for _ in range(9):
            page.keyboard.press("ArrowDown")
        s = page.evaluate(FOCUS_STATE)
        assert s["index"] == 7 and s["visible"], f"ArrowDown clamps at last row: {s}"

        # ── 4. ←/→ = the focused row's ‹k/N› cycler; focus + scroll survive ──
        assert s["cycleText"] == "1/3" and s["marker"] == "TURN-7-A", f"seed selects A: {s}"
        st1 = s["scrollTop"]
        page.keyboard.press("ArrowRight")
        s = page.evaluate(FOCUS_STATE)
        assert s["cycleText"] == "2/3" and s["marker"] == "TURN-7-B", f"→ cycles to B: {s}"
        assert s["index"] == 7, "focus must survive a cycle at the same row position"
        assert s["scrollTop"] == st1, "cycling must PRESERVE the scroll position"
        page.keyboard.press("ArrowLeft")
        assert page.evaluate(FOCUS_STATE)["marker"] == "TURN-7-A", "← cycles back to A"
        page.keyboard.press("ArrowLeft")  # wraps 1 → 3 (sibling cycling wraps; ↑/↓ don't)
        s = page.evaluate(FOCUS_STATE)
        assert s["cycleText"] == "3/3" and s["marker"] == "TURN-7-C", f"← wraps to C: {s}"

        # ── 5. Escape clears the focus ──
        page.keyboard.press("Escape")
        assert page.evaluate(FOCUS_STATE)["ringCount"] == 0, "Escape should clear focus"

        # ── 6. typing guard: keys in the composer are the composer's ──
        click_row(page, 7)
        page.locator("textarea.input-textarea").click()
        page.keyboard.type("drafting a prompt")
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("ArrowUp")
        s = page.evaluate(FOCUS_STATE)
        assert s["index"] == 7 and s["marker"] == "TURN-7-C", f"typing must not nav/cycle: {s}"
        page.keyboard.press("Escape")  # composer's history toggle — NOT our clear
        assert page.evaluate(FOCUS_STATE)["ringCount"] == 1, "Escape in composer keeps focus"
        page.evaluate("() => document.querySelector('textarea.input-textarea').blur()")

        # ── 7. modal guard: keys are inert while a modal is open ──
        page.locator("button[data-tooltip^='View response distribution']").click()
        page.wait_for_selector(".modal-overlay", timeout=4000)
        page.keyboard.press("ArrowUp")
        page.keyboard.press("Escape")
        s = page.evaluate(FOCUS_STATE)
        assert s["index"] == 7 and s["ringCount"] == 1, f"modal must swallow nav keys: {s}"
        page.locator("button.modal-close").click()
        page.wait_for_function("!document.querySelector('.modal-overlay')", timeout=4000)
        page.keyboard.press("Escape")  # modal gone → Escape clears again
        assert page.evaluate(FOCUS_STATE)["ringCount"] == 0

        assert not errors, f"console errors: {errors}"
        browser.close()

    # cleanup: remove the seeded conversation
    urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/api/conversations/{conv['id']}", method="DELETE"),
        timeout=10).read()

    print("browser_kbnav: OK — click-focus, arrow walk + reveal, cycle+preserve, guards")


if __name__ == "__main__":
    main()
