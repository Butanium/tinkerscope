"""Panel drag-to-reorder smoke — fully deterministic (no sampling).

Seeds a 3-panel conversation, each panel a DISTINCT model + a DISTINCT thread:

    slot 0  primary  model 'Zebra-Model'    thread 'ALPHA-THREAD'
    slot 1  compare  model 'Mango-Model'    thread 'BRAVO-THREAD'
    slot 2  p-2      model 'Cobalt-Model'   thread 'CHARLIE-THREAD'

Then drags column 3 (CHARLIE / p-2) to slot 1 via synthesized HTML5 DnD events
(mouse-only .drag_to doesn't fire dragstart/dragover/drop reliably — we dispatch
a shared-DataTransfer sequence). Asserts:

  - chat-column order changed to [CHARLIE, ALPHA, BRAVO]
  - sidebar Models picker order matches [Cobalt, Zebra, Mango]
  - each column kept ITS OWN thread content (content travels with the stable id)
  - the new order SURVIVES A RELOAD (per-conversation layout persistence)
  - no console errors throughout

Screenshots before/after to /tmp for eyeballing. Cleans up its conversation.

  uv run python tests/small-smokes/browser_panel_drag.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT_BEFORE = "/tmp/tinkerscope_panel_drag_before.png"
SHOT_AFTER = "/tmp/tinkerscope_panel_drag_after.png"
SHOT_SCALE = "/tmp/tinkerscope_panel_drag_8panels.png"

# (panel id, base-model label, thread marker) in seeded slot order.
PANELS = [
    ("primary", "Zebra-Model", "ALPHA-THREAD"),
    ("compare", "Mango-Model", "BRAVO-THREAD"),
    ("p-2", "Cobalt-Model", "CHARLIE-THREAD"),
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


def seed() -> str:
    """3-panel conversation, each panel a distinct base model + distinct thread."""
    trees = {}
    for pid, _model, marker in PANELS:
        trees[pid] = {
            "nodes": {
                "u1": {"id": "u1", "role": "user", "content": f"{marker} question",
                       "parent": None, "children": ["a1"]},
                "a1": {"id": "a1", "role": "assistant", "content": f"{marker} answer",
                       "parent": "u1", "children": []},
            },
            "rootChildren": ["u1"],
            "selected": {"__root__": "u1", "u1": "a1"},
        }
    conv = api("POST", "/api/conversations", {
        "name": "panel-drag-smoke",
        "trees": trees,
        "panels": [
            {"id": pid, "run_id": f"base:{model}", "checkpoint": None}
            for pid, model, _marker in PANELS
        ],
        "seen_panels": [pid for pid, _m, _t in PANELS],
    })
    return conv["id"]


def seed_scale(n: int) -> str:
    """An n-panel conversation (n > cap) to prove the cap is gone + layout scales."""
    pids = ["primary", "compare"] + [f"p-{i}" for i in range(2, n)]
    trees = {
        pid: {
            "nodes": {"u1": {"id": "u1", "role": "user", "content": f"SCALE-{i} q",
                             "parent": None, "children": ["a1"]},
                      "a1": {"id": "a1", "role": "assistant", "content": f"SCALE-{i} a",
                             "parent": "u1", "children": []}},
            "rootChildren": ["u1"], "selected": {"__root__": "u1", "u1": "a1"},
        }
        for i, pid in enumerate(pids)
    }
    conv = api("POST", "/api/conversations", {
        "name": f"panel-scale-{n}-smoke",
        "trees": trees,
        "panels": [{"id": pid, "run_id": f"base:Scale-{i}", "checkpoint": None}
                   for i, pid in enumerate(pids)],
        "seen_panels": pids,
    })
    return conv["id"]


# Synthesize an HTML5 drag: drag `srcSel` inside column `srcIndex` → the left
# region of column `tgtIndex`. Our handlers key off the DragReorder state set on
# dragstart + a gap from clientX vs the target column's midpoint, so a plain event
# sequence with a shared DataTransfer is enough (dataTransfer content is unused).
# srcSel defaults to '.drag-grip' — the ONLY draggable element (the amendment:
# the header is NOT draggable so its title text stays selectable). Passing
# '.column-title' instead lets us assert dragging the TITLE is inert.
DND_JS = """
({srcIndex, tgtIndex, xFrac, srcSel}) => {
  const cols = [...document.querySelectorAll('.chat-columns > .chat-column')];
  const src = cols[srcIndex].querySelector(srcSel || '.drag-grip');
  const tgt = cols[tgtIndex];
  const tr = tgt.getBoundingClientRect();
  const sr = src.getBoundingClientRect();
  const x = tr.left + tr.width * xFrac, y = tr.top + tr.height / 2;
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

# Programmatically select a column's title text; returns the selected string.
SELECT_TITLE_JS = """
(colIndex) => {
  const title = [...document.querySelectorAll('.chat-columns > .chat-column')][colIndex]
    .querySelector('.column-title');
  const range = document.createRange();
  range.selectNodeContents(title);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  return sel.toString();
}
"""


def column_order(page) -> list[str]:
    """Marker (ALPHA/BRAVO/CHARLIE) per chat column, in DOM order."""
    texts = page.eval_on_selector_all(
        ".chat-columns > .chat-column",
        "els => els.map(e => e.querySelector('.messages')?.innerText || '')",
    )
    out = []
    for t in texts:
        for _pid, _model, marker in PANELS:
            if marker in t:
                out.append(marker)
                break
        else:
            out.append("?")
    return out


def persisted_panel_ids(conv_id: str) -> list[str]:
    """Panel ids, in order, as persisted on the conversation (server truth)."""
    for c in api("GET", "/api/conversations"):
        if c["id"] == conv_id:
            return [p["id"] for p in (c.get("panels") or [])]
    return []


def wait_persisted(conv_id: str, want: list[str], timeout=5.0) -> list[str]:
    """Poll the conversation until its panel order matches (save is debounced 400ms)."""
    deadline = time.time() + timeout
    last: list[str] = []
    while time.time() < deadline:
        last = persisted_panel_ids(conv_id)
        if last == want:
            return last
        time.sleep(0.2)
    return last


def sidebar_order(page) -> list[str]:
    """Model label per sidebar picker block, in DOM order."""
    labels = page.eval_on_selector_all(
        ".model-block .model-dropdown-trigger-label",
        "els => els.map(e => e.innerText.trim())",
    )
    out = []
    for lab in labels:
        for _pid, model, _marker in PANELS:
            if model in lab:
                out.append(model)
                break
        else:
            out.append("?")
    return out


def main() -> None:
    conv_id = seed()
    conv8_id = None
    checks: list[tuple[str, bool]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1600, "height": 950})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(f"{BASE}/?c={conv_id}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-select", timeout=15000)
            page.wait_for_function(
                "document.body.innerText.includes('CHARLIE-THREAD')", timeout=15000
            )
            # all three columns rendered
            page.wait_for_function(
                "document.querySelectorAll('.chat-columns > .chat-column').length === 3",
                timeout=15000,
            )

            before_cols = column_order(page)
            before_side = sidebar_order(page)
            page.screenshot(path=SHOT_BEFORE)
            checks.append((f"initial column order {before_cols}",
                           before_cols == ["ALPHA-THREAD", "BRAVO-THREAD", "CHARLIE-THREAD"]))
            checks.append((f"initial sidebar order {before_side}",
                           before_side == ["Zebra-Model", "Mango-Model", "Cobalt-Model"]))

            # Drag column 3 (CHARLIE, index 2) → left region of column 1 (index 0) ⇒ gap 0.
            page.evaluate(DND_JS, {"srcIndex": 2, "tgtIndex": 0, "xFrac": 0.25})

            page.wait_for_function(
                """() => {
                  const c = document.querySelector('.chat-columns > .chat-column .messages');
                  return c && c.innerText.includes('CHARLIE-THREAD');
                }""",
                timeout=10000,
            )
            after_cols = column_order(page)
            after_side = sidebar_order(page)
            page.screenshot(path=SHOT_AFTER)
            checks.append((f"reordered column order {after_cols}",
                           after_cols == ["CHARLIE-THREAD", "ALPHA-THREAD", "BRAVO-THREAD"]))
            checks.append((f"reordered sidebar order {after_side}",
                           after_side == ["Cobalt-Model", "Zebra-Model", "Mango-Model"]))
            # content-travels: sidebar order and column order agree slot-for-slot
            model_by_marker = {m: mod for _p, mod, m in PANELS}
            checks.append((
                "each column kept its own model (content travels with id)",
                [model_by_marker[m] for m in after_cols] == after_side,
            ))

            # Amendment: the title text stays selectable (only the grip drags).
            selected = page.evaluate(SELECT_TITLE_JS, 0)
            checks.append((f"column title text is selectable ({selected!r})",
                           bool(selected and selected.strip())))

            # Amendment: dragging the TITLE (not the grip) must NOT reorder.
            order_pre = column_order(page)
            page.evaluate(DND_JS, {"srcIndex": 1, "tgtIndex": 0, "xFrac": 0.25,
                                   "srcSel": ".column-title"})
            page.wait_for_timeout(200)
            order_post = column_order(page)
            checks.append((f"dragging the title is inert (order {order_post})",
                           order_post == order_pre))

            # Persistence is debounced (400ms) — wait for the save to land on the
            # server, then assert the conversation itself carries the new order.
            persisted = wait_persisted(conv_id, ["p-2", "primary", "compare"])
            checks.append((f"reorder persisted to conversation {persisted}",
                           persisted == ["p-2", "primary", "compare"]))

            # Reload → per-conversation layout must restore the reordered set.
            page.goto(f"{BASE}/?c={conv_id}", wait_until="load", timeout=20000)
            page.wait_for_function(
                "document.querySelectorAll('.chat-columns > .chat-column').length === 3",
                timeout=15000,
            )
            page.wait_for_function(
                "document.body.innerText.includes('CHARLIE-THREAD')", timeout=15000
            )
            reload_cols = column_order(page)
            checks.append((f"order survives reload {reload_cols}",
                           reload_cols == ["CHARLIE-THREAD", "ALPHA-THREAD", "BRAVO-THREAD"]))

            # ── Phase 2: no MAX_PANELS cap + horizontal-scroll layout at 8 panels ──
            conv8_id = seed_scale(8)
            page.goto(f"{BASE}/?c={conv8_id}", wait_until="load", timeout=20000)
            page.wait_for_function(
                "document.querySelectorAll('.chat-columns > .chat-column').length === 8",
                timeout=15000,
            )
            n_cols = page.eval_on_selector_all(".chat-columns > .chat-column", "els => els.length")
            checks.append((f"8 panels render — no cap refusal ({n_cols})", n_cols == 8))
            layout = page.eval_on_selector(
                ".chat-columns",
                """el => ({ overflow: el.scrollWidth > el.clientWidth + 1,
                            minW: Math.min(...[...el.querySelectorAll(':scope > .chat-column')]
                                             .map(c => c.offsetWidth)) })""",
            )
            checks.append((f"columns row scrolls horizontally ({layout})", layout["overflow"]))
            checks.append((f"columns hold min-width ~280 ({layout['minW']}px)",
                           layout["minW"] >= 270))

            # Drag panel 8 (index 7, may be off-screen) → slot 1, across the scroll.
            page.evaluate(DND_JS, {"srcIndex": 7, "tgtIndex": 0, "xFrac": 0.25})
            page.wait_for_function(
                """() => {
                  const c = document.querySelector('.chat-columns > .chat-column .messages');
                  return c && c.innerText.includes('SCALE-7');
                }""",
                timeout=10000,
            )
            page.eval_on_selector(".chat-columns", "el => el.scrollLeft = 0")
            page.screenshot(path=SHOT_SCALE)
            first_scale = page.eval_on_selector(
                ".chat-columns > .chat-column .messages", "e => e.innerText"
            )
            checks.append(("drag panel 8 → slot 1 across scroll",
                           "SCALE-7" in first_scale))

            checks.append((f"no console errors ({len(errors)})", not errors))
            if errors:
                print("CONSOLE ERRORS:", errors)
            browser.close()
    finally:
        api("DELETE", f"/api/conversations/{conv_id}")
        if conv8_id:
            api("DELETE", f"/api/conversations/{conv8_id}")

    print()
    ok = True
    for name, passed in checks:
        print(f"  {'✓' if passed else '✗'} {name}")
        ok = ok and passed
    print(f"\nscreenshots: {SHOT_BEFORE}  {SHOT_AFTER}  {SHOT_SCALE}")
    if not ok:
        raise SystemExit("panel-drag smoke FAILED")
    print("panel-drag smoke PASSED")


if __name__ == "__main__":
    main()
