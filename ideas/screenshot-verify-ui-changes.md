## Screenshot-verify every UI change, not just plots

The memory note says it for plots; this session it was a UI control, and the
screenshot is the only thing that found the misplacement (smokes passed on both
placements — `data-testid` was present and clickable either way, because
playwright clicks through the scroll). If a browser smoke asserts a control's
BEHAVIOR, consider also asserting it's in the viewport (`bounding_box()` against
the scroll container) — that's the cheap encoding of what the screenshot taught.

**See also:** [screenshot-verify-second-vote](screenshot-verify-second-vote.md) —
independent recurrence the same day, different failure mode.

*(opus-5, 2026-08-03, per-panel-stop session — a practice, not code)*
