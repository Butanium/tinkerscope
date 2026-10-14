## Measure icons in place with a real script, not a session prototype

`scripts/icon_halfpx_check.py` catches one class (half-pixel placement) from the DOM
alone. The 2026-10-14 pass also needed the screenshot measurement in
`scratch/glyph_inplace/`: ink centre per icon, sub-pixel, against the button's drawn
border or the caps of the text beside it. That is what found continue-think's body
sitting high, undo getting worse after its fix, and the Help prose icons riding 2 px
high. It is hardcoded to Clément's workspaces and one port.

A `scripts/glyph_inplace.py <url>` would discover states the way the half-pixel check
does (plain, Shift, Help open, sample cards, chart), measure every visible icon, and
flag anything ≥0.3 px off, with an annotated before/after sheet when given two builds.

Also open:
- the half-pixel check only looks at y. The same rounding moves an icon 0.5 px
  sideways when its inset is x.5 horizontally. Untested how many sites that hits, or
  whether a sideways half pixel inside a button is visible at all.

Pitfalls the prototype hit, so the script doesn't: a Playwright clip with negative y
is silently clamped (every measurement shifts); a locator that matches on a
Shift-only class goes stale the moment Shift is released (take an element handle
first); lowercase neighbour text puts the "caps" reference ~0.25 px high (ascenders);
the area method reads a pointed tip or a faded part as thin ink.

*(opus-5.5, 2026-10-14, glyph pass session)*
