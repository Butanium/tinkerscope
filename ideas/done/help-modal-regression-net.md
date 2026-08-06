## The `?` modal has no visual regression net

*(opus-5, 2026-07-24, help-modal / tooltip session)*

**Done 2026-07-24**, same session: `browser_help_modal.py` now asserts every
`.help-chip` actually rendered an `svg` — a name `Icon.svelte`'s `{#if}` chain
doesn't know emits NOTHING, and `npm run build` doesn't typecheck, so
`npm run check` was the only guard.
