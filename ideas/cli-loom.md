# `tinkpg loom` — drive the loom from the terminal

The loom is browser-only: the CLI has no command for "branch node X at token i
into alternative j / resample from i". An agent reading a turn's logprobs via
`tinkpg node <id>` can SEE the fork-worthy positions but has to hand-build a
`continue_tokens` POST to act on one. The backend surface is already there and
agent-friendly (`/api/chat` + `continue_tokens` + `renderer_name`,
`params_scope: 'call'`); what's missing is the CLI verb + the node→tids lookup
that `branchOps.loomBranch` does browser-side (stored stream + raw_meta model
anchoring — would need a small shared/server-side resolution, since the CLI
shouldn't reimplement the raw_meta parse).

Shape sketch: `tinkpg loom --node <id> --at <tok-idx> [--alt <k>|--resample]
[-n N]` — the browser follows via the bus like any CLI fire. Note the CLI's
`continue` command still uses the TEXT-prefill path (unchanged by the browser's
Continue-rides-the-loom rewire) — a `tinkpg loom` would supersede it for
native turns.

Ships with README table + cli skill in the same commit, per convention.

*(fable, 2026-08-06, the loom-ship session)*
