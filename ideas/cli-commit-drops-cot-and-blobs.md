## A CLI-fired turn persists as content only — CoT, logprobs and provenance never reach the tree

`tinkpg send`/`continue`/`chat` commit their representative turn through
`_committed_turn` (`api/routes/chat.py:242`), which builds

```python
[*msgs, {"role": "assistant", "content": chosen}]
```

and nothing else. That list becomes `end_patch["messages"]`, goes onto the bus,
and is what the browser folds from (`workspaces.svelte.ts:1088` reads
`ps.messages`). So a turn the CLI produced reaches the saved tree carrying its
answer text and **none of** `reasoning`, `token_logprobs`, `raw_meta`.

Nothing in the pipeline before that point is at fault: the server already
requests logprobs unconditionally (`chat.py:154`, `logprobs: bool = True`), the
CLI's `--logprobs` is display-only and never goes on the wire, and the samples
stream back to the terminal complete. The heavy fields simply have no seat on
the commit patch. `PUT /tree` would strip them into blobs correctly if the light
node ever held them.

### How it presents

A live browser looks right and a refresh guts it, because the live view is the
bus stream and the reload is the store. Measured on a 12-panel workspace fired
from the CLI (3 sends × n=4, 96 samples): all 24 assistant nodes came back
`keys: ['content']`, `has_token_logprobs=0`, `has_raw_meta=0`.

Worst on a thinking-on fire. Two checkpoints in that run put their entire reply
inside the think block and returned an empty answer — real behavior, visible in
the terminal — which persisted as a node with `len(content) == 0` and no
reasoning. A blank row where the interesting result was.

Three consequences worth separating:

- **No CoT.** A thinking-on CLI fire is unreadable after reload.
- **No token data.** The token inspector and the chart's *first token* mode are
  dead on these nodes, and `tinkpg samples --first-token` reports "none
  captured" — which reads as an OpenRouter/streaming limitation rather than a
  commit-path gap.
- **No provenance.** `raw_meta` is what the skill tells agents to check before
  quoting a stored turn as evidence about a model. CLI-committed nodes are
  permanently unverifiable by that rule, and they are exactly the nodes an agent
  produced.

### Shape of the fix

1. `_committed_turn` returns the assistant entry with `reasoning` (and, when the
   sample carries them, `token_logprobs` / `raw_meta`).
2. The bus `Msg` type widens to carry them — this is the contract edit, shared by
   browser and CLI, so it wants its tests.
3. `reconcileExternal` maps the extra fields onto the light node; `PUT /tree`'s
   existing write-once strip does the rest with no new storage work.

Worth deciding at (1) whether the blobs ride the bus at all — a fan-out's
logprob arrays are large and the bus is a broadcast channel. The alternative is
committing them server-side out of band and having the fold set only
`has_token_logprobs` / `has_raw_meta`, which keeps the bus light and is closer to
how storage v2 already thinks about heavy fields.

### Also: the docs undersell this

`SKILL.md` and the API contract both say a CLI fan-out persists one
representative, which is true and is the thing people quote. Neither says the
representative is content-only. An agent reading either doc will promise a human
more than the tree will hold — as one did, in the session that found this.

*(opus-5, 2026-08-12, weird-personas fig-1 smoking workspace session)*

---

> **2026-08-12, fable (the server-authority design session):** everything
> measured above is real, and the "shape of the fix" sketched here is the
> interim graft that `docs/HANDOFF_SERVER_AUTHORITY.md` §6 explicitly
> supersedes — server-authored folds (its P2) persist reasoning + blobs +
> all n samples structurally instead of widening the echo. Don't build the
> graft (docs/TODO.md carries the same warning). This file's new findings —
> the reasoning drop, the blank thinking rows, the SKILL/API_CONTRACT
> undersell — are folded into the handoff as §2c.5 (P2/P3 verify items).
> Keep this file as the measured evidence + the docs-fix checklist.
