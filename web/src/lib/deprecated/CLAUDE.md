# Deprecated frontend modules

- `save-plan.ts` — the storage-v2 save-request planner (dirt → one PUT/PATCH) +
  post-save lightening helpers. Deprecated 2026-08-12: the ops cutover
  (HANDOFF_SERVER_AUTHORITY P1) replaced dirt-accumulation persistence with
  per-mutation op emission (`lib/ops.svelte.ts` + `setTree`'s ops option);
  `heavyNodeIds`/`lightenTree` moved to `lib/tree.ts` (folds lighten at birth).
- `save-plan.test.ts` — its unit suite, moved with it (kept runnable:
  `node deprecated/save-plan.test.ts`).
