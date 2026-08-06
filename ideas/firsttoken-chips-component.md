## `ChartModal.svelte` is at ~850 lines and has a second component inside it

The first-token chip row (exclude / drag-merge / unmerge / added-token search +
its ~40 lines of CSS) is self-contained and shares nothing with the plot except
`ft` and four module-scoped arrays — a `FirstTokenChips.svelte` taking
`{chips, onexclude, onmerge, …}` would cut the file by a third and make the
bespoke onto-drop DnD testable on its own. Not urgent; the file is sectioned and
navigable.

**See also:** [split-mega-files](split-mega-files.md) — same genre, bigger files.

*(opus-5, 2026-07-29, chart split / persistence session)*
