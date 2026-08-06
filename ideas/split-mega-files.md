## Split `+page.svelte` (2.2k) and `cli.py` (2.3k) — the two remaining mega-files

Both have clean seams now.

`+page.svelte`: the sidebar block (models + params + highlight rules) →
`Sidebar.svelte`, and the composer row (prefill/system split-chips + send targets
+ textarea) → `Composer.svelte`; that's most of the markup and would leave +page
as wiring.

`cli.py`: the table / tree PRINTERS (`_show_workspace`, `_list_workspaces`,
`_show_samples`, the state digest) are ~40% of the file and depend on nothing but
dicts → `cli/_render.py`, leaving the commands thin.

Neither needs a design decision, both are mechanical with tests already covering
behavior. Clément flagged the cli.py size on 2026-07-24.

**See also:** [firsttoken-chips-component](firsttoken-chips-component.md) — same
genre, one size down.

*(opus-5, 2026-07-24)*
