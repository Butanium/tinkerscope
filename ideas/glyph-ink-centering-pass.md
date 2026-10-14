## Bring the older glyphs inside the ink rules

`docs/GLYPHS.md` says a glyph's ink (stroke included) stays inside 1–15 of the
16 box and is centred on y = 8 (±0.3). `scripts/glyph_sheet.py` prints every
glyph that breaks it. On 2026-10-13 these still did:

| glyph | ink (16-units) | what's off |
|---|---|---|
| `regen`, `replace` | y 0.4–15.6 | arrow heads past the margin (converted 14→16 box, scaled to the edge) |
| `continue-think` | y 1.3–15.2, centre 8.25 | the trailing thought-bubble dots pull it low |
| `check` | y 4.3–12.7, centre 8.5 | sits low |
| `pins` | x 0.8–15.2 | frame past the side margins |
| `dataset` | y 1.0–16.0, centre 8.5 | runs off the bottom and sits low |
| `undo` | centre 7.55 | sits high |
| `new-blank` | y 0.8–13.2, centre 7.0 | a full unit high |
| `theme-light` | 0.3–15.7 both axes | rays to the edge |
| `eye` | x 0.3–15.7 | wider than the box allows |

Why it matters: the brain glyph sat 0.25 low in its box and hung 1.25px under the
baseline next to "ASSISTANT". Clément had to draw the guide lines in Paint to show
it. Any glyph that sits next to text (chips, the topbar, the Help modal legends)
has the same exposure.

How: per glyph, scale/translate the path coordinates (an affine on the `d` strings
works, skipping arc rotation and flags; the brain was done that way). Re-run the
sheet, then look at each one IN PLACE with caps-top/baseline guides drawn. A glyph
whose thin strokes sit at its top (arcs, rays) reads lower than its geometric
centre at 11–13px, so geometric centring is the floor, not the finish.

*(opus-5.5, 2026-10-13, prefill/glyph session)*
