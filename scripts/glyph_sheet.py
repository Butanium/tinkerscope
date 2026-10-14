"""Render every glyph in web/src/lib/Icon.svelte to one PNG, light over dark.

The check docs/GLYPHS.md asks for: look at a new or changed glyph at the sizes
the app draws it, next to its neighbours, in both themes.

    uv run python scripts/glyph_sheet.py /tmp/glyphs.png
    uv run python scripts/glyph_sheet.py /tmp/old.png --icon /path/to/old/Icon.svelte

It also prints every glyph whose ink (stroke included, measured at 160 px) leaves
the 1–15 live area or sits more than 0.3 units off the box centre vertically. An off-centre glyph
looks low or high next to text even when its box is centred perfectly.
"""

import argparse
import io
import re
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
THEMES = (("#FAF7F2", "#8a7e72"), ("#1a1a1a", "#888"))  # app.css bg / text-muted


def glyphs(src: str) -> list[tuple[str, str]]:
    """(name, svg with a SZ placeholder) per IconName, in file order."""
    body = src.split("</script>", 2)[2]
    parts = re.split(r"\{(?:#if|:else if) (name === '[^}]+)\}", body)
    out = []
    for cond, chunk in zip(parts[1::2], parts[2::2]):
        svg = re.search(r"<svg.*?</svg>", chunk, re.S).group(0).replace("{size}", "SZ")
        for name in re.findall(r"'([^']+)'", cond):
            # the one runtime expression in the file: sidebar-unfold is sidebar-fold mirrored
            flip = 'style="transform: scaleX(-1)"' if name == "sidebar-unfold" else ""
            out.append((name, re.sub(r"style=\{[^}]*\}", flip, svg)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("--icon", type=Path, default=REPO / "web/src/lib/Icon.svelte")
    ap.add_argument("--sizes", default="12,13,40", help="px sizes per glyph (the app draws 11–16, default 13)")
    ap.add_argument("--cols", type=int, default=8)
    a = ap.parse_args()
    sizes = [int(x) for x in a.sizes.split(",")]
    items = glyphs(a.icon.read_text())

    def cell(name: str, svg: str) -> str:
        small = "".join(svg.replace("SZ", str(z)) for z in sizes if z < 24)
        big = "".join(svg.replace("SZ", str(z)) for z in sizes if z >= 24)
        return f'<div class="c"><div class="sm">{small}</div><div>{big}</div><span>{name}</span></div>'

    grid = "".join(cell(n, s) for n, s in items)
    css = (
        "body{margin:0;font:11px sans-serif}"
        f".g{{display:grid;grid-template-columns:repeat({a.cols},1fr);gap:4px;padding:10px}}"
        ".c{display:flex;flex-direction:column;align-items:center;gap:6px;padding:6px}"
        ".sm{display:flex;gap:8px;align-items:center;min-height:16px}"
    )
    html = f"<style>{css}</style>" + "".join(
        f"<div class='g' style='background:{bg};color:{fg}'>{grid}</div>" for bg, fg in THEMES
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 110 * a.cols, "height": 300}, device_scale_factor=2)
        page.set_content(html)
        page.screenshot(path=str(a.out), full_page=True)
        bad = []
        probe = browser.new_page()
        for name, svg in items:
            probe.set_content(f"<body style='margin:0;background:#fff;color:#000'><div style='display:inline-block'>{svg.replace('SZ', '160')}</div></body>")
            img = Image.open(io.BytesIO(probe.locator("svg").screenshot())).convert("L")
            w, h = img.size
            px = img.load()
            rows = [y for y in range(h) if any(255 - px[x, y] > 60 for x in range(w))]
            cols = [x for x in range(w) if any(255 - px[x, y] > 60 for y in range(h))]
            if not rows:
                continue
            k = 16 / h
            y0, y1, x0, x1 = rows[0] * k, (rows[-1] + 1) * k, cols[0] * k, (cols[-1] + 1) * k
            if min(y0, x0) < 1.0 or max(y1, x1) > 15.0 or abs((y0 + y1) / 2 - 8) > 0.3:
                bad.append(f"  {name}: ink x {x0:.1f}–{x1:.1f}, y {y0:.1f}–{y1:.1f} (centre y {(y0 + y1) / 2:.2f})")
        browser.close()
    if bad:
        print("outside the live area / off-centre (docs/GLYPHS.md):")
        print("\n".join(bad))
    print(f"{len(items)} glyphs → {a.out}")


if __name__ == "__main__":
    main()
