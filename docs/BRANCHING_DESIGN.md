# Branching — the tree model + as-built persistence (ops world)

> Rewritten to as-built truth at the server-authority migration's P3
> (2026-08-12, `HANDOFF_SERVER_AUTHORITY.md`). §1/§2/§2b/§4/§5 (the TREE MODEL
> and interaction semantics) are the original v2 design and still exact; §0,
> §3 and §6 describe the OPS-WORLD persistence that replaced the
> browser-sole-writer design. Wire shapes: `docs/API_CONTRACT.md`; the two op
> interpreters: `api/tree_ops.py` + `web/src/lib/tree.ts` (kept in lockstep by
> `tests/fixtures/tree_vectors/`).

Implementation contract for workspace branching. v2 folds in the fixes from
the 3-lens design critique (2026-06-22). Folds into `HANDOFF_BRANCHING.md` when
the feature lands. Locked decisions: separate per-scan-root tree store (NOT in
the SSE snapshot); `messages`/`compare_messages` are the linear ACTIVE PATH;
edit/regen/n-samples FORK; ‹k/N› cycling; delete prunes subtree; named
workspaces via a dropdown.

## 0. The load-bearing invariants (ops world, as built)

1. **The tree is the single READ source.** `panelView`, the send context and the
   distribution chart all derive from `activeMessages(tree)`. The old per-panel
   bus echo (`PanelState.messages`) is GONE (P3): the CLI reads trees over
   `/api/workspaces`, and the bus carries selection + thread-system mirror only.
2. **The SERVER owns every tree; all mutation is idempotent ops.** Clients apply
   optimistically, POST the batch (`/api/workspaces/{id}/ops`), and ALWAYS-apply
   the bus `ops` echoes in `rev` order — their own included (skip-own provably
   breaks LWW convergence). Any rev mismatch (gap, or BACKWARDS after a pack
   replace) → refetch the light body. The op vocabulary + confluence guard:
   `API_CONTRACT.md`; the §4.1 table's browser call-site map: ENGINEERING_LOGS
   2026-08-12.
3. **Chats carry their placement; the server folds.** Every fire sends
   `parent_node` (the user turn was already persisted as the writer's own
   `add_nodes` op), and at terminal the server folds ALL n samples + writes
   blobs in one locked op — broadcast BEFORE `chat_done`, so fold data is
   present before any busy-surface lifts. `client_token` still marks a
   browser's own chats, but only for blob-cache seeding (the terminal's
   `folded` manifest) and busy-latching — ownership no longer gates any fold.
   Delete-vs-in-flight is a server-side rejection (the placement registry).
4. **A workspace is deleted loudly** (`workspace_deleted` broadcast → open tabs
   freeze + notice) and every write channel bumps `rev` at the store's
   `_persist` choke point — pack installs included.

## 1. Data model (`web/src/lib/tree.ts`, PURE — no svelte/browser imports)

```ts
export const ROOT = '__root__';                 // virtual-root sentinel
export type NodeRole = 'user' | 'assistant' | 'system';

export type TreeNode = {
  id: string;
  role: NodeRole;
  content: string;
  reasoning?: string;          // PERSISTED — populated by foldAssistant from samples
  raw_text?: string;           // PERSISTED — ditto; survives reload
  system_prompt?: string;      // PERSISTED — THREAD system prompt; ROOT user nodes only (see §2b)
  parent: string | null;       // null = child of the virtual root
  children: string[];          // ordered
};

export type ConvTree = {
  nodes: Record<string, TreeNode>;
  rootChildren: string[];
  selected: Record<string, string>;   // (parentId | ROOT) -> selected CHILD ID  (NOT an index)
};
```

**Selection by id, not index** — deleting/reordering a sibling never silently
reselects another node. `activePath` resolves `selected[parentKey]` to a child
id; if unset OR the id is no longer a child, default to the **last** child
(newest). Always clamps to a live child.

