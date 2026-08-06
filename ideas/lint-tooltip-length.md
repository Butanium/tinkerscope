## Lint the tooltip length rule

CLAUDE.md says tooltips are ONE short line (~70 chars) because a long one renders
as an ugly slab over the UI, but nothing enforces it and the six worst offenders
had accreted quietly over months. A ~20-line node test in `web/src/lib/` (or a
pre-commit grep) that parses `data-tooltip="..."` literals out of
`web/src/**/*.svelte` and fails over ~90 chars would catch the next one at write
time. Ternaries need care — measure each branch, not the whole expression.

*(opus-5, 2026-07-24, help-modal / tooltip session)*
