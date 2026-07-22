# HANDOFF — server-authoritative workspace trees (the ops protocol)

STATUS: **design, nothing implemented.** Drafted 2026-07-21 by fable
(claude-fable-5), same session that diagnosed the CLI "no token data" bug (§6).
Grounding claims verified against the working tree at commit `1f0ae3e` — each
carries a `file:line`. A fable teammate's review takes are to be incorporated
before implementation starts.

Supersedes-when-built: `BRANCHING_DESIGN.md` §0 invariants 2/3/3b, §3
(own-vs-external folding), and §6's save machinery describe the browser-authored
world; they get rewritten per phase as this lands (don't pre-edit them).

## 0. TL;DR

Today the **browser is the only writer** of conversation trees: the server
stores bytes but never authors them, chats fired by `tinkpg` are persisted only
if a browser tab happens to be open to fold them, and that fold reads a
deliberately-stripped text echo — which is why CLI turns lose `token_logprobs`,
lose their n−1 extra samples, and evaporate entirely on a server restart.

This handoff inverts authority: **the server owns every workspace tree; all
mutation travels as small idempotent ops; clients keep a local mirror they
mutate optimistically and converge via op events + a per-workspace `rev`.**
Chat completions are folded (and blob-persisted) by the server itself, so a
headless `tinkpg send` is durable with zero browsers attached. The browser
keeps `tree.ts` and all its interaction code — what changes is that persistence
stops being tree snapshots and becomes the ops themselves.

Clément's directives (2026-07-21, paraphrased from chat): full server-side
authority is the right call and **refactoring cost is explicitly not a
constraint**; his intuition that an op-shaped save ("here are the new things
and who they inherit") dissolves the save race is correct (§5); send-branch-to-
panel should go through the server rather than shipping trees (§4.1
`copy_subtree`); the one worry to engineer against is the browser *feeling*
clumsier (§4.2 — the mirror stays optimistic, zero added latency); the CLI
gains workspace targeting (§4.4); the row "Copy node id" button should emit a
workspace-qualified handle so agents can sample from any stored node (§4.4).

## 1. Why now

The single-writer-browser design is a historical artifact — tinkerscope began
as a browser playground and the CLI slotted into a read-only corner. Usage has
inverted: probe batteries, agent-driven sessions, `tinkpg send/continue` as a
primary interface. Under the current architecture all of these silently depend
on a browser tab being open, and even then persist only a text shadow of what
was generated. Fixing the symptom (grafting logprobs from the render bucket at
fold time) would be throwaway work; the disease is where authorship lives.

## 2. Current state (verified 2026-07-21 against `1f0ae3e`)

Authorship & persistence:

- The server-side store (`api/conversation_store.py`) is a dumb file store:
  light per-workspace JSON + write-once per-node blobs
  (`conversation_store.py:20` — blobs never rewritten, deleted only with the
  whole conversation). Located per instance under `STATE_HOME/<instance>/`
  (`paths.py:11`, `settings.py:37-41`).
- **Nothing server-side ever writes a conversation on its own initiative.**
  `routes/chat.py` streams samples onto the bus and updates the in-memory
  panel echo; `api/state.py` has zero disk writes (snapshots only fan out over
  SSE). The only conversation-writing client is the browser
  (`conversations.svelte.ts` + `save-plan.ts`: dirty-panel partial-upsert PUT /
  layout-only PATCH). `tinkpg` only reads (`cli.py` — no POST/PUT/PATCH of
  `/api/conversations` except the read-only `node-blobs` fetch, `cli.py:2024`).
- Browser saves ship **whole per-panel trees** (`save-plan.ts:44` `planSave`).

Folding (who turns a finished chat into tree nodes):

- Browser-own chats: detached fire (`chat.svelte.ts:2-10`), fold **all n**
  samples from the bus bucket on `chat_done` (`tryFoldOwnDone`,
  `chat.svelte.ts:129`), heavy fields seeded into the blob cache
  (`chat.svelte.ts:149`).
- CLI / foreign chats: `#onExternalDone` (`conversations.svelte.ts:817`)
  reconciles from the panel transcript **echo** — which
  `state.py:115` (`_HEAVY_MSG_FIELDS`) strips of `token_logprobs`/`raw_meta`
  on ingest, and which carries only the **representative** sample
  (`chat.py:216` `_committed_turn`). Hence: no token data, n−1 samples lost,
  and nothing at all if the server restarts before a browser folds
  (the echo lives only in process memory).
- Ownership/scoping is a stack of heuristics this design retires: client
  tokens (`chat.py:165`), the `conversation_id` stamp captured at chat start
  (`chat.py:560-567`), the deliberate null-stamp fold-anyway hole and the
  `#afterLoad`-is-ungated asymmetry (`BRANCHING_DESIGN.md` §0.3b), content-
  matching reconcile on reconnect (`conversations.svelte.ts:865`).

Tree mechanics that carry over unchanged:

- `tree.ts` node/tree shape and immutable ops (BRANCHING_DESIGN §1–2). Node
  content is **immutable after creation** — edits/regens mint sibling nodes —
  which is precisely what makes blobs write-once and (below) ops idempotent.
- Ids: `nid()` = `'n' + SESSION + counter` with a 4-char random base36
  per-page-load SESSION (`tree.ts:130-137`). Two clients minting concurrently
  collide with ~1/1.7M odds per pair — Clément's "unlikely they'd conflict" is
  right. The real id problem today is *divergence*, not collision: for a CLI
  chat, the store-side fold doesn't exist and the browser mints echo-derived
  ids, so nothing guarantees two tabs converge on the same id for the same
  turn. Server-minted fold ids fix convergence by construction.
- Thread identity at ROOT = (trimmed content, thread system prompt) pair
  (BRANCHING_DESIGN §2b); a fresh root minted by a fold must stamp
  `system_prompt`.
- Every UI tree mutation is busy-gated per panel
  (`branch-ops.svelte.ts:101,111,141,…`), so delete-during-generation is
  unreachable from one client today; only cross-client interleavings exist.
- `tinkpg continue --thread/--turn/--node` already reads the **saved**
  workspace tree, with `--conv` selecting which one (`cli.py:1298`,
  `_continue_target` at `cli.py:1228`); `--node` resolves an id prefix within
  one panel's tree. The Copy-node-id button ships the bare id
  (`ChatMessage.svelte:492`).

## 3. Locked decisions

1. **Server authority over ALL tree mutation** — not just chat folds. One
   author, one code path, no dual-authorship merge rules. (Clément: "putting
   everything on the server side … the right thing"; refactor cost accepted.)
2. **Op protocol, not tree snapshots.** Clients submit small ops; the server
   applies them under the existing flock, bumps a per-workspace `rev`, and
   broadcasts them. The op vocabulary mirrors `tree.ts` and is naturally
   idempotent because node content is immutable (§4.1).
3. **The browser stays optimistic.** `tree.ts` + the `$state.raw` mirror +
   bucket rendering are untouched; local mutations apply synchronously exactly
   as today, and the op POST is fire-and-forget. Divergence is handled by
   rev-gap → refetch, not by blocking the UI. This is the answer to the
   "browser feels clumsy" worry: added latency is zero by construction.
4. **Chat requests carry their placement**; the server folds. `/api/chat`
   gains `workspace` + an inline user turn + `parent_node`; at terminal the
   server folds **all n** samples + writes blobs. The echo/stamp/reconcile
   heuristics retire.
5. **Storage layout unchanged** (v2 files + blobs). Additive `rev` field. No
   data migration; legacy `{tree, compare_tree}` bodies are normalized
   server-side on first op (replacing today's browser-side FULL-map first-save
   rule).
6. **`tree.test.ts` cases become shared fixtures** for the Python tree model —
   the two implementations (server authoritative, browser mirror) must agree
   op-for-op, and the existing test vectors are the cheapest contract.

## 4. Design

### 4.1 Op vocabulary

`POST /api/conversations/{id}/ops` with
`{client: <token>, batch: <uuid>, base_rev: <int>, ops: [...]}` →
`{rev, results: [...]}`; applied atomically under `locked("conversations")`,
`rev` bumped once per accepted batch, then broadcast as one bus event
(`ops {workspace, rev, batch, client, ops}` — light node bodies only, never
heavy fields).

| Op | Payload | Semantics / conflict behavior |
|---|---|---|
| `add_nodes` | `{panel, nodes: [{id, role, content, reasoning?, raw_text?, system_prompt?, parent}], select}` | Append a node or chain (edit-fork-copy mints a chain). Ids client-minted (`nid()` format) or server-minted (folds). **Idempotent by id**: an existing id is a no-op (server asserts content equality — a mismatch is a client bug, rejected loudly). `parent` missing/deleted → op rejected (see §5). `select` = select the (last) added node under its parent. Root adds stamp `system_prompt` (thread identity). |
| `select` | `{panel, parent_key, child_id}` | `selected[parent] = child` — last-writer-wins; unknown child → no-op (the mirror's clamp-to-last-child rendering makes this harmless). Covers cycle/select/thread-switch. |
| `delete` | `{panel, node_id}` | Prune node + subtree (+ `selected` cleanup, same semantics as `deleteSubtree`). Idempotent: missing id → no-op. Rejected if the subtree contains the parent of an in-flight chat (§5). |
| `copy_subtree` | `{from_panel, node_id, to_panel}` | Server-side deep copy **keeping ids** (today's `duplicateTo`/send-branch semantics — preserves cross-panel blob sharing, `conversation_store.py:26-29`). Replaces shipping the mutated destination tree. Idempotent (same ids). |
| `set_meta` | any of `{name, system_prompt, system_enabled, panels, reduced_panels, send_targets, seen_panels}` | Field-wise last-writer-wins. The existing PATCH endpoint stays as sugar for this op (same locked apply, same rev bump, same broadcast) so old clients keep working. |

Deliberately **not** ops: in-place content edit (doesn't exist today; keeping
content immutable is what makes the whole table idempotent), blob writes
(server-internal, at fold time only).

Retry safety: ops are naturally idempotent per the table, so a retried batch
re-applies harmlessly; `batch` exists so a client can recognize its own ops on
the bus (self-skip), not for server-side dedup.

### 4.2 Rev + the mirror protocol

- `rev`: per-workspace monotonic int, persisted in the workspace file, bumped
  per applied batch (ops, PATCH, chat folds — every mutation channel).
- Mirror rule (browser store): apply own mutations optimistically as today;
  POST the op batch; on the bus `ops` event, **skip own batches** (by `batch`
  id — replaces `ownTokens` for tree changes), apply foreign ones through the
  same `tree.ts` functions; track `rev` and on any gap (`event.rev !==
  local + 1`) or op rejection, refetch the light body and reset the mirror.
  Refetch is the only non-incremental path and is rare, cheap (light tree),
  and already the shape of today's conversation-open.
- Open sequence: GET body (returns `rev = R`) → subsequent `ops` events apply
  when contiguous from R; events with `rev ≤ R` are no-ops. The SSE
  subscription is global-once as today; events for a workspace ≠ the open one
  are ignored by the mirror (optionally surfaced as a toast — P3 nicety).
- Reconnect: compare revs, refetch if behind. This **replaces**
  `reconcileOnReconnect`'s content-matching heuristics wholesale.

### 4.3 Chat lifecycle (server-authored folds)

`ChatRequest` gains: `workspace` (id; resolution in §4.4), `user_node`
(`{id?, content}` — the new user turn, id optional: the browser supplies its
optimistically-minted id so its mirror needs no swap; the CLI omits it),
`parent_node` (id under which the turn hangs; absent = the panel's active
leaf, today's semantics). Regen = request with `parent_node` = the existing
user node and no `user_node`.

- **At begin**: the server applies `add_nodes` for the user turn (rev++,
  broadcast `ops`, then `chat_start`). The user turn is durable the moment
  the chat starts — a crash mid-generation no longer loses the question.
- **During**: `delta`/`sample` bus events unchanged; the bucket stays the
  render path for streaming.
- **At terminal**: the server folds **all non-error samples** as assistant
  siblings under the user node — same ordering/selection semantics as
  `foldAssistant` (sample-index order, select the first) — writes light nodes
  + `token_logprobs`/`raw_meta` blobs in one locked write, rev++, broadcasts
  `ops` **then** `chat_done` (ordering guarantee: fold data is present before
  busy releases). Cancel/error with ≥1 completed sample folds what completed
  (today's "partial data is real" rule, `chat.py:585`); 0 samples folds
  nothing.
- Browser adoption: `tryFoldOwnDone` and `#onExternalDone` both collapse into
  "apply the `ops` event" (own batches included — the fold is server-minted,
  so no self-skip for fold ops; the mirror just applies them). The bucket
  keeps rendering until the ops land, exactly like today's fold timing.
  `nodeBlobs` seeds from the bucket when present (own + watched-live chats)
  and lazy-fetches otherwise (unchanged).
- The prefill/thinking merge logic already lives server-side
  (`_committed_turn`, `chat.py:216`) — the fold generalizes it from
  "representative only, text only" to "all n, with blobs", it does not need
  porting from the browser.

### 4.4 Workspace addressing (headless CLI)

- Resolution order for a chat's home workspace: explicit `workspace` on the
  request → else the open conversation (`BUS.state.conversation_id`) → else
  **auto-create** a workspace (name derived from the first user message,
  id printed prominently by the CLI). Every chat has a home; the null-stamp
  hole and the "pure lockstep, nothing persisted" mode retire. Escape hatch
  if we ever miss it: a `--no-persist` flag can bring ephemeral mode back —
  not built now.
- Firing into a workspace that is NOT the browser's open one touches nothing
  in the browser's view (its mirror ignores foreign-workspace ops); the
  browser's open workspace is a *view* concept, no longer a persistence
  dependency.
- CLI surface: `--workspace <id-prefix|name>` on `send`/`continue`/`battery`
  (same resolver as today's `--conv`, which becomes an alias), plus
  `--new-workspace [name]`. `battery` wants one workspace per run by default.
- **Qualified node handles** (Clément's ask): `--node` accepts
  `[<conv-prefix>:]<node-prefix>`; bare node ids keep today's meaning (open
  workspace). The row copy button emits the qualified form
  (`8f3a2c:nq1x4b`) and its tooltip shows ready-to-paste `tinkpg samples
  --node …` / `tinkpg continue --node …` commands. Node-id ambiguity across
  panels (shared-id copies) resolves as today: error listing candidates,
  `--panel` disambiguates.

### 4.5 What retires / what stays

Retires (per deprecation protocol — moved, not deleted, where it's code):

- `save-plan.ts` + its dirt accumulation in `conversations.svelte.ts`
  (+ tests → `deprecated/`), the PUT `/tree` save path (endpoint kept one
  transition window for stale tabs, then removed — this box has one user;
  same old-tab trap `HANDOFF_WORKSPACE_RENAME.md` documents),
  post-save heavy-field lightening (mirror nodes are light from birth; heavy
  data only ever lives in the bucket + `nodeBlobs` + server blobs).
- `#onExternalDone`, `#afterLoad`'s reconcile loop, `reconcileOnReconnect`
  content matching, the `conversation_id` chat stamp + null-stamp hole,
  `ownTokens`-as-fold-gate (a busy-latch role may remain).
- The panel `messages` echo + `_HEAVY_MSG_FIELDS` strip + `_light_msgs`
  (`state.py`) — after P3, `PanelState` holds selection + thread-system
  mirror only, and `tinkpg state/show` reads workspaces (they mostly already
  do). `reconcileExternal` itself survives only as the legacy-import path
  (`--ancestry-file`-style grafts), if at all.

Stays untouched: `tree.ts` ops + tests (now doubly load-bearing as the mirror
+ the fixture source for the Python port), bucket/streaming render, the whole
render layer (`panelView`, ChatMessage, charts, threads), `nodeBlobs`, storage
files, highlights/prefs stores, discovery/sampling.

### 4.6 Server tree model

New `api/tree_ops.py`: the `ConvTree` shape + `add_nodes`/`select`/`delete`/
`copy_subtree` in Python, property-tested against fixture vectors exported
from `tree.test.ts` (one JSON file of `{op, tree_before, tree_after}` cases
generated by a small node script — the contract that keeps mirror and
authority in lockstep). Applies through `conversation_store` under the
existing lock; bodies memoized as today (`_bodies` eviction on write already
exists).

## 5. The save race, analyzed (Clément's intuition, confirmed)

Today's race: browser PUTs whole per-panel trees; any server-side fold
concurrent with a prepared save gets clobbered. This is unfixable politely
under snapshot saves — it's why the current design has exactly one writer.

Under ops the clobber class **does not exist**: nobody ever sends a whole
tree, so nobody can un-send someone else's node. What remains is enumerable:

| Interleaving | Outcome |
|---|---|
| add vs add, same id | Idempotent no-op (content asserted equal). Distinct clients can't mint the same id in practice (§2 ids). |
| add vs add, same parent | Both append — siblings, exactly the semantic today's forks produce. Order = arrival order; harmless. |
| select vs select | Last-writer-wins; render clamps make any transient stale selection benign. |
| add under a node being deleted | `add_nodes` with a pruned parent → rejected → client refetches. Structural, loud, no silent loss. |
| **delete vs in-flight chat** (the one real case) | The server tracks in-flight chats per (workspace, panel, parent); a `delete` whose subtree contains an in-flight parent is **rejected** with a reason. UI already busy-gates this locally (`branch-ops.svelte.ts:101`); the rejection covers the cross-client window. Chosen over fold-into-orphan (grafts confusion) and over drop-the-fold (silent data loss — the worst option). |
| set_meta vs set_meta | Field-wise LWW — same as today's PATCH. |
| copy_subtree vs delete of source | Copy reads under the lock: either it copied (dest keeps the copy — fine, copies are independent) or the source was gone (rejected, refetch). |

Rejections and rev gaps share one recovery: refetch the light body. Expected
frequency: ~never in single-user practice; the point is that the failure mode
is a visible refresh, not lost data.

## 6. What this fixes for free

- **The "no token data" bug** (diagnosed this session): CLI-fired turns fold
  server-side from the actual samples, so blobs exist and `has_token_logprobs`
  is real. The interim graft-from-bucket fix is superseded — do not build it.
- **The n−1 lost samples**: CLI `send -n 8` persists 8 assistant siblings
  (chart-on-stored-turns gets full distributions), not 1.
- **Restart durability**: user turn persisted at chat begin, samples at
  terminal — no unfolded echo to lose.
- **Headless persistence**: zero browsers required, the original goal.
- **Cross-tab convergence**: ids are minted once (client for its own ops,
  server for folds) and adopted everywhere — the last-writer-wins multi-tab
  limitation in BRANCHING_DESIGN §6 narrows to genuine simultaneous edits of
  the same field.
- **A large deletion of heuristic code**: origin stamps, null-stamp
  rationale, reconcile-on-reconnect, save dirt — replaced by `rev` compare.

## 7. Staging

Three phases, each shippable + verified before the next. All browser smokes
run against `scripts/dev-isolated.sh`, never :8767.

**P1 — op layer + browser cutover of local mutations.**
Server: `tree_ops.py` + fixture-vector tests; `/ops` endpoint + rev + bus
`ops` event; PATCH rerouted through set_meta; legacy-body normalize-on-first-
op. Browser: mirror protocol (apply-optimistic → POST batch → self-skip →
rev-gap refetch) for edit/regen-mint/delete/cycle/select/thread-switch/
send-branch (`copy_subtree`)/panel add-drop; save-plan + PUT retire to
`deprecated/`. Chats still fold browser-side in P1 (own-bucket + echo paths
untouched) — the op layer must not wait on the chat rewrite.
Verify: fixture vectors green both sides; pytest; `npm test`/`check`; smokes
`browser_system_chip`, `browser_thread_switcher`, `browser_row_toolbar`, plus
a new `browser_ops_convergence.py` (two pages, one workspace: edits in A
appear in B; kill-server-mid-op recovery).

**P2 — server-authored chat folds.**
`ChatRequest` gains `user_node`/`parent_node`/`workspace` (resolution §4.4
minus CLI flags); begin-fold + terminal-fold + blob writes; `ops`-then-
`chat_done` ordering; in-flight delete rejection; browser fold paths replaced
by ops adoption; echo becomes emit-only legacy.
Verify: pytest (fold semantics: n>1, thinking-both, prefill merge, cancel-
partial, error-empty); rerun the token-logprob smokes (`browser_token_
logprobs.py` seeded + `_live.py` real sampling); NEW small-smoke: `tinkpg
send -n 3` against dev-isolated with **no browser attached**, then assert the
workspace file + blobs on disk; restart-mid-generation smoke.

**P3 — addressing + retirement.**
CLI `--workspace`/`--new-workspace`/auto-create + qualified `--node`; Copy
button emits qualified handle; `tinkpg state/show` off the echo; strip echo
emission + `_HEAVY_MSG_FIELDS` + PUT endpoint; BRANCHING_DESIGN/STORAGE_V2/
API_CONTRACT rewritten to as-built; README + skill (CLI changes ship with
docs, same commit).
Verify: full pytest + smoke sweep; a battery run into a fresh workspace
end-to-end.

## 8. Cost

P1 is the bulk (Python tree model + mirror cutover + smoke repair): 1–1.5
focused days. P2: 0.5–1 day (fold logic mostly generalizes `_committed_turn`;
the tests are the work). P3: ~0.5 day. Total **2–3 agent-days including
review**, the usual uncertainty being smoke flakiness and the transition
edges (§4.5's old-tab window), not the core logic.

## 9. Open questions for Clément

1. **Headless default**: auto-create a workspace for a `tinkpg send` with no
   open conversation and no `--workspace` (proposed — never silently
   unpersisted), or hard-error asking for `--workspace`?
2. **All-n folding for CLI fires**: CLI turns will now show ‹k/N› sibling
   cyclers in the browser like browser fires do. Intended and wanted, yes?
3. **Qualified handle format**: `convprefix:nodeid` (proposed) vs
   `convprefix.nodeid` — the tooltip/CLI parse just needs one canonical
   separator (`:` avoids ambiguity with dotted names).
4. Any attachment to the ephemeral no-persist lockstep mode, or is
   "everything lands in a workspace" strictly better? (Design assumes the
   latter.)
