## Per-row availability tooltip = the real reason

The typeahead row's unavailable-tooltip is static generic copy ("base not served
or weights no longer exist"), while the backend already sends the precise per-run
`unsampleable_reason` (the sidebar warn uses it). Threading it through
`ModelItem` (catalog builder → typeahead `title`) would tell you WHICH constraint
binds at hover time.

*(fable, 2026-07-21 — small)*
