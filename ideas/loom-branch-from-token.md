## Loom: branch from a token into one of its alternatives

In Token-probs view, click a token to mark a cut point, then click one of the
alternatives in its top-5 popover — that fires a new sample whose prefill is
everything up to the cut plus the chosen token, so the model continues from the
counterfactual. "What if it had said *however* here?" is currently a manual
round-trip: read the popover, copy the text, hand-edit a prefill. Two clicks is a
different tool.

The plumbing mostly exists: prefill + `prefill_scope` already prefixes a turn,
`branchOps.continueSample` already forks a sibling from one sample, and
`TokenLogprobs` already knows each token's offset and its alternatives — so the
new part is a cut-point selection mode and rebuilding the prefix text from the
token stream (careful: the prefix must be the RAW text, thinking tags included,
not the rendered content) rather than new API surface. Sits naturally next to
the existing continue/fork vocabulary: the result should be an ordinary sibling
branch, cyclable with ‹k/N›, not a special kind of turn.

Open questions worth deciding before building: what a click means in the two
halves (mark cut vs pick alternative — a mode, or click-then-click as Clément
described); whether the branch is one sample or n; and whether an alternative
that isn't in the top-5 can be typed by hand (the top-K ceiling bites here too).

**See also:** [loom-cut-from-prose](loom-cut-from-prose.md) (where the cut point
should actually be picked), [topk-capture-configurable](topk-capture-configurable.md)
(the top-5 ceiling).

*(Clément, 2026-08-03, sidebar / per-panel-stop session)*

### Addendum: do it at the TOKEN level, not via text prefill

The "no new API surface" paragraph above steers toward text prefill; checking
the stored shapes flips that call. Alternatives persist as `[text, tid, lp]`
(`tinker_sampler.py` wire shape) and the stream carries `tid` per token, so the
counterfactual prompt is `prompt_tokens + generated_tids[:i] + [alt_tid]` —
exact, no re-tokenization drift at the cut. The construction pattern already
exists at `tinker_sampler.py:350` (the logprob-capture re-submit:
`ModelInput(chunks=[*prompt.chunks, EncodedTextChunk(tokens=...)])`); a
continue-from-tokens mode is that with real `max_tokens`. Token-level also makes
mid-thinking cuts trivial (special tokens ride along as ids — no reopened
`<think>`, no `_tml_continue` dance), and the branch comes out GHOST-FREE: the
prefix inherits its stored logprobs exactly (identical context AND tokens —
stronger than `token-edit`'s divergence guess), the picked alternative wears its
recorded top-K logprob, the continuation arrives with fresh ones. Fully
overlayable/chartable.

Two more design answers: clicking the SAMPLED token itself = resample-from-here
with no swap ("how locked-in was the rest?") — free once the endpoint exists.
And the fire must reuse the original turn's render context (thinking toggle
etc.), not the current sidebar state, or the inherited prefix numbers are lies.
Hand-typed off-top-5 alternatives: decline in v1, that's the existing
edit+continue path.

*(fable, 2026-08-06, after Clément re-raised the idea unprompted)*
