# Shared tree-op fixture vectors

The cross-implementation contract between the two tree engines: `src/tinkerscope/api/tree_ops.py`
(authoritative) and `web/src/lib/tree.ts` + the browser mirror's op application. Each file is one
op applied to one tree; **both sides assert the same files**, so a semantic drift fails on
whichever side moved instead of surfacing later as a workspace that renders differently in two
tabs. See `docs/HANDOFF_SERVER_AUTHORITY.md` decision #6 / §4.6.

Hand-authored from `tree.test.ts` behavior and the §4.1 op table — one vector per op-table row
plus the idempotent-replay and reject cases. They are NOT recorded from a run of either engine:
a vector recorded from the implementation it is meant to police only pins what that engine does
today.

## File shape

Two forms. **Which one a file uses is told by which key pair is present** — a runner switches on
`tree_before`:

**Single-panel** (the default; the op's `panel` names the tree):

```jsonc
{
  "name": "human-readable, shown on failure",
  "op":   { "op": "add_nodes", "panel": "p", "nodes": [ … ], "select": true },
  "tree_before": { "nodes": {…}, "rootChildren": [], "selected": {} },
  "tree_after":  { "nodes": {…}, "rootChildren": [], "selected": {} }
}
```

**Trees-map** — for the two ops that act on the panel MAP rather than inside one tree
(`copy_tree`, and `replace_tree` with `tree: null`, which removes a panel):

```jsonc
{
  "name": "…",
  "op": { "op": "copy_tree", "from_panel": "p", "to_panel": "q" },
  "trees_before": { "p": {…} },
  "trees_after":  { "p": {…}, "q": {…} }
}
```

**Batch** — `ops` (a list) instead of `op`, with either tree form. Needed for anything whose
behavior depends on op BOUNDARIES; a single-op vector structurally cannot see a batch-scope
bug, which is exactly how the selection-claim asymmetry got in (server claimed batch-wide, a
mirror replaying op-by-op claimed per-op, and the two picked different siblings forever):

```jsonc
{
  "name": "…",
  "ops": [ {"op": "add_nodes", …}, {"op": "add_nodes", …} ],
  "trees_before": { "p": {…} },
  "trees_after":  { "p": {…} }
}
```

**Reject vectors** carry `"rejects": "<substring of the error>"` and NO `tree_after` /
`trees_after`. A rejection must discard the whole batch, so the correct assertion is that the
tree is untouched AND the error mentions the substring.

## `broadcast` — the one recorded field, and what a runner does with it

Every non-reject vector carries `"broadcast": [...]`: the ops as the server actually fans them
out (light nodes, merged `set_meta` values, `add_nodes` nodes MINUS `children`). It is the only
field that is machine-recorded — `uv run scripts/record_tree_vectors.py` writes it, `--check`
reports staleness — and that is safe precisely because it has its own oracle:

> **Replaying the recorded `broadcast` ONE OP AT A TIME must reproduce the hand-authored
> `tree_after`.** Both engines assert exactly this, and it is what a production mirror does.

So a wrong broadcast cannot pass by having been recorded — the replay diverges from the
hand-authored tree. Everything else in a vector stays hand-authored for the usual reason: a
`tree_after` recorded from the engine it polices only pins what that engine does today.

Two properties fall out, and both earned their place:

- **Batch ≡ per-op replay.** Applying a vector's ops as a batch must equal applying its
  broadcast one op at a time, because that is how a mirror consumes events. Anything scoped to
  the BATCH rather than the OP breaks it silently — contiguous revs, no gap, no repair. That is
  how the selection-claim asymmetry got in.
- **The broadcast must be replayable at all.** Vectors exercise the INPUT op shape; mirrors
  consume the BROADCAST shape; nothing was testing the second until this assertion existed. It
  immediately caught `add_nodes` shipping stored nodes WITH their `children`, which our own
  input validation rejects.

A diff from `record_tree_vectors.py` you did not intend is the signal, not a nuisance: the wire
contract moved.

## Rules for adding one

- Ops are the full WIRE shape (`panel` included), so a vector can be fed straight to either
  engine's op entry point.
- Node ids are hand-picked and deterministic (`u1`, `a1`, …). Neither engine mints ids while
  applying an op, so a vector never depends on an id counter.
- Pure JSON — no comments, no Python/JS-isms. Key order is not significant on either side.
- `set_meta` has no vectors: it touches workspace metadata, not a tree. It is covered by
  `tests/test_tree_ops.py` (merge rules) and the phantom-heal tests.
