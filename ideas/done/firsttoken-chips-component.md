## `ChartModal.svelte` is at ~850 lines and has a second component inside it

The first-token chip row (exclude / drag-merge / unmerge / added-token search +
its ~40 lines of CSS) is self-contained and shares nothing with the plot except
`ft` and four module-scoped arrays — a `FirstTokenChips.svelte` taking
`{chips, onexclude, onmerge, …}` would cut the file by a third and make the
bespoke onto-drop DnD testable on its own. Not urgent; the file is sectioned and
navigable.

**See also:** [split-mega-files](split-mega-files.md) — same genre, bigger files.

*(opus-5, 2026-07-29, chart split / persistence session)*

**Done 2026-08-12**: extracted to `lib/FirstTokenChips.svelte` (chips + the
onto-drop merge DnD + the add-token search, ~40 lines of CSS with them); it owns
the transient state (drag target, query) and takes the units + callbacks as
props. `FtChip` is exported from its `<script module>`. The three
`.chart-legend-{item,swatch,label}` rules moved to `app.css` — a scoped rule
can't reach an extracted component. Pure refactor; `browser_chart_firsttoken_ops`
+ `browser_chart_modal` pass unchanged.
