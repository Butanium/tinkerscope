## Second independent vote for "screenshot-verify every UI change"

[screenshot-verify-ui-changes](screenshot-verify-ui-changes.md) (per-panel-stop
session, same day) says a screenshot found a misplacement no assertion would
have. This session it happened again in a different genre: the first overlay used
one canvas per row, which painted *behind* `.sample-reasoning`'s OPAQUE
background — so every thinking block came out completely flat while the response
below it painted correctly. The smoke passed (it asserted on the message body's
canvas), svelte-check was clean, and the code reads fine; only looking at a
picture of a real workspace showed a whole region of the feature silently not
working. Two sessions, two different failure modes, both invisible to everything
except a rendered image — that's not a coincidence, it's the category.

The fix here was a per-container canvas at `z-index: -1`, and the smoke now pins
it with a pixel-count on the reasoning block's own canvas.

*(opus-5, 2026-08-03, token-overlay session)*
