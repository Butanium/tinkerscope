## Give the surprisal tint the same ramp knob

`surprisalAlpha` is hardcoded linear-in-(-logprob), saturating at -lp 6. Same
class of complaint the Contrast slider just fixed for match tint: sometimes you
want "show me anything at all unusual" (a step), sometimes the relative shape. If
it happens, share the control rather than growing a second slider — one "tint
ramp" that applies to whichever mode is active.

*(opus-5, 2026-07-24, match-tint contrast session)*
