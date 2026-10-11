"""Render every glyph in web/src/lib/Icon.svelte to one PNG, light over dark.

The check docs/GLYPHS.md asks for: look at a new or changed glyph at the sizes
the app draws it, next to its neighbours, in both themes.

    uv run python scripts/glyph_sheet.py /tmp/glyphs.png
    uv run python scripts/glyph_sheet.py /tmp/old.png --icon /path/to/old/Icon.svelte
"""

import argparse
import re
from pathlib import Path

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
        browser.close()
    print(f"{len(items)} glyphs → {a.out}")


if __name__ == "__main__":
    main()
