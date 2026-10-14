"""Measure every icon in the real UI states where the re-drawn glyphs live.

    uv run --with numpy python verify.py <tag>       # whatever build web/dist currently holds

For each state: a dpr-2 screenshot of a region + every <svg> inside it with its DOM box and
its button's DOM box. The ink centre of each icon is measured sub-pixel from the screenshot
(inside the svg's own box, so button borders never count) and compared with its button's
centre (or, for an inline icon in prose, with the middle of the surrounding text's caps).
Writes vcap/<tag>/<state>.png + <tag>.json.
"""

import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

TAG = sys.argv[1]
OUT = Path(__file__).resolve().parent / "vcap" / TAG
OUT.mkdir(parents=True, exist_ok=True)
HOST = "http://127.0.0.1:8793"
WS_MAIN = "a410b399-ebb9-4819-a34a-d8e017573e14"
WS_THINK = "947676fd-bee8-409b-b7da-8d08c76bc12d"
DPR = 2
results = {}

COLLECT = """([x0, y0, x1, y1]) => {
  const out = [];
  for (const svg of document.querySelectorAll('svg')) {
    const s = svg.getBoundingClientRect();
    if (s.width < 8 || s.width > 20) continue;
    if (s.left < x0 || s.right > x1 || s.top < y0 || s.bottom > y1) continue;
    if (getComputedStyle(svg).visibility === 'hidden') continue;
    const btn = svg.closest('button, a, .chip, .sel-chip');
    const b = btn ? btn.getBoundingClientRect() : null;
    const cs = btn ? getComputedStyle(btn) : null;
    const bordered = !!cs && parseFloat(cs.borderTopWidth) > 0 && !/rgba\(.*, 0\)|transparent/.test(cs.borderTopColor);
    const label = btn ? (btn.getAttribute('aria-label') || btn.getAttribute('data-tooltip') || btn.className || '').slice(0, 40) : 'inline';
    out.push({label, bordered, svg: [s.left, s.top, s.width, s.height], btn: b ? [b.left, b.top, b.width, b.height] : null});
  }
  return out;
}"""


def ink_edges(img: np.ndarray, x0: int, x1: int, y0: int, y1: int):
    """Sub-pixel top/bottom of the ink in a device-px window (area method on the per-row max coverage)."""
    reg = img[max(0, y0):y1, max(0, x0):x1].astype(float)
    if reg.shape[0] < 6 or reg.shape[1] < 4:
        return None
    y0 = max(0, y0)
    bg = np.median(np.concatenate([reg[:2].reshape(-1, 3), reg[-2:].reshape(-1, 3)]), axis=0)
    dist = np.sqrt(((reg - bg) ** 2).sum(-1))
    if dist.max() < 25:
        return None
    hi = np.percentile(dist[dist > 0.3 * dist.max()], 90)
    p = np.clip(dist / hi, 0, 1).max(1)
    full = np.where(p >= 0.9)[0]
    if not len(full):
        return None
    top = full[0] - p[: full[0]].sum()
    bot = full[-1] + 1 + p[full[-1] + 1:].sum()
    return y0 + top, y0 + bot


def painted_centre(img: np.ndarray, btn, x0: float, y0: float):
    """Centre of the button's DRAWN border (Chrome rounds a box and its icon together to whole CSS px,
    so the layout rect's centre can sit half a pixel from what is painted). Border rows = rows where
    nearly the whole span between the corners differs from the button's interior."""
    bx, by, bw, bh = btn
    xa, xb = int((bx + 6 - x0) * DPR), int((bx + bw - 6 - x0) * DPR)
    ya, yb = max(0, int(np.floor((by - y0) * DPR)) - 3), min(img.shape[0], int(np.ceil((by + bh - y0) * DPR)) + 3)
    if xb - xa < 4 or yb - ya < 10:
        return None
    inside = img[int((by + bh / 2 - y0) * DPR), int((bx + 3 - x0) * DPR)].astype(float)
    reg = img[ya:yb, xa:xb].astype(float)
    frac = (np.sqrt(((reg - inside) ** 2).sum(-1)) > 25).mean(1)
    mid = (yb - ya) // 2
    top = [i for i in range(mid) if frac[i] > 0.85]
    bot = [i for i in range(mid, yb - ya) if frac[i] > 0.85]
    if not top or not bot:
        return None
    return (ya + (max(top) + 1 + min(bot)) / 2) / DPR + y0


