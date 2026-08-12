## Show the resolved base model on loose-ckpt panels

A loose `ckpt:` panel now resolves its base model server-side
(`resolve_base_model`) but the label still reads just the UUID/checkpoint. The
frontend could fetch + show the resolved base (e.g. "…final-step-100 · Inkling")
and the `supports_thinking` flag, so loose ckpts get the same affordances
(thinking toggle visibility, family label) as discovered runs. The value is on
the backend already; it's a labeling/plumbing pass to surface it.

*(opus-4.8, 2026-07-23, Inkling / loose-ckpt session)*

**Done 2026-08-12**: a `ckpt:` panel probes its path once
(`/api/tinker-models/probe`, cached per path in `modelCatalog.ckptBases`) and
shows `◇ <base model>` as its meta line; `ckptSupportsThinking` then gates the
composer's Thinking toggle the way a `base:` pick already was.
Second half needed a fix the idea didn't anticipate: the tinker catalog (where
per-family `supports_thinking` lives) is LAZY, so on a fresh load it was empty and
every base:/ckpt: panel silently defaulted to thinking-capable — hence
`ensureTinkerCatalog()`, called when any panel holds such a pick. Found by the new
smoke, `tests/small-smokes/browser_ckpt_base_label.py` (token-free, one metadata
probe; skips without a key or a resolvable checkpoint).
