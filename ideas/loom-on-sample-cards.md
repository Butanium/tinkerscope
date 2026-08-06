# Loom directly from an n>1 sample card

The loom's pin-to-branch is wired on the SINGLE committed row only (v1 scope
call): the sample-card token views (`ChatMessage`'s `sampleCard` snippet — the
eye view and the n>1 bucket) render `TokenLogprobs`/`TokenHeatOverlay` with no
`onLoom`, so clicking a token in a card does nothing. Today's workflow is: pick
the card (make it active) → collapse to the single row → loom. Two steps that
could be zero.

Plumbing is small and all the pieces exist: the card mounts already compute the
per-card display stream + `cLoomCut`; a card's node id is
`msg.sampleNodeIds[idx]`, so +page's `onLoom` closure needs a sample-index
variant (the `onTag(sampleIndex, …)` shape is the precedent) and ChatMessage
forwards `(idx, cut, altTid)` for committed cards (bucket cards without a node
id stay inert). `branchOps.loomBranch` already fires by node id via `#loomFire`.

Worth doing when someone actually reaches for it mid-eye-view; not before —
the collapse-then-loom path is fine and the card toolbar is already dense.

*(fable, 2026-08-06, the loom-ship session — deferred from v1 deliberately)*