def text_caps(img: np.ndarray, x0: int, x1: int, y0: int, y1: int):
    """Caps-top and baseline of text in a window: rows inked by most columns' worth of text = x-band; use tall letters' top."""
    reg = img[y0:y1, x0:x1].astype(float)
    bg = np.median(reg.reshape(-1, 3), axis=0)
    ink = np.sqrt(((reg - bg) ** 2).sum(-1)) > 90
    prof = ink.sum(1)
    rows = np.where(prof > 0)[0]
    if not len(rows):
        return None
    # baseline: last row with substantial ink (descenders are sparse)
    strong = np.where(prof > 0.25 * prof.max())[0]
    return y0 + rows[0], y0 + strong[-1] + 1


def capture(page, name: str, region, inline_text=False):
    """region in CSS px [x0, y0, x1, y1]."""
    x0, y0, x1, y1 = region = [max(0, region[0]), max(0, region[1]), region[2], region[3]]
    page.wait_for_timeout(250)
    png = page.screenshot(clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}, animations="disabled")
    (OUT / f"{name}.png").write_bytes(png)
    img = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
    icons = page.evaluate(COLLECT, [x0 - 0.01, y0 - 0.01, x1 + 0.01, y1 + 0.01])
    rows = []
    for ic in icons:
        sx, sy, sw, sh = ic["svg"]
        dx0, dx1 = int(round((sx - x0) * DPR)), int(round((sx + sw - x0) * DPR))
        dy0, dy1 = int(np.floor((sy - y0) * DPR)) - 2, int(np.ceil((sy + sh - y0) * DPR)) + 2
        e = ink_edges(img, dx0, dx1, max(0, dy0), min(img.shape[0], dy1))
        if e is None:
            continue
        ink_cy = ((e[0] + e[1]) / 2) / DPR + y0  # CSS px
        if ic["btn"] and not inline_text:
            bx, by, bw, bh = ic["btn"]
            ref = painted_centre(img, ic["btn"], x0, y0) if ic["bordered"] else None
            kind = "border"
            if ref is None:
                ref = by + bh / 2
                kind = "button"
        else:
            # inline icon: compare with the caps middle of the text right next to it
            tx0, tx1 = dx1 + 4, min(img.shape[1], dx1 + 60)
            caps = text_caps(img, tx0, tx1, max(0, dy0 - 6), min(img.shape[0], dy1 + 6))
            if caps is None:
                continue
            ref = ((caps[0] + caps[1]) / 2) / DPR + y0
            kind = "caps"
        rows.append({
            "label": ic["label"], "svg": ic["svg"], "btn": ic["btn"], "kind": kind,
            "ink": [e[0] / DPR + y0, e[1] / DPR + y0], "ref": ref, "off": ink_cy - ref,
            "svg_frac": sy % 1, "svg_cx": sx + sw / 2,
        })
    results[name] = {"region": region, "icons": rows}


def box(page, sel, pad=(10, 8), nth=0):
    b = page.locator(sel).nth(nth).bounding_box()
    return [b["x"] - pad[0], b["y"] - pad[1], b["x"] + b["width"] + pad[0], b["y"] + b["height"] + pad[1]]


def union(*rs):
    return [min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs)]


def open_ws(page, wid, extra=""):
    page.goto(f"{HOST}/?w={wid}{extra}")
    page.wait_for_selector(".btn-act", timeout=90000)
    page.wait_for_timeout(1200)
    page.mouse.move(3, 940)


