# Teacher-force the ghosts: score authored text instead of greying it

Today any text the model didn't sample — an edit's post-divergence tail
(`token-edit.ts`), a prefill (`token-prefill.ts`) — becomes a GHOST: dashed
underline, no fill, "no token data". Clément's variant (2026-08-06): compute
logprobs for it anyway. Teacher-force the whole turn (the `_token_logprobs`
re-submit pattern — prompt + full token sequence, `max_tokens=1`,
`include_prompt_logprobs + topk_prompt_logprobs`) and every authored token gets
a real number under the real context. A surprising insertion just glows very
orange — which is the point: "how off-policy is my edit?" becomes visible. The
provenance changes from "no data" to **"not sampled"**: the token keeps a
distinguishing mark (dashed underline stays) but gains the heat fill and a
popover that says authored + model p=X%.

The primitive is shared with [loom-branch-from-token](loom-branch-from-token.md):
the loom's backend (continue-from-tokens, scoring forced+sampled tokens in one
pass) IS "score these forced tokens under this context". Once that ships, what
remains here is:

- **Trigger** — when to score an edit: eagerly at edit time (one sampling call
  per edit, even if never looked at) vs lazily (a button / on opening the token
  view for a ghost turn). Lazy feels right; edits are frequent, inspection isn't.
- **Tokenization of authored text** — the model never sampled it, so we
  tokenize it fresh (that IS teacher forcing). Watch the boundary with the
  inherited sampled prefix, and tml_v0 special-token thinking blocks if the
  edit touches reasoning (tag renderers are trivial, tags are just text).
- **Wire provenance** — a per-entry flag (e.g. `src: sampled|forced`), since
  forced spans are arbitrary in an edited turn. The loom gets away with
  `raw_meta`-level provenance (one cut index); this can't.
- **Numbers churn** — rescoring replaces the prefix's stored lps with fresh
  same-forward-pass ones (matches to ~1e-2 per the `_token_logprobs`
  docstring); decide whether to keep or replace the originals.

**See also:** [score-the-context](score-the-context.md) — the prompt-side twin
(scoring `[0, L)`); this one is the completion-side: scoring what a HUMAN put in
the completion.

*(Clément's variant of the loom idea, filed by fable, 2026-08-06 — parked
because the loom lands the shared primitive first; Clément: "unsure" on this
one, main interest is the loom.)*
