## Per-row availability tooltip = the real reason

The typeahead row's unavailable-tooltip is static generic copy ("base not served
or weights no longer exist"), while the backend already sends the precise per-run
`unsampleable_reason` (the sidebar warn uses it). Threading it through
`ModelItem` (catalog builder → typeahead `title`) would tell you WHICH constraint
binds at hover time.

*(fable, 2026-07-21 — small)*

**Done 2026-08-12**: `ModelItem`/`PickerItem` carry an optional `reason`,
`modelItems` fills it from the run's `unsampleable_reason`, and the typeahead
row's title reads `Not samplable — <reason>` (the generic sentence stays as the
fallback for a row with none). Pinned by the pre-existing
`browser_model_availability`, whose title assertion only ever accepted a
reason-string: it FAILS on main at that assertion and passes here (baselined
2026-08-12), and it's now in `smoke.sh`'s DEFAULT list.
