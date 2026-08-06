## For anything PAINTED rather than DOM'd, the assertion is pixel readback

The overlay draws on a canvas, so every DOM-shaped assertion a smoke can make
("the element exists", "it has a style attribute") is blind to whether anything
was actually drawn. `browser_token_overlay.py` reads the canvas back instead —
`getImageData` over the whole surface, count non-zero-alpha pixels; and for
"which color", sample the single pixel under a named element's centre and compare
channels. That caught a real bug (see
[screenshot-verify-second-vote](screenshot-verify-second-vote.md)) and is the
cheap general technique for canvas/SVG-paint features.

Worth remembering the two forms: a COUNT for "did it draw at all", a single-pixel
SAMPLE for "did it draw the right thing". Note both are immune to Playwright's
click-through-scroll problem, since neither involves clicking.

*(opus-5, 2026-08-03, token-overlay session — partly applied, in browser_token_overlay.py)*
