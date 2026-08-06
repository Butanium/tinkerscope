## Layout history / undo for a workspace

A workspace's panel layout can be clobbered with no way back — worth recording
every change so a bad save is recoverable.

*(opus-5, 2026-07-24)*

**Done 2026-07-24**: `<state>/workspaces/<id>.layouts.jsonl` — `{ts, panels}`
appended on every layout CHANGE (not every save), capped at 50, recorded in
`workspace_store._record_layout` off the single `_persist` choke point. Read via
`GET /api/workspaces/{id}/layout-history`; browse/restore with
`scripts/layout_history.py`. Tests: `tests/test_layout_history.py`. Note it is
NOT backfilled — history starts at the first layout change after it shipped.
