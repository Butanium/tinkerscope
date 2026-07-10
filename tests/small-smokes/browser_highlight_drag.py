"""Highlight-rule drag-to-reorder smoke — deterministic, no sampling.

Seeds 3 highlight rules, then drags rule 3's grip to slot 1 (top) via synthesized
HTML5 DnD (vertical axis). Asserts:

  - rule-row order changed to [C, A, B]
  - the new order PERSISTED (GET /api/highlights sort_order), and survives reload
  - a rule row's name input stays EDITABLE (grip-only drag ⇒ text not locked)
  - dragging the NAME INPUT (not the grip) is inert (no reorder)
  - no console errors

Shares the `DragReorder`/`reorder.ts` helper with the panel-column drag (axis 'y'
here vs 'x' there). Cleans up its rules. Run against an isolated instance:

  uv run python tests/small-smokes/browser_highlight_drag.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_highlight_drag.png"

# (id, name) in seeded order. Distinct names double as the drop markers.
RULES = [
    ("hl-drag-a", "hl-drag-A"),
    ("hl-drag-b", "hl-drag-B"),
    ("hl-drag-c", "hl-drag-C"),
]


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def seed() -> None:
    for i, (rid, name) in enumerate(RULES):
        api("PUT", f"/api/highlights/{rid}", {
            "id": rid, "name": name, "enabled": True, "patterns": [name.lower()],
            "combinator": "or", "is_regex": False, "case_sensitive": False,
            "color": "#f87171", "scope_role": None, "sort_order": i,
        })


def cleanup() -> None:
    for rid, _ in RULES:
        try:
            api("DELETE", f"/api/highlights/{rid}")
        except Exception:
            pass


# Vertical DnD: drag `srcSel` in rule row `srcIndex` → top region of row
# `tgtIndex`. DragReorder('y') reads clientY vs the target row's midpoint.
DND_JS = """
({srcIndex, tgtIndex, yFrac, srcSel}) => {
  const rows = [...document.querySelectorAll('.hr-rule')];
  const src = rows[srcIndex].querySelector(srcSel || '.hr-grip');
  const tgt = rows[tgtIndex];
  const tr = tgt.getBoundingClientRect();
  const sr = src.getBoundingClientRect();
  const x = tr.left + tr.width / 2, y = tr.top + tr.height * yFrac;
  const dt = new DataTransfer();
  const fire = (el, type, cx, cy) =>
    el.dispatchEvent(new DragEvent(type, {
      bubbles: true, cancelable: true, composed: true,
      dataTransfer: dt, clientX: cx, clientY: cy,
    }));
  fire(src, 'dragstart', sr.left + sr.width / 2, sr.top + sr.height / 2);
  fire(tgt, 'dragover', x, y);
  fire(tgt, 'drop', x, y);
  fire(src, 'dragend', sr.left + sr.width / 2, sr.top + sr.height / 2);
}
"""


def all_names(page) -> list[str]:
    return page.eval_on_selector_all(".hr-rule .hr-name", "els => els.map(e => e.value)")


def row_order(page) -> list[str]:
    """The seeded rules' names, in DOM row order (ignores default-seed rules —
    a fresh instance ships starter rules, so we assert only RELATIVE order)."""
    seeded = {name for _rid, name in RULES}
    return [n for n in all_names(page) if n in seeded]


def dom_index(page, name: str) -> int:
    """Full DOM row index of the rule whose name input == `name` (-1 if absent)."""
    names = all_names(page)
    return names.index(name) if name in names else -1


def persisted_order() -> list[str]:
    rows = sorted(api("GET", "/api/highlights"), key=lambda r: r["sort_order"])
    seeded = {name for _rid, name in RULES}
    return [r["name"] for r in rows if r["name"] in seeded]


def wait_persisted(want: list[str], timeout=5.0) -> list[str]:
    deadline = time.time() + timeout
    last: list[str] = []
    while time.time() < deadline:
        last = persisted_order()
        if last == want:
            return last
        time.sleep(0.2)
    return last


def main() -> None:
    seed()
    checks: list[tuple[str, bool]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(f"{BASE}/", wait_until="load", timeout=20000)
            page.wait_for_function(
                "document.querySelectorAll('.hr-rule').length >= 3", timeout=15000
            )

            before = row_order(page)
            page.screenshot(path=SHOT)
            checks.append((f"initial rule order {before}",
                           before == ["hl-drag-A", "hl-drag-B", "hl-drag-C"]))

            # Drag my rule C's grip → top region of my rule A's row (⇒ gap at A's
            # index), landing C just before A. DOM indices are looked up live so we
            # tolerate the default-seed rules sitting above ours.
            page.evaluate(DND_JS, {"srcIndex": dom_index(page, "hl-drag-C"),
                                   "tgtIndex": dom_index(page, "hl-drag-A"),
                                   "yFrac": 0.25})
            page.wait_for_function(
                "() => { const n=[...document.querySelectorAll('.hr-rule .hr-name')]"
                ".map(e=>e.value).filter(v=>v.startsWith('hl-drag-'));"
                " return JSON.stringify(n)===JSON.stringify(['hl-drag-C','hl-drag-A','hl-drag-B']); }",
                timeout=10000,
            )
            after = row_order(page)
            checks.append((f"reordered rule order {after}",
                           after == ["hl-drag-C", "hl-drag-A", "hl-drag-B"]))

            # Grip-only ⇒ a rule's name input stays selectable + editable. Located
            # by value (my rule B, which doesn't move) so we never touch a seed rule.
            edit_res = page.evaluate(
                """(name) => {
                  const inp = [...document.querySelectorAll('.hr-name')].find(e => e.value === name);
                  if (!inp) return null;
                  inp.focus();
                  inp.setSelectionRange(0, inp.value.length);
                  return { editable: !inp.disabled && !inp.readOnly,
                           selLen: inp.selectionEnd - inp.selectionStart };
                }""",
                "hl-drag-B",
            )
            checks.append((f"rule name input selectable + editable ({edit_res})",
                           bool(edit_res and edit_res["editable"] and edit_res["selLen"] == len("hl-drag-B"))))

            # Dragging the NAME INPUT (not the grip) must NOT reorder.
            order_pre = row_order(page)
            page.evaluate(DND_JS, {"srcIndex": dom_index(page, "hl-drag-B"),
                                   "tgtIndex": dom_index(page, "hl-drag-C"),
                                   "yFrac": 0.25, "srcSel": ".hr-name"})
            page.wait_for_timeout(200)
            checks.append((f"dragging the name input is inert (order {row_order(page)})",
                           row_order(page) == order_pre))

            # Persisted + survives reload.
            persisted = wait_persisted(["hl-drag-C", "hl-drag-A", "hl-drag-B"])
            checks.append((f"reorder persisted {persisted}",
                           persisted == ["hl-drag-C", "hl-drag-A", "hl-drag-B"]))
            page.goto(f"{BASE}/", wait_until="load", timeout=20000)
            page.wait_for_function(
                "document.querySelectorAll('.hr-rule').length >= 3", timeout=15000
            )
            reloaded = row_order(page)
            checks.append((f"order survives reload {reloaded}",
                           reloaded == ["hl-drag-C", "hl-drag-A", "hl-drag-B"]))

            checks.append((f"no console errors ({len(errors)})", not errors))
            if errors:
                print("CONSOLE ERRORS:", errors)
            browser.close()
    finally:
        cleanup()

    print()
    ok = True
    for name, passed in checks:
        print(f"  {'✓' if passed else '✗'} {name}")
        ok = ok and passed
    print(f"\nscreenshot: {SHOT}")
    if not ok:
        raise SystemExit("highlight-drag smoke FAILED")
    print("highlight-drag smoke PASSED")


if __name__ == "__main__":
    main()
