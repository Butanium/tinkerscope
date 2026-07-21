# Branching — design spec v2 (resolved after adversarial critique)

Implementation contract for conversation branching. v2 folds in the fixes from
the 3-lens design critique (2026-06-22). Folds into `HANDOFF_BRANCHING.md` when
the feature lands. Locked decisions: separate per-scan-root tree store (NOT in
the SSE snapshot); `messages`/`compare_messages` are the linear ACTIVE PATH;
edit/regen/n-samples FORK; ‹k/N› cycling; delete prunes subtree; named
conversations via a dropdown.

## 0. The load-bearing invariants (v2)

1. **The tree is the single READ source.** `panelView`, the send context,
   `lastUserQuestion`, and the distribution chart all derive from
   `activeMessages(tree)` — **never** from `s.messages`. `s.messages` /
   `s.compare_messages` are a WRITE-ONLY echo channel: the browser patches them
   (so the CLI/sampler see the active path) and the backend overwrites them
   transiently per chat, but the frontend never reads them for logic/render.
   This neutralizes the backend's `chat_begin`/`chat_done` clobber (chat.py:240-277)
   and the CLI's reset-to-`[user]`.
2. **Own turns fold from the request's OWN response stream**, not the shared
   render bucket. `fireChat` parses its `/api/chat` SSE response, collects the
   `message` samples, and folds them into the tree under the user node it was
   given. So a CLI / second-tab chat landing on the same panel mid-stream (it
   clobbers the single-slot bucket) can NEVER corrupt or drop an own fold.
3. **Chat ownership is by an explicit client token**, not chat_start ordering.
   `fireChat` mints a token, registers it in `ownTokens`, and sends it on the
   `/api/chat` body; the backend echoes it on the `chat_start`/`chat_done`/
   `chat_error` bus broadcasts. The external-fold hook skips any chat whose
   token is in `ownTokens`. Tokens are removed when `fireChat` finishes.
