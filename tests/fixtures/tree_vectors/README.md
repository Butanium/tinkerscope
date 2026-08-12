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

**Reject vectors** carry `"rejects": "<substring of the error>"` and NO `tree_after` /
`trees_after`. A rejection must discard the whole batch, so the correct assertion is that the
tree is untouched AND the error mentions the substring.

## Rules for adding one

- Ops are the full WIRE shape (`panel` included), so a vector can be fed straight to either
  engine's op entry point.
- Node ids are hand-picked and deterministic (`u1`, `a1`, …). Neither engine mints ids while
  applying an op, so a vector never depends on an id counter.
- Pure JSON — no comments, no Python/JS-isms. Key order is not significant on either side.
- `set_meta` has no vectors: it touches workspace metadata, not a tree. It is covered by
  `tests/test_tree_ops.py` (merge rules) and the phantom-heal tests.
