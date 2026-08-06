## Score the CONTEXT, not just the completion

Clément wants to think more before this is built. Attribution matters in this
one: the idea and its shape are his, the hedging below is opus-5's.

*The mechanism (opus-5).* `_token_logprobs` already re-submits prompt+completion
as one prompt with `include_prompt_logprobs` + `topk_prompt_logprobs`, reads
positions `[L, L+T)` for the generated tokens, and throws `[0, L)` away. Those
discarded positions are the whole rendered context — system prompt, user turns,
prior assistant turns, template scaffolding, and (on a Continue) the assistant
PREFILL region. Real teacher-forced numbers, already paid for, zero extra API
calls. opus-5 raised the prefill slice; Clément's "so we can collect the user
prompt logprobs too?" is what turned it into this idea.

*Smallest first step (opus-5).* The prefill region: today a continued turn
stores logprobs for the continuation ONLY, so its token view starts mid-turn
and the text you prefilled has no numbers at all. Slicing
`[L-len(region_ids), L)` fixes exactly that, and needs no design decisions.

*Clément's design.* Capture rule: if the context has no token probs yet, take
them while sampling the response; if it already has them, don't re-store. He
also flagged the catch himself — you then have logprobs on EDITED tokens, which
"could be a bit misleading, in the sense that it looks like the model sampled
this". His tentative render, explicitly not final: `[prefill with probs] [ghost
tokens from the edit] [completion token probs]`, maybe plus a button to compute
the full logprobs of a conversation on demand.

*Storage is not an objection — opus-5 argued it was, and was wrong.* Recorded so
nobody re-derives it: the context is deterministic given the tree, so it's
stored once per (prompt, checkpoint), and the N-samples-of-one-prompt case (30,
100 samples of the same question) is precisely where it amortizes rather than
multiplies. Bound ~2× a completion's worth, usually well under.

*opus-5's open questions — Clément is not blocked on these and does not share
the caution; treat them as a checklist, not a gate.* Whether hand-edited spans
stay ghosts, get a distinct third style ("scored, not sampled"), or become a
toggle — note `ghost` currently means "no number exists", while here one WOULD
exist, so the render becomes a choice about what to claim. Whether context
scoring rides along with sampling or is its own `max_tokens=1` action (the
latter needs no generation and would let the SAME context be diffed across two
checkpoints, which the ride-along can't do cleanly since each panel samples its
own). And whether `top` is stored for context positions or only `lp` (the
alternatives are the expensive part; a surprisal heat map only needs `lp`).

*Interim shipped 2026-08-03 (fable-5): the display side of the prefill slice.*
`lib/token-prefill.ts` now prepends the node's persisted `prefill` as one
leading ghost (`ghostKind: 'prefill'`) at render time, so a Continue'd turn's
overlay aligns instead of warning and the stream view shows the prefix dimmed.
That IS Clément's tentative render with the prefill's probs set to "none yet" —
when the real `[L-len(region_ids), L)` slice lands, replace the synthesized
ghost with stored prefill entries and the views need no further change.

*(Clément's idea, opus-5's hedging, 2026-08-03, edit-keeps-logprobs session — design deliberately unsettled, he wants to think more first)*
