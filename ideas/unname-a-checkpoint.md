## Naming a checkpoint is a one-way write

`POST /api/tinker-models/name` (shipped 2026-08-10) upserts a human label into
`pack_models_store`. There is no route that removes one. So:

- Name a checkpoint badly and you cannot fix it from the UI. The only repair is
  editing `<state_dir>/pack_models.json` by hand, which is not a thing to tell a
  user to do.
- Re-naming works (`upsert` replaces by `(kind, ref)`), so the gap is specifically
  *removal*, not correction — but you also cannot get back to "unnamed", which is
  the state that makes the prompt offer itself again.
- `browser_tinker_custom_ckpt.py` inherits the gap: its green section has to skip
  when a prior run on the same state dir already registered the foreign checkpoint,
  because the add row correctly disappears once the catalog holds it. A fresh state
  dir is the current workaround.

Shape, if someone builds it: `DELETE /api/tinker-models/name?kind=&ref=` plus a
`pack_models_store.remove()`, and somewhere to reach it — a ✕ on a `named` row in
the tinker picker is the obvious spot, though that row is also where you'd want a
rename affordance, so the two probably want designing together rather than one
at a time.

One thing to decide, not obvious: a **pack-shipped** label and a **user-given** one
are the same record today (both are just entries with `pack:true` from
`tinker_model_entries`). Deleting a pack-shipped label means the next
`pack apply`/`--reseed` puts it straight back, which will read as the delete not
working. Either distinguish the two sources when storing, or say plainly in the UI
that a pack-supplied name returns with the pack.

Not urgent: naming is opt-in, skipping is one click, and re-naming already works.
It becomes urgent the first time someone types a name they regret in front of
someone else.

— Claude Opus 5, 2026-08-10