3b. **External folds are conversation-scoped.** Panel ids (`compare`, `p-2`…) are
   re-minted across conversations and `PlaygroundState` is a process-wide singleton
   (shared by every tab + the CLI), so a `chat_done` for one conversation must NOT
   graft onto a freshly-reused panel id of another (the "new panel loads a weird
   random conversation" bug). Each chat broadcast is stamped with `conversation_id`
   = the conversation open **when the chat started** (chat.py snapshots
   `state.conversation_id` right after `chat_begin` — race-free, no await between;
   preferred over a request field because tinkpg has no per-chat conversation of its
   own to send, and the browser's own chats bypass the gate via the ownership token
   anyway — a request field would be a second, racier source of truth for the same
   fact). `#onExternalDone` folds a `chat_done` only when its stamp == `convo.activeId`.

   **The null-stamp hole (deliberate).** The stamp is null when NO conversation was
   open in shared state at chat start — i.e. a pure-CLI/headless session with no
   browser pushing its id, or a legacy browser that never writes `conversation_id`,
   or the split-second on first load before the browser's opening push lands. A null
   stamp is **folded, not dropped** — the conservative choice. Rationale: a graft
   requires a *different, non-null* origin colliding with the open conversation; null
   cannot express that, so folding a null-stamped chat can't reproduce the bug. What
   it CAN do is break a legitimate CLI/legacy live-drive turn the user is waiting to
   see. So folding-on-null trades a bug that null can't cause for a feature it would
   otherwise break — the lesser evil. (The day tinkpg learns to set `conversation_id`,
   tighten this to reject null.)

   **Asymmetry — only `#onExternalDone` is origin-gated, `#afterLoad` is not.** The
   `chat_done` event carries an origin stamp, so `#onExternalDone` can scope by it.
   `#afterLoad` instead reads the panel `messages` echoes in shared state, which
   carry **no** origin — but it's structurally safe without a gate: `#loadTrees`
   clears every echo to `[]` synchronously (no await) before `#afterLoad` runs, so
   its reconcile loop is dormant at load and cannot graft a foreign turn. Gating it
   on the live (non-stamped) `state.conversation_id` would be a near-no-op (the
   browser continuously pushes its own activeId into that single field) and could
   even skip a legitimate same-conversation reconcile during a switch lag, so it's
   deliberately left ungated.
4. **Backend + CLI logic unchanged** except: `chat.py` accepts an optional
   `client_token` and echoes it on the three chat broadcasts (additive); the
   conversations store backs up a corrupt file instead of silently resetting.

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
| cycle / select | `setSelected(tree, nodeId) -> tree` / `cycle(tree, nodeId, delta) -> tree` | set `selected[parentKey] = nodeId` (or step ±1 among siblings, clamped, no wrap); subtree below re-derives. |
| reconcile external | `reconcileExternal(tree, msgs) -> tree` | build a linear chain from `msgs`; if an existing root→leaf path already equals `msgs` (role+content) → no-op (idempotent, kills reload-dupes); else attach the chain's head as a NEW `rootChildren` entry + select it. NEVER prefix-appends. |

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
  (`PanelState.thread_system_prompt`, echoed by `#mirror` next to
  `panel_messages`) exists for the CLI's mid-thread inherit + the
  reconnect/on-load reconcile.

## 3. Folding — own vs external

- **Own** (browser-initiated): `fireChat(panelSel, userParentId, messages, signal)`
  mints `token`, adds to `ownTokens`, POSTs `/api/chat` with `client_token:token`,
  parses the SSE response collecting `message` samples, then
  `store.foldAssistant(panel, userParentId, samples)`; removes `token` from
  `ownTokens` in a `finally`. On abort/error: remove token, do NOT fold a partial
  (the user node keeps no reply; user can regenerate).
- **External** (CLI / other tab): the live store forwards `chat_done`/`chat_error`
  to an `onChatDone(panel, run)` hook carrying `run.client_token`. The hook:
  if `client_token ∈ ownTokens` → **skip** (own path handles it). Else →
  `reconcileExternal(panel, live.state.messages)` (reliable `[user, assistant0]`
  for the CLI; extra n>1 siblings folded from the bucket samples best-effort).
  `chat_error` only clears any state; never folds a partial.

Ordering note: chat.py broadcasts `chat_done` on the bus BEFORE yielding `done`
to the caller stream, so the hook sees the token still in `ownTokens` (fireChat
hasn't reached its `finally`) → correctly skips. No race.

The bucket (`live.panels`) is **render-only**; never the source of an own fold.

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

## 6. Conversations store + persistence

**Frontend** `web/src/lib/conversations.svelte.ts` — `list`, `activeId`,
reactive `tree`/`compareTree`, plus `ownTokens` + `foldAssistant`/external-fold
glue. Methods: `load()`, `switchTo(id)`, `create(name?)`, `rename(id,name)`,
`remove(id)`, debounced `save()`.

- ~~`save()` captures `(activeId, structuredClone({tree, compareTree, system_prompt}))`
  at SCHEDULE time and PUTs that~~ — SUPERSEDED by storage v2 (see
  `docs/STORAGE_V2.md`): trees are `$state.raw` immutable refs, so the capture is
  the REF at mark time (zero copy); saves accumulate dirty-panel/dropped/layout
  dirt and ship a partial-upsert PUT or a layout-only PATCH (`lib/save-plan.ts`).
  Flush-on-switch semantics below are unchanged.
- `switchTo`/`create`/`remove` **flush** any pending save first, **clear both
  buckets**, set the new trees, restore `system_prompt`, then run the load steps.
- All of `switchTo`/`create`/`remove`/cycle/edit/delete/regenerate are gated on
  `!anyRunning` (mirror the existing toolbar guards).
- **`newConversation`/`enableCompare`/`disableCompare` reset the TREE(s)** (to
  empty ConvTree), not just `messages` — since render reads the tree.
- Last-conversation delete = **reset in place** (same id, empty tree, default
  name); never a window with zero conversations.

**load()/switchTo() sequence** (after `live.state` is guaranteed non-null —
sequence it after the onMount `getState()` fallback):
1. set `tree`/`compareTree` from the conversation (or empty).
2. IF `live.state.messages` non-empty AND NOT `state.running` AND it diverges
   from `activeMessages(tree)` → `reconcileExternal` (fold a stray CLI turn once).
   Same for `compare_messages`.
3. **UNCONDITIONALLY** patch `messages`/`compare_messages` = the active paths
   (so a fresh backend after restart still learns the loaded conversation).

**Conversation type** (`types.ts`):
`{ id, name, system_prompt: string|null, tree: ConvTree, compare_tree: ConvTree|null, created_at, updated_at }`.
`system_prompt` travels with the conversation (each conv = one experiment);
restored into `state.system_prompt` on switch. (mode + model selection stay
GLOBAL in v1 — documented limitation; a conversation continues with whatever
models are currently selected.)

**Backend** `routes/conversations.py` (built; see §store): LIST/CREATE/PATCH-rename/
PUT-tree/DELETE, flock-wrapped. ADD: on `json.JSONDecodeError`, rename the file
to `conversations.json.corrupt-<ts>` BEFORE returning `[]` (don't let the next
save cement total loss). `settings.py`: `conversations_path` (done). `main.py`:
router registered (done). `api.ts`: 5 `j<T>` methods. `chat.py`: optional
`client_token` echoed on chat_start/chat_done/chat_error (additive).

**Known limitations (documented, not fixed in v1):** two browser tabs editing the
SAME conversation simultaneously = last-writer-wins (flock prevents file
corruption + sibling-entry clobber, not same-id logical merge). Per-conversation
mode/model-selection not restored. CLI external turns lack `reasoning` (backend
commits only content to `messages`).

## 7. Verification
- `tree.ts`: `node web/src/lib/tree.test.ts` (Node 22 strip-types; no dep). Cover
  every op, `activePath` default/clamp, selection-by-id survives delete-of-earlier-
  sibling, shift-copy chain + selected entries, reconcile idempotency, the
  selected-key invariant.
- backend: pytest (done) + a corrupt-file-backup test.
- browser smoke `tests/small-smokes/browser_branching.py`: seed via `/api/state`;
  shift-edit a user msg (fork+copy, 0 tokens) → assert `[data-testid=branch-cycle]`
  shows `2`; cycle → text toggles + path re-derives; delete fork → count drops,
  selection falls back; open editor then cycle to identical-content sibling →
  draft cleared (edit-leak); no console/pageerror; oracle = `GET /api/conversations`
  + `GET /api/state`. One real n=3 call exercises assistant-sibling cycling (flagged).

## 8. Edge cases to defend (tests)
delete earlier sibling → active branch unchanged (selection-by-id); delete active
leaf → path ends at parent; delete the user node whose reply is mid-fold → fold
finds no parent → no-op (guard `foldAssistant` on missing parent); cycle to a
never-continued sibling → path ends at it; regen under a user with downstream →
new sibling selected, old subtree preserved; shift-copy long path → full dup, fresh
ids, selected chain, original untouched; external CLI turn mid-deep-conv → new
root, prior tree intact, root ‹k/N› recovers (with a toast/hint that the terminal
branched); on-load stray CLI turn → folded once (skip if running); compare: two
independent trees, send appends to each; switch conversations → buckets cleared,
no stale overlay; switch/delete while a debounced save pends → flushed under the
right id; corrupt store → backed up, banner, not wiped; abort mid-stream → token
cleared, no partial fold; empty-string edit → treated as no-op (no empty fork).
