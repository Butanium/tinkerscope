# Deprecated smokes

- `browser_save_lightening.py` — pinned the post-save lightening choreography
  (#lightenShipped: strip shipped heavies client-side after a successful PUT,
  keep them inline on failure so the dirt re-merge re-ships). Deprecated
  2026-08-12: P1 of the server-authority migration DELETED its subject — folds
  are light from birth, heavy fields ride the add_nodes op, save-plan/dirt
  retired to web/src/lib/deprecated/. The durability property it guarded now
  lives in the server's idempotent-replay blob repair
  (tests/test_workspace_ops.py::test_a_replay_repairs_a_node_whose_blob_never_landed)
  and browser_ops_convergence.py scenario C. It had also been skip-listed since
  2026-08-03 ("composer textarea never appears") — its choreography predated the
  detached-fire send path.
- `browser_storage_v2_monster.py` — pinned the storage-v2 SAVE-PATH diet (PUT /tree
  ships light trees, layout changes PATCH with zero tree bytes) on a monster
  workspace. Deprecated 2026-08-12: P1 retired the browser's PUT save path (ops
  emission has no whole-tree hot path to diet) and P3 removed the endpoint; the
  monster-scale MEMORY policy it also brushed is still real but untested — a
  future ops-scale smoke would be new work, not this one repaired.
