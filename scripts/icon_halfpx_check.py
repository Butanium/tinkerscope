"""List every icon on a half pixel inside its button, or in a button on a half pixel inside its row.

    uv run python scripts/icon_halfpx_check.py http://127.0.0.1:8793/?w=<id> [more URLs]

Chrome draws an <svg> at a whole CSS pixel even at dpr 2, so an icon whose box is inset
x.5 px inside its button is drawn half a pixel off-centre. That happens whenever the icon
size and the button's inner size differ in parity: a 13 px icon in a 28 px button, a 15 px
one in a row of 30 px buttons. docs/GLYPHS.md has the rule. Each URL is checked plain,
with Shift held, with the Help modal open, in the sample-card view and in the chart
modal. Point it at an isolated instance (scripts/dev-isolated.sh), never the live one.
Exit 1 if anything is found.
"""

import sys

from playwright.sync_api import sync_playwright

# icons that are deliberately not centred in their box (top of a tall folded-panel strip)
SKIP = {"restore-panel"}

JS = """() => {
  const out = [];
  for (const svg of document.querySelectorAll('svg')) {
    const s = svg.getBoundingClientRect();
    if (s.width < 8 || s.width > 24 || s.height === 0) continue;
    const st = getComputedStyle(svg);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    const btn = svg.closest('button, a, [role=button], .help-chip, .chip');
    if (!btn) continue;
    const b = btn.getBoundingClientRect();
    const half = (v) => Math.abs(((v * 2) % 2 + 2) % 2 - 1) < 0.01;
    // the icon inside its button, or the button inside its row (a 29 px button in a 30 px row)
    const where = half(s.top - b.top) ? 'icon in button' : half(b.top - btn.parentElement.getBoundingClientRect().top) ? 'button in row' : null;
    if (!where) continue;
    const cls = String(btn.className).split(' ').filter(c => c && !c.startsWith('svelte-'))[0] || btn.tagName;
    out.push({cls, where, label: (btn.getAttribute('aria-label') || btn.getAttribute('data-tooltip') || '').slice(0, 40), size: s.height, bh: b.height});
  }
  return out;
}"""


def main() -> None:
    urls = sys.argv[1:]
    if not urls:
        sys.exit(__doc__)
    found = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": 1500, "height": 950}, device_scale_factor=2).new_page()

        def sweep(state: str) -> None:
            for r in page.evaluate(JS):
                if r["cls"] not in SKIP:
                    found.setdefault((r["cls"], r["where"], r["size"], r["bh"]), set()).add(f"{state}: {r['label']}")

        for url in urls:
            page.goto(url)
            page.wait_for_selector(".sidebar-top-actions", timeout=60000)  # no networkidle: the state bus is a held-open SSE stream
            page.wait_for_timeout(2500)
            sweep("plain")
            page.keyboard.down("Shift")
            page.wait_for_timeout(250)
            sweep("Shift")
            page.keyboard.up("Shift")
            page.locator('button[aria-label="Help"]').first.click()
            page.wait_for_timeout(400)
            sweep("help")
            page.locator(".modal-overlay").first.click(position={"x": 5, "y": 5})
            page.wait_for_timeout(300)
            eye = page.locator('[aria-label="View all samples"]')
            if eye.count():
                eye.first.evaluate("e => e.click()")
                page.wait_for_timeout(500)
                sweep("sample cards")
            page.locator(".sidebar-top-actions button").first.click()
            page.wait_for_timeout(500)
            sweep("chart")
            page.locator(".modal-overlay").first.click(position={"x": 5, "y": 5})
        browser.close()
    for (cls, where, size, bh), seen in sorted(found.items()):
        print(f"{cls} ({where} on a half pixel): {size:g} px icon, {bh:g} px button — {', '.join(sorted(seen)[:3])}")
    print(f"{len(found)} half-pixel icon placement(s)")
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
