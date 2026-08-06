## Show the resolved base model on loose-ckpt panels

A loose `ckpt:` panel now resolves its base model server-side
(`resolve_base_model`) but the label still reads just the UUID/checkpoint. The
frontend could fetch + show the resolved base (e.g. "…final-step-100 · Inkling")
and the `supports_thinking` flag, so loose ckpts get the same affordances
(thinking toggle visibility, family label) as discovered runs. The value is on
the backend already; it's a labeling/plumbing pass to surface it.

*(opus-4.8, 2026-07-23, Inkling / loose-ckpt session)*
