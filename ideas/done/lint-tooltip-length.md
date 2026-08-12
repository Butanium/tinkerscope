## Lint the tooltip length rule

CLAUDE.md says tooltips are ONE short line (~70 chars) because a long one renders
as an ugly slab over the UI, but nothing enforces it and the six worst offenders
had accreted quietly over months. A ~20-line node test in `web/src/lib/` (or a
pre-commit grep) that parses `data-tooltip="..."` literals out of
`web/src/**/*.svelte` and fails over ~90 chars would catch the next one at write
time. Ternaries need care — measure each branch, not the whole expression.

*(opus-5, 2026-07-24, help-modal / tooltip session)*

**Done 2026-08-12**: `web/src/lib/tooltip-length.test.ts` (runs under the plain
`npm test` glob). Parses every `data-tooltip=` in `web/src/**/*.svelte` — a
`{...}` value per STRING LITERAL, so a ternary is judged by the branch that
renders, and `${…}` counts as one char — and fails over 90 chars or on a
multi-line value, naming the offending branch's own line. The tree is clean at
103 tooltips; verified to fire on all three shapes (plain, ternary branch,
multi-line) before shipping.
