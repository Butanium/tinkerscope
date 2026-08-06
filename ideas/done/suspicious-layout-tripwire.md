## A "suspiciously large layout change" tripwire

*(opus-5, 2026-07-24)*

**Done 2026-07-24**, alongside [layout history](layout-history-undo.md):
`workspace_store._suspicious_layout_change` logs a warning when a save replaces a
≥2-panel layout with another ≥2-panel one sharing NO model — the clobber's shape,
which no human action produces. Quiet for one-panel swaps, adds/removes,
reorders, and blank→filled.