with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": 1500, "height": 950}, device_scale_factor=DPR, color_scheme="light")
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=HOST)
    pg = ctx.new_page()
    open_ws(pg, WS_MAIN)

    # sidebar icon row (undo enabled for the picture: greyed it would measure the same, just faint)
    pg.locator('[aria-label="Undo last delete"]').evaluate("e => e.removeAttribute('disabled')")
    capture(pg, "sidebar", box(pg, ".sidebar-top-actions", pad=(6, 6)))

    # topbar theme toggle: one capture per mode (the click cycles it)
    theme_btn = pg.locator('[data-testid="theme-toggle"]')
    for i in range(3):
        r = box(pg, '[data-testid="theme-toggle"]', pad=(8, 6))
        capture(pg, f"theme_{i}", [r[0], r[1], r[0] + 140, r[3]])
        theme_btn.click()
        pg.wait_for_timeout(300)
        pg.mouse.move(3, 940)
    capture(pg, "topbar_right", box(pg, 'button[aria-label="Help"]', pad=(60, 6)))
    # theme glyph is meant to line up (horizontally) with the sidebar glyphs below it
    results["theme_x"] = pg.evaluate("""() => {
      const c = (el) => { const r = el.getBoundingClientRect(); return r.left + r.width / 2; };
      return {theme: c(document.querySelector('[data-testid="theme-toggle"] svg')),
              first_sidebar: c(document.querySelector('.sidebar-top-actions button svg'))};
    }""")

    # sidebar section headings (fold chevron beside the title) and the + Compare button: icon vs the text next to it
    t = pg.locator(".sidebar-section-toggle").first
    capture(pg, "heading", box(pg, ".sidebar-section-toggle", pad=(6, 6)), inline_text=True)

    # workspace row: + and, with Shift, new-blank
    reg = union(box(pg, ".ws-picker", pad=(4, 6)), *[box(pg, ".ws-icon-btn", nth=i, pad=(4, 6)) for i in range(pg.locator(".ws-icon-btn").count())])
    capture(pg, "ws_plain", reg)
    pg.keyboard.down("Shift")
    capture(pg, "ws_shift", reg)

    # first panel's copy-sampler-path button: Shift → dataset, Ctrl+Shift → external, click → check
    cp = pg.locator('[aria-label="Copy sampler path"]').first
    cb = cp.bounding_box()
    reg = [cb["x"] - 230, cb["y"] - 8, cb["x"] + cb["width"] + 50, cb["y"] + cb["height"] + 8]
    capture(pg, "path_shift", reg)
    pg.keyboard.down("Control")
    capture(pg, "path_ctrlshift", reg)
    pg.keyboard.up("Control")
    pg.keyboard.up("Shift")
    capture(pg, "path_plain", reg)
    cp.evaluate("e => e.click()")
    pg.wait_for_timeout(150)
    capture(pg, "path_copied", reg)

    capture(pg, "panel_send", box(pg, ".panel-send", pad=(6, 6)))
    pg.locator(".btn-add-model").scroll_into_view_if_needed()
    capture(pg, "add_model", box(pg, ".btn-add-model", pad=(6, 6)), inline_text=True)

    # row toolbar on a turn with thinking, its panel focused so nothing folds
    open_ws(pg, WS_THINK)
    pg.keyboard.down("Shift")
    pg.wait_for_timeout(300)
    alt = pg.locator('[aria-label="Continue this message"].shift-alt').first
    panel_id = alt.evaluate("e => e.closest('.chat-column')?.getAttribute('data-panel') || ''")
    pg.keyboard.up("Shift")
    print("thinking panel:", repr(panel_id))
    if panel_id:
        open_ws(pg, WS_THINK, f"&focus={panel_id}")
    pg.keyboard.down("Shift")
    pg.wait_for_timeout(300)
    c = pg.locator('[aria-label="Continue this message"].shift-alt').first.element_handle()
    c.scroll_into_view_if_needed()
    pg.wait_for_timeout(200)
    cbox = c.bounding_box()
    bar = c.evaluate_handle("e => e.parentElement").as_element()
    bb = bar.bounding_box()
    pg.keyboard.up("Shift")
    reg = [bb["x"] - 6, bb["y"] - 8, min(1495, bb["x"] + bb["width"] + 6), bb["y"] + bb["height"] + 8]
    pg.mouse.move(cbox["x"] + cbox["width"] / 2, cbox["y"] - 30)
    capture(pg, "bar_plain", reg)
    pg.keyboard.down("Shift")
    capture(pg, "bar_shift", reg)
    pg.keyboard.up("Shift")
    pg.wait_for_timeout(200)
    bar.query_selector('[aria-label="Copy this message"]').evaluate("e => e.click()")
    pg.wait_for_timeout(150)
    capture(pg, "bar_copied", reg)

    # Help modal: inline icons in the Guide prose (undo, dataset) + the toolbar legend
    pg.mouse.move(3, 940)
    pg.locator('button[aria-label="Help"]').first.click()
    pg.wait_for_timeout(600)
    for name, icon_text in (("help_undo", "puts back the last delete"), ("help_dataset", "training JSONL")):
        para = pg.locator(".modal p", has_text=icon_text).first
        para.scroll_into_view_if_needed()
        pb = para.bounding_box()
        capture(pg, name, [pb["x"] - 4, pb["y"] - 4, pb["x"] + pb["width"] + 4, pb["y"] + pb["height"] + 4], inline_text=True)
    chip = pg.locator(".modal .help-chip").first
    chip.scroll_into_view_if_needed()
    rowel = chip.locator("xpath=ancestor::*[self::li or self::tr or self::p or self::div][1]")
    rb = rowel.bounding_box()
    capture(pg, "help_legend", [rb["x"] - 4, rb["y"] - 4, rb["x"] + min(rb["width"], 700) + 4, rb["y"] + rb["height"] + 4])
    br.close()

(OUT.parent / f"{TAG}.json").write_text(json.dumps(results, indent=1))
for st, r in results.items():
    if st == "theme_x":
        print("theme_x", r)
        continue
    print(f"{st:15s}", "  ".join(f"{i['label'][:18]}:{i['off']:+.2f}" for i in r["icons"]))
