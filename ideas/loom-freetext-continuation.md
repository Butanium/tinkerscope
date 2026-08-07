## A text bar in the pinned loom popover: continue from words you type

Today the pinned token popover offers exactly the top-K alternatives the capture
recorded (5 of them) plus "resample from this token". So the counterfactual you
can pose is bounded by what the model already put in its top-5 — which is the
right default (every alternative comes with a real probability), but it means the
question "what if it had said *this* instead" only has an answer when the model
was already considering it.

**The idea** (Clément, 2026-08-06): add a free-text input to the pinned popover.
You type whatever you want, and the branch continues from the replayed prefix
with your text spliced in at the cut.

**Why it's cheap.** The hard half already shipped. `branchOps.loomBranch` replays
`tlp[0..cut]`'s stored `tid`s through `ChatRequest.continue_tokens`, anchored to
the turn's own model + renderer. A picked alternative is just "replace the tid at
the cut". Free text is the same request with a *sequence* of tids appended
instead of one — so the only genuinely new piece is **tokenizing the typed string
under that turn's tokenizer** and handing back the ids. That has to happen
server-side (the browser has no tokenizer, and it must be the SAME tokenizer that
produced the stored ids — `lib/loom.ts`'s `parseRawMetaModel` already recovers
which one from the turn's `raw_meta`, for exactly this reason). Clément's read:
"it's not a token request that you send, you need tokenization, but should be
fine."

**Sketch.**

- Backend: an endpoint that takes `{base_model | sampler_path, renderer, text}`
  and returns `tids`. The sampler already holds the tokenizer per model; this is
  a thin wrapper, not new machinery.
- `TokenPopover` (pinned mode): an input + a "continue from this" button next to
  the alternatives row. Empty input ⇒ unchanged behaviour.
- The resulting node is a loom branch like any other: `loom_cut` counts the
  forced entries (prefix + however many tids the text became), `loom_text` is the
  prefix plus the typed text, so the existing prefill-style tinting, the
  dot-underline and the fork tick all work with no display changes.

**Two things to decide when someone builds it.**

1. *What probability does the typed text carry?* Nothing, by default — the same
   ghost treatment as an edit (`token-edit.ts`). But this is precisely the case
   [score-authored-ghosts.md](score-authored-ghosts.md) wants to solve: the loom
   endpoint could teacher-force the typed tids and hand back their real logprobs,
   so you'd immediately see "the model would never have said that" as an orange
   glow. These two ideas want to ship together.
2. *Whitespace.* A BPE token carries its leading space, and the cut lands mid-
   stream. Typing `blue` where the stream had ` blue` tokenizes differently and
   will silently produce a word-joined mess. Whatever the UI does here (show the
   resolved tokens back before firing? normalize the leading space against the
   token being replaced?) it should not be guessed at silently — the whole point
   of the loom is that the prefix is exact.

Deferred deliberately — Clément: "but not for today."

— fable (claude-opus-5), 2026-08-06
