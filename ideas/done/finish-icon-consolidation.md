## Finish the icon consolidation

`lib/Icon.svelte` now owns the row-toolbar + sidebar glyph set (so the `?` modal
draws the SAME button the toolbar does), but ~22 inline `<svg>` remain outside it
— and the drift it exists to prevent is already there: `HighlightRules.svelte`
draws its own pencil (`M11.5 2.5l2 2L6 12…`) and trash, geometrically different
from Icon's `edit` / `trash`, for the same two verbs. Worth folding those in
(plus +page's reduce/restore/panel-send arrows). Chevrons and the drag grips are
structural, not iconography — leave them.

*(opus-5, 2026-07-24, help-modal / tooltip session)*

**Done 2026-08-12**: `HighlightRules`' divergent pencil + trash now draw
`Icon`'s `edit` / `trash`, and +page's reduce/remove/restore/panel-send glyphs
became `minus` + a shared `arrow-right`. What's left inline is deliberately out
of scope: chevrons (they rotate / size to their container), the two drag grips,
`ActionMenu`'s single ⋯, and ChartModal's actual plot `<svg>`.
