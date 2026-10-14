"""Before/after contact sheet from verify.py's captures, every icon annotated.

Per icon: blue = top/bottom of its ink, solid red = ink centre, dashed red = its reference
(the button's centre, or the caps middle of the text next to an inline icon)."""

import base64
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

V = Path(__file__).resolve().parent / "vcap"
OUT = V / "glyph_verify.png"
DPR, UP = 2, 3
R = {t: json.loads((V / f"{t}.json").read_text()) for t in ("before", "after")}

STATES = [
    ("sidebar", "Sidebar icon row", "undo and search were 15 px, which put their buttons on a half pixel; now 16 px. pins and refresh redrawn inside the margin"),
    ("theme_1", "Topbar, theme toggle (light)", "15 → 16 px, same half-pixel reason; sun rays shortened"),
    ("topbar_right", "Topbar, help", "15 → 16 px"),
    ("heading", "Sidebar section heading", "fold chevron 12 → 13 px: a 12 px icon in the 17 px heading sat on a half pixel"),
    ("ws_plain", "Workspace row", "icons 13 → 12 px: 13 px in a 28 px button left a half-pixel inset, so all three sat high"),
    ("ws_shift", "Workspace row, Shift held", "new-blank moved down 1 unit"),
    ("add_model", "Add-panel button (+ Compare)", "plus 12 → 13 px, half-pixel inset again"),
    ("panel_send", "Panel continue box", "arrow 14 → 13 px: its 24 px button sat on a half pixel beside the 25 px input"),
    ("path_shift", "Panel copy-path button, Shift held", "dataset scaled 0.9 and centred"),
    ("path_copied", "Panel copy-path button, after a click", "check moved up 0.5 unit"),
    ("bar_shift", "Row toolbar, Shift held (turn with thinking)", "replace arrowheads shortened; continue-think redrawn (body lower, dots tucked in)"),
    ("bar_copied", "Row toolbar, after copying", "check moved up; regen arrowheads shortened; eye narrowed; tag 12 → 13 px: its 20 px button sat on a half pixel among 21 px ones"),
    ("help_undo", "Help modal prose", "inline icons sat on the baseline, about 2 px high; now centred on the caps"),
    ("help_dataset", "Help modal prose", ""),
]


def annotated(tag, state, crop_x=None):
    r = R[tag][state]
    x0, y0 = r["region"][0], r["region"][1]
    im = Image.open(V / tag / f"{state}.png").convert("RGB")
    if crop_x:
        cx0, cx1 = [int((c - x0) * DPR) for c in crop_x]
        im = im.crop((max(0, cx0), 0, min(im.width, cx1), im.height))
        x0 = x0 + max(0, cx0) / DPR
    big = im.resize((im.width * UP, im.height * UP), Image.NEAREST)
    d = ImageDraw.Draw(big)
    S = DPR * UP
    worst = []
    for ic in r["icons"]:
        sx, sy, sw, sh = ic["svg"]
        a, b = (sx - x0) * S - 6, (sx + sw - x0) * S + 6
        if b < 0 or a > big.width:
            continue
        for y in ic["ink"]:
            d.line([(a, (y - y0) * S), (b, (y - y0) * S)], fill=(47, 111, 222), width=2)
        ym = ((ic["ink"][0] + ic["ink"][1]) / 2 - y0) * S
        d.line([(a, ym), (b, ym)], fill=(214, 51, 51), width=3)
        yr = (ic["ref"] - y0) * S
        ra, rb = (a - 30, b + 30)
        x = ra
        while x < rb:
            d.line([(x, yr), (min(x + 8, rb), yr)], fill=(214, 51, 51), width=2)
            x += 14
        if abs(ic["off"]) >= 0.3:
            worst.append(f"{ic['label'][:28]} {abs(ic['off']):.1f} px {'low' if ic['off'] > 0 else 'high'}")
    buf = io.BytesIO()
    big.save(buf, "PNG")
    img = f"<img src='data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}' style='width:{big.width / 2}px'>"
    note = ("<span class='bad'>off-centre: " + "; ".join(worst) + "</span>") if worst else "<span class='ok'>every icon within 0.3 px of centre</span>"
    if tag == "after" and any(w.startswith("Discard other") for w in worst):
        note += ("<br><span class='n'>Left as it is: discard-others has a centred outline; the measure counts its faded back sheet "
                 "as thin ink, and by eye it lines up with its neighbours.</span>")
    return img, note


def help_crop(state):
    """Inline prose icons: crop around them instead of the whole paragraph."""
    xs = [ic["svg"][0] for ic in R["after"][state]["icons"]]
    return (min(xs) - 120, max(xs) + 140)


blocks = []
for state, title, what in STATES:
    if state.startswith("help"):
        cx = help_crop(state)
    else:
        xs = [(ic["svg"][0], ic["svg"][0] + ic["svg"][2]) for ic in R["after"][state]["icons"]]
        cx = (min(x for x, _ in xs) - 40, max(x for _, x in xs) + 40)
    bi, bn = annotated("before", state, cx)
    ai, an = annotated("after", state, cx)
    blocks.append(f"""<div class='blk'><div class='t'>{title} <span>{('— ' + what) if what else ''}</span></div>
      <div class='pair'><div><div class='h'>before</div>{bi}<div class='cap'>{bn}</div></div>
      <div><div class='h fx'>after</div>{ai}<div class='cap'>{an}</div></div></div></div>""")

html = f"""<html><head><link href='https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=block' rel='stylesheet'><style>
body{{margin:0;background:#FAF7F2;color:#3D3328;font-family:Inter,sans-serif;width:1240px}}
.wrap{{padding:22px 26px 28px}} h1{{font-size:20px;margin:0 0 6px}}
.lede{{font-size:13px;color:#5a4e42;margin:0 0 6px;line-height:1.45}}
.blk{{margin-top:16px}} .t{{font-size:14px;font-weight:600;margin-bottom:4px}} .t span{{font-weight:400;color:#5a4e42}}
.pair{{display:flex;flex-direction:column;gap:6px}} .h{{font-size:11.5px;color:#8a7e72}} .h.fx{{color:#2e7d4f;font-weight:600}}
img{{display:block;image-rendering:pixelated;border:1px solid #e6dfd4;border-radius:4px;max-width:1188px}}
.cap{{font-size:11.5px;margin-top:2px}} .cap .n{{color:#5a4e42}} .bad{{color:#c0392b}} .ok{{color:#2e7d4f}}
</style></head><body><div class='wrap'>
<h1>Glyph pass: every icon measured in the real UI, before and after</h1>
<p class='lede'>Isolated tinkerscope on a copy of your workspaces, dpr 2, pixels ×{UP}. Per icon: blue = top and bottom of its ink,
solid red = the ink's centre, dashed red = what it should be centred on: the drawn border of its button, or for a borderless button its box,
or for an icon beside text the middle of that text's caps. Off-centre is flagged at 0.3 px or more.</p>
{''.join(blocks)}
</div></body></html>"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1240, "height": 800}, device_scale_factor=2)
    pg.set_content(html)
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(300)
    pg.screenshot(path=str(OUT), full_page=True)
    b.close()
print(OUT)
