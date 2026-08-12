## Token overlay: hovering a word sometimes yields no popover

While aiming the README token shot at "cigarette" (range-center of the word in
the last `.message-content`), no `.tok-pop` appeared; the same code on "good" two
words earlier worked. Didn't investigate — plausible suspects: that token
null-mapped by the aligner (no rect cached → no hover), or the range center
falling in an inter-rect gap. If a user reports "hover does nothing on some
words", start here.

*(fable-5, 2026-08-05)*

**Done 2026-08-12**: neither suspect. `TokenHeatOverlay.measure()` only built a
box for a token that HAD a color, and the boxes are also the hover hit-test —
while `surprisalAlpha` rounds to 0 for anything the model gave p > 93.55%. So
every word the model was confident about was hover-dead, and its less-predictable
neighbours worked; "cigarette" in a smoking answer is exactly that case. A box
now marks where a token IS, not whether it was painted (unaligned tokens are
still skipped — they have no position). Painting is unchanged: `paint()` already
no-ops on empty bands. Pinned in `browser_token_overlay` by a p=.99 fixture token
("mostly", tinted with nothing) that must still open its popover.
