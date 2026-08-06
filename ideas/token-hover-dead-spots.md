## Token overlay: hovering a word sometimes yields no popover

While aiming the README token shot at "cigarette" (range-center of the word in
the last `.message-content`), no `.tok-pop` appeared; the same code on "good" two
words earlier worked. Didn't investigate — plausible suspects: that token
null-mapped by the aligner (no rect cached → no hover), or the range center
falling in an inter-rect gap. If a user reports "hover does nothing on some
words", start here.

*(fable-5, 2026-08-05)*