```
activePath(tree): TreeNode[]                 // ROOT → leaf, following selected child ids
activeMessages(tree): {role,content}[]       // path filtered to user/assistant → [{role,content}]  (system excluded)
parentKeyOf(tree, id): string                // parent id, or ROOT
siblingsOf(tree, id): string[]               // the children array id lives in (or rootChildren)
siblingInfo(tree, id): {index, count}        // index of id among its siblings, sibling count
```

IDs: `nid()` = `'n' + SESSION + (++counter).toString(36)`, `SESSION` a short
per-load random base36 (browser `Math.random` is fine — the workflow-script ban
doesn't apply here; tests call `__resetIds()`). Two tabs get different SESSIONs →
no id collision when both edit one persisted tree (multi-tab same-conv save is
last-writer-wins; see §6 limitations). Load-time validator asserts no node id ===
ROOT.

## 2. Operations (immutable — each returns a NEW `ConvTree`)

| Op | Signature | Effect |
|---|---|---|
| append user | `appendUserTurn(tree, content) -> {tree, nodeId}` | user node as child of active leaf (or new root if empty); selected. |
| fold assistant | `foldAssistant(tree, parentUserId, samples) -> {tree, ids}` | append one assistant child per sample (in `sample_index` order; ERROR samples skipped), **copying `content/reasoning/raw_text`**; select the FIRST appended; returns the new ids in order (for card→node mapping). |
| regenerate | `regenTarget(tree, nodeId) -> {userParentId, fireMessages}` | `nodeId` = assistant or its user node → returns the user node to fold under + the messages root→userParent inclusive. No mutation. |
| edit user (fork+regen) | `editUserFork(tree, userId, content) -> {tree, newUserId, fireMessages}` | new user sibling, selected, no children; caller fires `fireMessages` then folds under `newUserId`. |
| edit user (shift: fork+copy) | `editUserForkCopy(tree, userId, content) -> {tree, newUserId}` | new user sibling + DEEP-COPY of the downstream active path as a fresh-id single-child chain; writes `selected[newChain]` along the way (each copied node selects its single copied child); copies NO original-keyed selected entries; no fire. |
| edit assistant | `editAssistant(tree, asstId, content) -> {tree, newId}` | new assistant sibling, selected, no children, no fire. |
| delete | `deleteSubtree(tree, nodeId) -> tree` | prune node + descendants; drop from parent's children; delete their `selected` entries; if `selected[parent]` pointed at a pruned id, reselect last surviving (or clear). |
| cycle / select | `setSelected(tree, nodeId) -> tree` / `cycle(tree, nodeId, delta) -> tree` | set `selected[parentKey] = nodeId` (or step ±1 among siblings, WRAPPING at the ends — 1-2-3-1…); subtree below re-derives. |
| reconcile external | `reconcileExternal(tree, msgs, threadSystem?) -> tree` | follow + select the longest existing prefix of `msgs` (by role+content; at ROOT level also by thread system when `threadSystem` is known — see §2b), then append the unmatched tail: EXTENDS the matched branch in place (a continued CLI thread), or a NEW root when nothing matched. Idempotent (same ref) when the path already exists — kills reload-dupes. |

Invariant (assert in tests): every `selected` key is a live node id (or ROOT) and
every value is a live child of that key.

## 2b. Thread system prompts (shipped 2026-07-21)

A thread's ROOT user node may carry `system_prompt` — the thread's own
system-prompt part, composed over the workspace's global one **server-side**
(`effective = "\n".join(p for p in (global, thread) if p)`; see
`docs/API_CONTRACT.md` → ChatRequest.thread_system_prompt). Design invariants:

- **A field of the first message, not a system node** — no new role for the
  cyclers / active-path walkers; the ‹k/N› root cycler swaps (system, content)
  as one unit, and editing either forks a sibling thread through the existing
  `editUserFork(tree, userId, content, systemPrompt?)` (root-only: the param is
  ignored for non-root edits). `appendUserTurn(tree, content, atRoot,
  systemPrompt?)` stamps it at thread birth (⑂ composer).
- **Thread identity is the (trimmed content, system_prompt) PAIR** — the probe
  pattern is many threads sharing a first message under different prompts.
  `threadStarts` keys on the pair (entry gains `system?`), and
  `reconcileExternal(tree, msgs, threadSystem?)` matches ROOT-level candidates
  on it when provenance is known: the bus terminal events + the panel-state
  mirror carry `thread_system_prompt`, so a CLI-fired probe folds under (or
  mints, stamped) the RIGHT root. `threadSystem === undefined` = unknown
  (legacy event) → content-only matching, exactly the old behavior.
- **Every fire re-derives the thread part from the tree**: `chat.fireOne` walks
  `threadSystemAt(tree, userParentId)` and sends it explicitly (`''` = none), so
  regen/continue/edit deep in a probe thread composes the probe's prompt and the
  server never falls back to a stale mirror for a browser chat. The mirror
  (`PanelState.thread_system_prompt`, the one per-panel field `#mirror` still
  ships) exists for the CLI's mid-thread inherit.

## 3. Folding — server-authored, adopted by every mirror

The server folds every `parent_node` chat at terminal (all non-error samples as
assistant siblings, sample-index order, first selected — content byte-identical
to `_committed_turn`'s commit, prefill merge included). The fold IS an
`add_nodes` op: one locked write (light nodes + write-once blobs), `rev`++, one
bus `ops` event that every mirror — the firing tab, other tabs, a reloaded
page — replays like any other batch. Nothing folds browser-side anymore:

- **Own chats** (`client_token` match): the terminal's `folded` manifest
  (`[{sample_index, node_id}]` + `fold_rev`) maps each server-minted node to
  the bucket sample it came from — the browser seeds `nodeBlobs` from it (no
  fetch for this session's own turns) and releases the busy token. `fold_rev`
  is the ordering backstop: local rev behind it ⇒ the ops event was missed ⇒
  refetch. A manifest-less terminal (stale server) falls back to the legacy
  bucket fold.
- **External chats**: nothing to do — the ops event already landed the fold; a
  foreign-WORKSPACE chat completing on a reused panel id only triggers bucket
  render hygiene (`live.dropBucket`).
- **Partials are real data**: cancel/error with ≥1 completed sample folds what
  completed (`chat_error` carries the manifest too); 0 samples folds nothing.

The bucket (`live.panels`) is **render-only** — the streaming overlay until the
ops event lands, and the blob source for the manifest seeding.

## 4. Rendering (`panelView`)

```
path = activePath(treeFor(panel))                       // committed turns
out  = path → ViewMessage{role,content,reasoning,raw_text, nodeId, sib:{index,count}}
bucket = live.panels[panel]
if bucket present (live / just-finished, not yet superseded by next turn):
  if out ends in assistant → pop it, push richer bucketTurn carrying that node's id + sib
  else (path ends in the user node, reply streaming) → push bucketTurn (nodeId=null, isBucket)
if bucket.error → push error row
```
`ViewMessage` gains `nodeId: string | null` and `sib?: {index, count}`. The
`{#each}` is **keyed by `nodeId`** (`(msg.nodeId ?? 'b'+i)`) so a fork/cycle
remounts rather than re-feeding a positional slot; ChatMessage's edit-leak
`$effect` also tracks `msg.nodeId` (covers identical-content siblings).

**n>1 distribution.** Live inline cards = the bucket batch (clicking a card →
`setSelected(thatNodeId)`; +page passes the folded sibling-id array). The
**chart** (`buildChartData`) aggregates the active assistant turn's ALL tree
siblings' content (full distribution across regen batches), falling back to the
live bucket while first streaming — so the chart and ‹k/N› never disagree.

## 5. ChatMessage changes
- ‹k/N› cycle control (`.branch-cycle`, `data-testid="branch-cycle"`) on ANY row
  with `sib.count > 1`; prev/next → `onCycle(±1)`.
- Regenerate on USER rows too.
- Edit forks; capture `shiftKey` on the Edit click → `onEdit(content, copyDownstream)`.
- n>1 cards: replace "Use this" with click-card-selects-branch; highlight the selected.
- `$effect` tracks `msg.nodeId` (edit/raw reset on node change even at identical content).

## 6. Workspaces store + persistence (ops mirror)

**Frontend** `web/src/lib/workspaces.svelte.ts` — `list` (summaries), `activeId`,
`trees` (`$state.raw`, per-panel immutable refs), the authoritative panel
`layout`, per-workspace `rev`, and the mirror protocol:

- **Local mutations apply optimistically and EMIT their ops** (`setTree(panel,
  next, {ops})` → `lib/ops.svelte.ts`: one ordered chain, bounded retry on
  transport/5xx — idempotent replay is safe — desync→refetch on 4xx). A
  `setTree` without ops falls back to a whole-panel `replace_tree`, so no
  call-site can silently skip persistence (undo restores use it on purpose).
- **Bus `ops` events always-apply in rev order, own echoes included**, through
  the same interpreters the server and the fixture vectors use
  (`applyTreeOp`/`applyPanelOp`). Workspace filter BEFORE the rev-gap check;
  equal rev drops; anything else refetches (single-flight with a re-arm latch
  + a seen-rev map so an event raced by the refetch GET is never swallowed).
- **Meta** (layout / panel-UI sets / system) debounces into one `set_meta` op;
  own-echo application is churn-guarded (value-compared Sets, claims-in-flight
  counters) so an echo never reverts a toggle made during the RTT.
- **Drafts**: a new workspace exists only in `list` until its first op batch —
  the emitter materializes it (POST create, retried like any batch) then
  replays the batch idempotently over what the create shipped.
- `switchTo`/`create`/`remove` flush the ops chain first, clear buckets, reset
  `rev` from the fetched body, and never leave zero workspaces (last delete =
  reset in place). A body whose stored tree is present-but-malformed latches
  op emission off loudly (`#loadFailed`) instead of persisting emptiness.

**Backend** `api/workspace_store.py` + `api/tree_ops.py`: the authoritative
apply under the workspaces flock — validation, blob splitting (write-once),
`rev`++ in `_persist` (EVERY write channel), the trash journal diff, and the
`ops` broadcast. `PATCH /{id}` is set_meta sugar. The PUT /tree save path is
GONE (P3) — wholesale writes are `replace_tree` ops, shape-validated.

**Multi-tab semantics:** contended edits CONVERGE (idempotent-structural +
LWW ops under rev-ordered replay — the confluence invariant); the old
last-writer-wins whole-tree clobber class is structurally impossible (nobody
sends whole trees on the hot path). Genuine simultaneous edits of one FIELD
remain LWW. CLI turns persist all n samples with CoT + blobs (P2) — the
"CLI external turns lack reasoning" limitation is dead.

## 7. Verification
- `tree.ts`: `node web/src/lib/tree.test.ts` (Node 22 strip-types; no dep). Cover
  every op, `activePath` default/clamp, selection-by-id survives delete-of-earlier-
  sibling, shift-copy chain + selected entries, reconcile idempotency, the
  selected-key invariant.
- backend: pytest (done) + a corrupt-file-backup test.
- browser smokes (the live set): `browser_ops_convergence.py` (cross-tab
  convergence, contended LWW, retry durability, workspace isolation, restart,
  delete/reborn), `browser_send_adopt.py` (server-authored folds adopted by a
  composer send, exactly-n siblings, blobs), `browser_undo.py`, and the
  token-free sweep via `scripts/smoke.sh`.

## 8. Edge cases to defend (tests)
delete earlier sibling → active branch unchanged (selection-by-id); delete active
leaf → path ends at parent; delete the user node whose reply is mid-fold → fold
finds no parent → no-op (guard `foldAssistant` on missing parent); cycle to a
never-continued sibling → path ends at it; regen under a user with downstream →
new sibling selected, old subtree preserved; shift-copy long path → full dup, fresh
ids, selected chain, original untouched; external CLI turn mid-deep-conv → new
root, prior tree intact, root ‹k/N› recovers (with a toast/hint that the terminal
branched); on-load stray CLI turn → folded once (skip if running); compare: two
independent trees, send appends to each; switch workspaces → buckets cleared,
no stale overlay; switch/delete while a debounced save pends → flushed under the
right id; corrupt store → backed up, banner, not wiped; abort mid-stream → token
cleared, no partial fold; empty-string edit → treated as no-op (no empty fork).
