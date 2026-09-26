// The workspace/branch-tree store — the frontend MIRROR of the server-owned
// per-panel branch trees (HANDOFF_SERVER_AUTHORITY §4.2; storage detail in
// docs/STORAGE_V2.md, tree semantics in docs/BRANCHING_DESIGN.md).
//
// Division of responsibility: THIS store owns the reactive `trees`, the
// summaries `list`/`activeId`, op-based persistence, the chat-ownership token
// set, and the external-fold reconciliation wired off the live bus. The TREE
// OPERATIONS (append/fold/regen/edit/delete/cycle) live in branch-ops/+page,
// which read `treeFor(panel)`, compute a new tree via lib/tree.ts, and commit
// it with `setTree(panel, …, {ops})` — the single entry that mirrors the active
// path into PlaygroundState.messages AND persists.
//
// Persistence = the ops mirror:
//   - local mutations apply optimistically and EMIT their ops (fire-and-forget,
//     ordered, retried — lib/ops.svelte.ts); setTree without ops falls back to
//     a whole-panel replace_tree, so no call-site can silently not persist.
//   - bus `ops` events (every accepted batch, own echoes included) apply in
//     rev order through the same interpreters; any rev gap → refetch the body.
//   - meta (layout / panel UI / system) debounces into one set_meta op.
//
// Storage v2 memory policy (docs/STORAGE_V2.md):
//   - `list` holds SUMMARIES only; a workspace's light body is fetched on
//     open (GET /api/workspaces/{id}) and the previous one's trees + node-blob
//     cache are dropped on switch.
//   - `trees` is $state.raw: every mutation is a wholesale per-panel ref
//     replacement (tree.ts ops are immutable), so deep proxies were pure
//     overhead. Nodes are LIGHT from birth — folds seed the blob cache and ship
//     heavy fields on their add_nodes op, never through a tree serialization.

import { live } from './state.svelte';
import { api } from './api';
import { nodeBlobs } from './node-blobs.svelte';
import { undo } from './undo.svelte';
import { opsEmitter } from './ops.svelte';
import { FIRST_PANEL_ID, highestPanelSeq, legacyLayout, mintPanelId as mintId } from './panel-id';
import {
  emptyTree,
  selectedChildId,
  applyPanelOp,
  ROOT,
  type ConvTree,
  type Msg,
  type TreeOp
} from './tree';
import type {
  Workspace,
  WorkspaceSummary,
  Panel,
  PanelLayout,
  StatePatch,
  WorkspaceOp,
  OpsEvent,
  ConvFields,
  SetMetaFields
} from './types';

function asTree(x: unknown): ConvTree {
  const t = x as ConvTree | undefined | null;
  return t && t.nodes && Array.isArray(t.rootChildren) ? t : emptyTree();
}

/** Distinguish "no tree" from "unreadable tree" (ideas/astree-silent-emptytree):
 *  absent and `{}` (the server's create seed) are normal empties; anything else
 *  that fails the shape check is PRESENT BUT MALFORMED — rendering it as empty
 *  and then persisting would replace the real data with emptiness, so the load
 *  path latches saves off instead. */
function treeUnreadable(x: unknown): boolean {
  if (x == null) return false;
  if (typeof x !== 'object') return true;
  if (!Object.keys(x as object).length) return false;
  const t = x as Partial<ConvTree>;
  return !(t.nodes && Array.isArray(t.rootChildren));
}

function newest(list: WorkspaceSummary[]): WorkspaceSummary | undefined {
  return [...list].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))[0];
}

class ConversationsStore {
  /** Workspace summaries (no trees) — the sidebar list. */
  list = $state<WorkspaceSummary[]>([]);
  activeId = $state<string | null>(null);
  /** Per-panel branch trees keyed by stable panel id (one panel always present).
   *  THE read source: +page reads treeFor(panel), computes a new tree via tree.ts,
   *  commits with setTree(panel,…). N-panel: any number of keys.
   *  $state.raw — plain immutable objects, replaced wholesale per commit; never
   *  mutate a tree/node in place (nothing would react, nothing would save). */
  trees = $state.raw<Record<string, ConvTree>>({ [FIRST_PANEL_ID]: emptyTree() });
  /** THE authoritative client-side panel layout of the OPEN workspace (model per
   *  panel, in display order). Set on load/create, mutated ONLY by explicit user
   *  actions (`applyLayout`/`setPanelModel`, called from +page's panel lifecycle)
   *  or by a bus message STAMPED with this workspace (live-drive lockstep —
   *  `#adoptLayout`, wired in init). Rendering, persistence and bus claims all
   *  read THIS — never `live.state.panels`: the mirror follows a process-global
   *  bus that can transiently describe ANOTHER tab's workspace (bootstrap adopt,
   *  restart re-prime races), and reading it back at save time is how two
   *  cross-tab layout clobbers reached disk (ENGINEERING_LOGS 2026-07-24 +
   *  2026-08-06). $state.raw like `trees`: replaced wholesale, never mutated. */
  layout = $state.raw<PanelLayout[]>([{ id: FIRST_PANEL_ID, run_id: null, checkpoint: null }]);
  /** Transient hint shown when the terminal/another tab branched the workspace. */
  externalNotice = $state<string | null>(null);

  /** Hook (assigned by +page, which owns patchState): flush the pending debounced
   *  /api/state patch — assigning its response into live.state — BEFORE any
   *  workspace transition. Without this barrier a half-typed system prompt's
   *  200ms timer fires AFTER the new workspace loads and silently leaks onto
   *  it (persisted!), while the old workspace loses the edit — see
   *  tests/small-smokes/browser_sysprompt_switch.py. Every transition goes
   *  through #preSwitch so a future switch path gets the barrier for free. */
  flushStatePatch: (() => Promise<void>) | null = null;

  /** The one pre-transition barrier: settle the page's pending state patch so
   *  live.state is current before we persist against it or swap away from it. */
  async #preSwitch(): Promise<void> {
    await this.flushStatePatch?.();
  }

  // ── per-workspace panel UI (persisted with the workspace) ───────
  /** Panels folded out of view (tree kept alive). */
  reducedPanels = $state<Set<string>>(new Set());
  /** Panels the composer fires a send to (the "Send to" chips). */
  sendTargets = $state<Set<string>>(new Set());
  /** Defaulting bookkeeping: a panel is auto-added to `sendTargets` exactly once,
   *  the first time syncPanels() sees it (unless it's reduced). Persisted too so a
   *  restart restores the EXACT deselected/folded state rather than re-defaulting
   *  every panel ON. Not rendered ⇒ plain (non-reactive) field. */
  #seenPanels = new Set<string>();
  /** Monotonic panel-id counter, persisted per workspace. Rationale + the mint/seed
   *  rules live in ./panel-id.ts. */
  #panelSeq = 0;

  /** Next never-before-used panel id for this workspace. */
  mintPanelId(): string {
    const taken = new Set([...Object.keys(this.trees), ...this.layout.map((p) => p.id), ...this.#seenPanels]);
    const { id, seq } = mintId(this.#panelSeq, taken);
    this.#panelSeq = seq;
    return id;
  }

  /** Tokens of chats THIS browser fired — the chat store folds these from their
   *  bus bucket on chat_done (routed before the foreign path), so the external-fold
   *  reconcile below skips them. Kept in lockstep with #busy for switch-gating. */
  #ownTokens = new Set<string>();
  /** Reactive mirror of (#ownTokens.size > 0). A plain Set isn't tracked by Svelte 5,
   *  so `busy` reading the Set directly never re-fires the `disabled={…ws.busy}`
   *  bindings when a token is removed — the New/regen/edit buttons would latch
   *  disabled after a generation. Keep this in lockstep with every Set mutation. */
  #busy = $state(false);

  // ── ops persistence (HANDOFF_SERVER_AUTHORITY §4.2 — the mirror) ──
  /** The open workspace's rev as of the last body fetch / contiguously-applied
   *  ops event. Advances ONLY via events or a body fetch — never from an op
   *  POST's response (an interleaved other-tab op would be silently skipped). */
  #rev = 0;
  /** Meta (set_meta) debounce — layout toggles come in bursts; tree ops emit
   *  immediately (they're small and order-sensitive). */
  #metaTimer: ReturnType<typeof setTimeout> | null = null;
  /** System fields explicitly set by the pending meta write (see save()). */
  #metaExplicit: { system_prompt?: string | null; system_enabled?: boolean | null } = {};
  /** Single-flight guard for the desync refetch, plus the RE-ARM latch: an
   *  event that would have triggered a refetch while one is in flight must not
   *  be swallowed — the finished refetch may have adopted a body from BEFORE
   *  that event (GET raced the write). */
  #refetching = false;
  #refetchAgain = false;
  /** Highest ops-event rev SEEN on the stream, per workspace — updated for
   *  EVERY event, foreign ones included, nothing applied. The catch-up oracle
   *  for the two adoption windows (a refetch racing new writes; the open/switch
   *  gap between the body GET and #setActive, where destination events are
   *  correctly filtered but would otherwise never be made up). */
  #seenRevs = new Map<string, number>();
  /** Own meta batches on the wire (see #flushMeta). */
  #metaClaimsInFlight = 0;
  /** The id remove() is deleting right now — its own workspace_deleted
   *  broadcast must not latch the tab that asked for the delete. */
  #selfDeleting: string | null = null;
  /** Guards a mid-session body-fetch failure (see remove()) AND a present-but-
   *  malformed stored tree (treeUnreadable): the store would be showing emptiness
   *  it did not author — emitting ops then would persist that emptiness over the
   *  stored data, so op emission latches off until a successful load. */
  #loadFailed = false;
  /** Supersedes an in-flight switchTo body fetch when a newer switch starts. */
  #switchSeq = 0;
  #noticeTimer: ReturnType<typeof setTimeout> | null = null;
  /** Id of the current UNSAVED draft (a new workspace that exists only in `list`,
   *  not yet on the backend). It is materialized on the first save (= first real
   *  change), and discarded if abandoned untouched. null ⇒ no pending draft. */
  #draftId: string | null = null;

  treeFor(panel: Panel): ConvTree {
    return this.trees[panel] ?? emptyTree();
  }

  /** THE single writer of `activeId`. Keeps `live.workspaceId` in lockstep so the
   *  bus filter (bus-scope.ts) always knows which workspace this tab owns — a gap
   *  between the two would let another tab's layout through for a beat, which is
   *  long enough to be mirrored and saved. */
  #setActive(id: string | null): void {
    this.activeId = id;
    live.workspaceId = id;
  }

  /** Stamp a workspace-scoped state patch with the workspace it describes, so other
   *  tabs can tell it isn't theirs. Every setState in this store goes through here
   *  (see bus-scope.ts: unstamped workspace writes are what other tabs adopt). */
  #ownPatch(patch: StatePatch): StatePatch {
    return { ...patch, workspace_id: this.activeId };
  }

  /** True while ANY own chat is in flight — its fold (from the response stream)
   *  outlives the bucket `running` flag (which clears on the bus chat_done). Gate
   *  workspace switch/create/delete on this so an in-flight fold can't land on
   *  (and be dropped by) a freshly-swapped workspace tree. */
  get busy(): boolean {
    return this.#busy;
  }

  // ── ownership tokens ─────────────────────────────────────────────
  newToken(): string {
    const t = 'ct' + Math.random().toString(36).slice(2, 10);
    this.#ownTokens.add(t);
    this.#busy = true;
    return t;
  }
  endToken(t: string): void {
    this.#ownTokens.delete(t);
    this.#busy = this.#ownTokens.size > 0;
  }

  constructor() {
    opsEmitter.configure({
      ensureMaterialized: (id) => this.#materialize(id),
      onDesync: (id, why) => this.#desync(id, why),
      notice: (msg) => this.#flashNotice(msg)
    });
  }

  // ── the single commit entry ──────────────────────────────────────
  /** Commit a new tree for a panel: update reactive state, mirror the active
   *  path into PlaygroundState.messages (so the CLI/sampler see it), and
   *  persist as ops. `opts.ops` = the precise tree-level ops that produced
   *  `next` (panel-stamped here); WITHOUT them the whole panel tree ships as a
   *  replace_tree — the fallback that guarantees no call-site can silently skip
   *  persistence (undo uses it on purpose: its tree is the current one plus
   *  the restored nodes, see undo.ts restoreInto). */
  setTree(panel: Panel, next: ConvTree, opts?: { persist?: boolean; ops?: TreeOp[] }): void {
    this.trees = { ...this.trees, [panel]: next };
    this.#mirror();
    if (opts?.persist === false) return;
    if (opts?.ops) {
      if (opts.ops.length) this.#emit(opts.ops.map((o) => ({ ...o, panel })));
    } else {
      this.#emit([{ op: 'replace_tree', panel, tree: next }]);
    }
  }

  /** THE op-emission gate: binds the batch to the ACTIVE workspace and drops it
   *  when there is nothing safe to bind to (no workspace, or the load-failed
   *  latch — persisting would ship emptiness we did not author). Returns
   *  whether the batch was actually queued. */
  #emit(ops: WorkspaceOp[]): boolean {
    const id = this.activeId;
    if (!id) return false;
    if (this.#loadFailed) {
      console.warn('workspace failed to load — change NOT persisted');
      return false;
    }
    opsEmitter.emit(id, ops);
    return true;
  }

  /** True when the bus currently describes OUR workspace. A tab that isn't the
   *  owner still renders and saves its own workspace (layout + trees are store-
   *  owned) — it just isn't the one `tinkpg` is pointed at, and its incremental
   *  workspace writes are dropped server-side until it claims. Decided on
   *  `live.busId` — the RAW bus stamp — not the mirror's `workspace_id`, which
   *  mergeBusState pins to our own id and so can never report a lost bus
   *  (that read made this constant-true and focus-reclaim dead code). */
  get ownsBus(): boolean {
    return live.busId === this.activeId;
  }

  /** This workspace's full picture for the bus: OUR layout + per-panel thread
   *  system. (The transcript echo retired with P3 — the tree IS the transcript,
   *  read over /api/workspaces.) A patch carrying this is a CLAIM — after
   *  it, the bus coherently describes us (see bus-scope.ts on why a partial
   *  write from a non-owner would leave the bus a chimera). Built from the
   *  store-owned `layout`, never from `live.state.panels`: claiming with the
   *  mirror's panels was itself a chimera generator — a transiently-foreign
   *  mirror got re-published stamped with OUR id, which every same-id client
   *  then adopted as its own. */
  #busPanels(): StatePatch['panels'] {
    return this.layout.map((p) => ({
      id: p.id,
      run_id: p.run_id ?? null,
      checkpoint: p.checkpoint ?? null,
      thread_system_prompt: this.#threadSystem(p.id)
    }));
  }

  /** Extra fields +page folds into a workspace-scoped patch so a NON-OWNER tab's
   *  write re-claims the bus in the same request (no extra round trip, and no
   *  half-applied patch). Empty when we already own it. */
  claimFields(): StatePatch {
    if (this.ownsBus) return {};
    const panels = this.#busPanels();
    return panels?.length ? { panels } : {};
  }

  /** Explicitly assert that this tab's workspace is the one on screen. Called on
   *  window focus: the bus tracks exactly one workspace, so "the tab you last
   *  looked at is the one the terminal drives" is the rule that makes multi-tab
   *  predictable. Skipped while a chat is streaming — whoever owns the bus then
   *  is mid-drive and the CLI is likely watching it. */
  async claimBus(): Promise<void> {
    if (!this.activeId || this.ownsBus || live.state?.running) return;
    const panels = this.#busPanels();
    if (!panels?.length) return;
    const next = await api
      .setState(
        this.#ownPatch({
          panels,
          system_prompt: live.state?.system_prompt ?? null,
          system_enabled: live.state?.system_enabled ?? null
        })
      )
      .catch(() => null);
    live.adopt(next);
  }

  // ── layout mutation (the single entry for panel add/remove/reorder/model) ──
  /** Claims currently on the wire from applyLayout. While >0, incoming bus
   *  panel lists are NOT adopted into `layout`: they describe a state our own
   *  in-flight full-replace is about to overwrite server-side anyway, and
   *  adopting a stale echo mid-burst would briefly revert a rapid second edit. */
  #layoutClaimsInFlight = 0;

  /** Replace the open workspace's panel layout. Updates the authoritative copy
   *  (rendering follows synchronously), schedules persistence, and re-CLAIMS
   *  the bus with the new panel list + tree echoes — so after any layout edit
   *  the bus coherently describes THIS tab, whoever held it before. */
  applyLayout(next: PanelLayout[]): void {
    this.layout = next.map((p) => ({
      id: p.id,
      run_id: p.run_id ?? null,
      checkpoint: p.checkpoint ?? null
    }));
    this.save();
    this.#layoutClaimsInFlight++;
    api
      .setState(this.#ownPatch({ panels: this.#busPanels() }))
      .then((n) => live.adopt(n))
      .catch(() => {})
      .finally(() => {
        this.#layoutClaimsInFlight--;
      });
  }

  /** Point one panel at a model (the sidebar picker / CLI-independent path). */
  setPanelModel(panel: Panel, run_id: string | null, checkpoint: string | null): void {
    this.applyLayout(
      this.layout.map((p) => (p.id === panel ? { ...p, run_id, checkpoint } : p))
    );
  }

  /** A server state stamped with OUR workspace carried a panel list — the CLI
   *  (or another tab on the same workspace) drove the layout. Adopt it as ours
   *  and persist, keeping live-drive lockstep. Content-compare first: our own
   *  claims echo back through here, and marking dirt on an echo would loop
   *  save → broadcast → save. */
  #adoptLayout(panels: { id: string; run_id: string | null; checkpoint: string | null }[]): void {
    if (!this.activeId || this.#layoutClaimsInFlight > 0) return;
    if (!panels.length) return; // a bus with no panels describes nothing — never adopt emptiness
    const next: PanelLayout[] = panels.map((p) => ({
      id: p.id,
      run_id: p.run_id ?? null,
      checkpoint: p.checkpoint ?? null
    }));
    const same =
      next.length === this.layout.length &&
      next.every((p, i) => {
        const c = this.layout[i];
        return c.id === p.id && (c.run_id ?? null) === p.run_id && (c.checkpoint ?? null) === p.checkpoint;
      });
    if (same) return;
    this.layout = next;
    this.save();
  }

  /** The store's half of a bus re-prime (state.svelte.ts `reprimeClaim`): the
   *  open workspace's true claim, or null when none is open. */
  #reprimeClaim(): StatePatch | null {
    if (!this.activeId) return null;
    return this.#ownPatch({
      panels: this.#busPanels(),
      system_prompt: live.state?.system_prompt ?? null,
      system_enabled: live.state?.system_enabled ?? null
    });
  }

  /** The active THREAD's system prompt for a panel (its selected root node's) —
   *  travels with every echo so a mid-thread CLI send inherits the right prompt. */
  #threadSystem(pid: string): string | null {
    const tree = this.trees[pid];
    if (!tree) return null;
    const rootId = selectedChildId(tree, ROOT);
    return (rootId ? tree.nodes[rootId]?.system_prompt : null) ?? null;
  }

  #mirror(): void {
    // Mirror each OPEN panel's active-THREAD system prompt into PlaygroundState
    // (a mid-thread CLI send inherits it server-side). This is all that's left
    // of the old per-panel transcript echo (P3): message text lives in the
    // workspace tree, which the CLI reads directly. Restricted to panels in OUR
    // layout — a tree that outlived its panel must not re-register it.
    const liveIds = new Set(this.layout.map((p) => p.id));
    const panel_thread_system: Record<string, string | null> = {};
    for (const pid of Object.keys(this.trees)) {
      if (liveIds.size && !liveIds.has(pid)) continue;
      panel_thread_system[pid] = this.#threadSystem(pid);
    }
    // A non-owner's per-panel write would be dropped server-side — fold in the
    // claim so the mirror lands either way.
    api
      .setState(this.#ownPatch({ ...this.claimFields(), panel_thread_system }))
      .catch(() => {});
  }

  /** Duplicate one panel's tree into another (used when a new compare panel should
   *  start from an existing thread). structuredClone, NOT $state.snapshot: trees
   *  are $state.raw (plain objects), so snapshot would return the SAME ref and
   *  alias two panels. Light trees make the clone cheap. Does NOT clear other
   *  panels' live buckets, so adding a panel can't wipe an in-flight stream. */
  duplicateTo(srcPanel: Panel, dstPanel: Panel): void {
    this.trees = { ...this.trees, [dstPanel]: structuredClone(this.treeFor(srcPanel)) };
    this.#mirror();
    // Keep-ids clone server-side — no tree bytes travel, and shared ids preserve
    // cross-panel blob sharing (workspace_store keys blobs by node id).
    this.#emit([{ op: 'copy_tree', from_panel: srcPanel, to_panel: dstPanel }]);
  }

  /** Seed a panel with an EMPTY thread (Shift+add panel = blank, vs duplicateTo's
   *  clone-the-first-panel default). Mirrors + persists like duplicateTo. */
  freshTree(panel: Panel): void {
    this.trees = { ...this.trees, [panel]: emptyTree() };
    this.#mirror();
    this.#emit([{ op: 'replace_tree', panel, tree: emptyTree() }]);
  }

  /** Drop a panel's tree (on panel removal). The LAST tree is never dropped —
   *  a workspace always keeps at least one panel (any id). */
  dropTree(panel: Panel): void {
    if (!(panel in this.trees) || Object.keys(this.trees).length <= 1) return;
    const next = { ...this.trees };
    delete next[panel];
    this.trees = next;
    this.#mirror();
    this.#emit([{ op: 'replace_tree', panel, tree: null }]);
  }

  // ── panel UI (folded / send-targets), persisted with the workspace ──
  /** Toggle a panel as a composer send-target. */
  toggleSendTarget(panel: Panel): void {
    this.sendTargets = this.sendTargets.has(panel)
      ? new Set([...this.sendTargets].filter((t) => t !== panel))
      : new Set([...this.sendTargets, panel]);
    this.save();
  }
  /** Fold a panel out of view → also stop sending to it (off by default). */
  reducePanel(panel: Panel): void {
    this.reducedPanels = new Set([...this.reducedPanels, panel]);
    this.sendTargets = new Set([...this.sendTargets].filter((t) => t !== panel));
    this.save();
  }
  /** Un-fold a panel → resume sending to it. */
  restorePanel(panel: Panel): void {
    this.reducedPanels = new Set([...this.reducedPanels].filter((t) => t !== panel));
    this.sendTargets = new Set([...this.sendTargets, panel]);
    this.save();
  }
  /** Forget a removed panel's UI bookkeeping (called from +page removePanel).
   *  `#seenPanels` is deliberately NOT pruned: it is the workspace's ledger of
   *  every id it ever minted, which is what keeps a closed panel's number from
   *  being handed to a new column (and what seeds `panel_seq` for a workspace
   *  saved before that counter existed). Keeping a dead id costs nothing — its
   *  only reader gates first-sight sendTargets defaulting, and a monotonic id is
   *  never seen twice. The live-view sets DO get pruned. */
  dropPanelUi(panel: Panel): void {
    this.reducedPanels = new Set([...this.reducedPanels].filter((t) => t !== panel));
    this.sendTargets = new Set([...this.sendTargets].filter((t) => t !== panel));
    this.save();
  }
  /** Reconcile against the current panel list: default each NEWLY-seen panel into
   *  sendTargets (unless reduced). Purely additive — removed panels are left in the
   *  sets (harmless: every reader filters by the live panel list) and pruned only on
   *  explicit dropPanelUi, so a transient gap while a panel's state-bus patch lands
   *  can't wrongly re-default a previously-deselected panel. Persists on change. */
  syncPanels(ids: string[]): void {
    // Gate on #seenPanels (non-reactive) FIRST and read sendTargets/reducedPanels
    // only when a genuinely new panel appears — so in steady state the calling
    // effect depends on the panel list alone, not on the sets it writes.
    let targets: Set<string> | null = null;
    let seenChanged = false;
    for (const id of ids) {
      if (this.#seenPanels.has(id)) continue;
      this.#seenPanels.add(id);
      seenChanged = true;
      if (!this.reducedPanels.has(id)) {
        if (!targets) targets = new Set(this.sendTargets);
        targets.add(id);
      }
    }
    if (targets) this.sendTargets = targets;
    if (seenChanged) this.save(); // persist the seen growth + any new default
  }
  /** Restore the panel-UI sets from a loaded workspace. Missing keys (legacy
   *  workspaces) ⇒ empty sets + empty seen ⇒ syncPanels defaults every open
   *  panel ON, exactly as before this was persisted. */
  #applyPanelUi(conv: Workspace): void {
    this.reducedPanels = new Set(conv.reduced_panels ?? []);
    this.sendTargets = new Set(conv.send_targets ?? []);
    this.#seenPanels = new Set(conv.seen_panels ?? []);
    // max(), not ??: a stored 0 is what a writer that omits the field leaves behind
    // (and 0 isn't nullish, so ?? would take it as authoritative). Seeding from the
    // ids actually present can only ever be too LOW, never too high.
    this.#panelSeq = Math.max(conv.panel_seq ?? 0, highestPanelSeq(conv));
  }

  /** The panel layout (model selection per panel) currently shown — what a new
   *  workspace inherits. With a workspace open that's OUR layout; before any is
   *  open (the fresh-install draft) it's the session-restored bus panels.
   *  Always at least one blank panel. */
  #currentLayout(): PanelLayout[] {
    if (this.activeId) return this.layout.map((p) => ({ ...p }));
    const restored = (live.state?.panels ?? []).map((p) => ({
      id: p.id,
      run_id: p.run_id,
      checkpoint: p.checkpoint
    }));
    return restored.length ? restored : [{ id: FIRST_PANEL_ID, run_id: null, checkpoint: null }];
  }

  /** Reset every open panel's tree to empty (fresh thread, same panel layout).
   *  `mark` schedules the emptiness for persistence (dirty new ids + dropped
   *  stale ids); pass false ONLY for an unsaved draft, which must stay unsaved. */
  #freshTrees(mark: boolean): Promise<void> {
    const ids = this.layout.map((p) => p.id);
    if (!ids.length) ids.push(FIRST_PANEL_ID); // no panels known yet → the default first slot
    const prev = Object.keys(this.trees);
    this.trees = Object.fromEntries(ids.map((id) => [id, emptyTree()]));
    if (mark) {
      this.#emit([
        ...ids.map((id): WorkspaceOp => ({ op: 'replace_tree', panel: id, tree: emptyTree() })),
        ...prev
          .filter((p) => !ids.includes(p))
          .map((p): WorkspaceOp => ({ op: 'replace_tree', panel: p, tree: null }))
      ]);
    }
    return api
      .setState(
        this.#ownPatch({
          panel_thread_system: Object.fromEntries(ids.map((id) => [id, null]))
        })
      )
      .then(() => {})
      .catch(() => {});
  }

  // ── persistence (ops emission; flush-on-switch) ───────────────────
  /** Public save = a workspace-LEVEL (non-tree) change: panel layout, model
   *  selection, send-targets, folds, system prompt, seen bookkeeping. Debounced
   *  into ONE set_meta op (layout toggles come in bursts); tree mutations emit
   *  their own ops at the call site. */
  save(explicit?: Pick<ConvFields, never> & { system_prompt?: string | null; system_enabled?: boolean | null }): void {
    if (!this.activeId || this.#loadFailed) return;
    // System fields ride ONLY when the calling ACTION owns them, with the
    // values it just set — never read back off the bus mirror at flush time.
    // A transient mirror regression (a late claim response wiping
    // system_prompt) used to be PERSISTED by the next unrelated meta write
    // (fold/power click → set_meta carrying the nulled mirror), which is the
    // task-#7 data loss: the typed prompt came back null live AND stored.
    // set_meta is key-presence-based server-side, so omitting the keys leaves
    // the stored values untouched.
    if (explicit) this.#metaExplicit = { ...this.#metaExplicit, ...explicit };
    if (this.#metaTimer) clearTimeout(this.#metaTimer);
    this.#metaTimer = setTimeout(() => this.#flushMeta(), 400);
  }

  #flushMeta(): void {
    if (this.#metaTimer) {
      clearTimeout(this.#metaTimer);
      this.#metaTimer = null;
    } else return; // nothing pending
    const explicit = this.#metaExplicit;
    this.#metaExplicit = {};
    if (!this.#emit([{ op: 'set_meta', fields: { ...this.#fields(), ...explicit } }])) return;
    // While our own meta write is on the wire, its echo carries the Sets as of
    // EMIT time — applying that over a toggle made during the RTT would revert
    // the toggle on screen and then persist the reversion (the next #fields()
    // reads the reverted Sets). Count claims; #applyMetaEvent skips the two
    // LWW Set fields while any are in flight — the #layoutClaimsInFlight
    // pattern, for meta.
    this.#metaClaimsInFlight++;
    void opsEmitter.flush().finally(() => {
      this.#metaClaimsInFlight--;
    });
  }

  /** The workspace-level field set, read at EMIT time; flush-on-switch keeps it
   *  bound to the right workspace. `panels` comes from the STORE-OWNED layout —
   *  never from live.state, whose panels follow a process-global bus that can
   *  transiently describe another tab's workspace (reading it at save time is
   *  exactly how two cross-tab layout clobbers reached disk). system_prompt
   *  stays a mirror read: mergeBusState protects it per-workspace, and the
   *  +page patch flush (#preSwitch) settles it before any transition. */
  #fields(): Omit<ConvFields, 'system_prompt' | 'system_enabled'> {
    return {
      panels: this.layout.map((p) => ({
        id: p.id,
        run_id: p.run_id ?? null,
        checkpoint: p.checkpoint ?? null
      })),
      reduced_panels: [...this.reducedPanels],
      send_targets: [...this.sendTargets],
      seen_panels: [...this.#seenPanels],
      panel_seq: this.#panelSeq
    };
  }

  /** opsEmitter seam: a batch for an unsaved DRAFT materializes it first (POST
   *  create with the draft's id + the CURRENT trees — the batch then replays
   *  idempotently over what the create shipped). Runs inside the emitter chain,
   *  which flush-on-switch drains before any transition, so `this.trees` still
   *  belongs to `id` here. Throws on failure (the emitter takes the desync
   *  path); #draftId is restored so a retry still creates it. */
  async #materialize(id: string): Promise<void> {
    if (id !== this.#draftId) return;
    this.#draftId = null;
    try {
      const name = this.list.find((c) => c.id === id)?.name ?? 'Untitled';
      const body = await api.createWorkspace({ id, name, ...this.#fields(), trees: { ...this.trees } });
      // The create is this mirror's rev BASELINE (same role as a body GET) —
      // ops events for the follow-up batches arrive contiguously above it.
      if (this.activeId === id) this.#rev = body.rev ?? 0;
      this.list = this.list.map((c) =>
        c.id === id ? { ...c, updated_at: body.updated_at ?? c.updated_at } : c
      );
    } catch (e) {
      this.#draftId = id;
      throw e;
    }
  }

  /** opsEmitter seam: a rejected batch / exhausted retries — the mirror may
   *  have diverged from the store. Refetch the light body (single-flight). */
  #desync(id: string, why: string): void {
    console.warn(`workspace ${id} ops desync (${why}) — refetching`);
    if (id === this.activeId) this.#refetchBody();
  }

  #refetchBody(): void {
    if (this.#refetching) {
      this.#refetchAgain = true; // don't swallow the signal — go again after
      return;
    }
    this.#refetching = true;
    void (async () => {
      try {
        for (let round = 0; round < 3; round++) {
          this.#refetchAgain = false;
          // Let queued batches settle first — refetching UNDER a pending batch
          // would briefly rewind the view to a body those ops haven't reached.
          await opsEmitter.flush();
          const id = this.activeId;
          if (!id || id === this.#draftId) return;
          let conv: Workspace;
          try {
            conv = await api.getWorkspace(id);
          } catch (e) {
            // A tab that cannot re-read its workspace (deleted? server gone?)
            // must not keep editing into the void: latch op emission off,
            // loudly. A later successful load (switch back, recovery refetch)
            // unlatches via #loadTrees.
            if (this.activeId === id) {
              this.#loadFailed = true;
              this.#flashNotice(
                'This workspace could not be re-read — changes are NOT being saved. Switch away and back, or reload.'
              );
            }
            console.warn('workspace refetch failed', e);
            return;
          }
          if (this.activeId !== id) return;
          const lastAdopted = this.#rev;
          await this.#loadTrees(conv);
          this.#mirror();
          // The GET may have raced newer writes: events seen on the stream
          // above the adopted rev mean the body is already stale — go again.
          // Unless the body rev did NOT move between rounds: then the seen-rev
          // is from a PREVIOUS incarnation of this id (deleted + re-created —
          // the rev line restarted) and chasing it would never converge; adopt
          // the store's truth and let the stream correct us if we're wrong.
          if ((this.#seenRevs.get(id) ?? 0) > this.#rev) {
            if (round > 0 && this.#rev === lastAdopted) {
              this.#seenRevs.set(id, this.#rev);
              return;
            }
            this.#refetchAgain = true;
          }
          if (!this.#refetchAgain) return;
        }
        // Bounded: leave the next arriving event to re-trigger, but say so.
        this.#flashNotice('Workspace is changing faster than it can be re-read — the view may lag briefly.');
      } finally {
        this.#refetching = false;
      }
    })();
  }

  async flush(): Promise<void> {
    this.#flushMeta();
    // A transition must never overlap a batch still on the wire.
    await opsEmitter.flush();
  }

  // ── load / switch / create / rename / remove ─────────────────────
  /** Load the workspace SUMMARIES and open the active one (fetching its body).
   *  If `preferredId` is given (e.g. from the `?c=` URL param) and matches, open
   *  it; otherwise open the newest. Returns whether `preferredId` was honored
   *  (false ⇒ absent or unknown, so the caller can normalize the URL / notify). */
  async load(preferredId?: string | null): Promise<boolean> {
    await this.#preSwitch();
    const list = await api.listWorkspaces();
    this.list = list;
    if (!list.length) {
      // Nothing saved yet → open an unsaved draft (an empty workspace is never
      // persisted until it changes). create() sets it active + lays out panels.
      await this.create('Untitled', this.#currentLayout());
      return false;
    }
    const preferred = preferredId ? list.find((c) => c.id === preferredId) : undefined;
    const active = preferred ?? newest(list)!;
    const conv = await api.getWorkspace(active.id); // throws → +page's load banner
    nodeBlobs.reset(active.id);
    this.#setActive(active.id);
    await this.#loadTrees(conv);
    this.#mirror();
    return !!preferred;
  }

  #inflightSwitch: { id: string; p: Promise<void> } | null = null;

  /** COALESCE duplicate switches to the same id: the `?w=` effect re-fires on
   *  dep changes mid-switch (busy/list flips), so one URL change used to spawn
   *  TWO full switches — measured: every palette jump fetched the body twice,
   *  and the second load's wholesale `trees` assignment clobbered any tree edit
   *  made right after the first landed (the search palette's jump-and-reveal
   *  lost its selection ~1-in-6; ENGINEERING_LOGS 2026-08-10). A switch to a
   *  DIFFERENT id still goes through and supersedes via #switchSeq as before. */
  async switchTo(id: string): Promise<void> {
    if (this.#inflightSwitch?.id === id) return this.#inflightSwitch.p;
    const p = this.#doSwitchTo(id);
    this.#inflightSwitch = { id, p };
    try {
      await p;
    } finally {
      if (this.#inflightSwitch?.p === p) this.#inflightSwitch = null;
    }
  }

  async #doSwitchTo(id: string): Promise<void> {
    if (id === this.activeId) return;
    // Settle the pending state patch FIRST: flush() below reads live.state
    // (system_prompt, panel layout) when persisting the workspace we're leaving.
    await this.#preSwitch();
    // If we're leaving an untouched draft, drop it (flush below materializes it first
    // if it had any pending change, clearing #draftId so the discard then no-ops).
    const leavingDraft = this.#draftId !== null && this.#draftId === this.activeId;
    await this.flush();
    if (leavingDraft) this.#discardDraftIfUntouched();
    if (!this.list.find((c) => c.id === id)) return;
    // Fetch the body BEFORE committing any transition state: on failure we stay
    // fully on the current workspace (nothing half-switched to mis-save
    // against), on supersession (a newer switch) we just stand down.
    const seq = ++this.#switchSeq;
    let conv: Workspace;
    try {
      conv = await api.getWorkspace(id);
    } catch (e: any) {
      this.#flashNotice(`Failed to open workspace: ${e?.message ?? e}`);
      return;
    }
    if (seq !== this.#switchSeq) return;
    // Edits made to the OLD workspace while the body was in flight: flush them
    // now, while activeId/live.state still belong to it.
    await this.flush();
    if (seq !== this.#switchSeq) return;
    live.clearBuckets();
    nodeBlobs.reset(id);
    this.#setActive(id);
    await this.#loadTrees(conv);
    this.#mirror();
  }

  /** Create + switch to a new workspace. `panels` is the layout it opens with:
   *  callers inherit the current workspace's models (a new workspace keeps the
   *  MODELS, never the messages) or pass a single blank panel (Shift+New). Omitted ⇒
   *  inherit the current layout.
   *
   *  The new workspace is an UNSAVED DRAFT — it lives only in `list` and is NOT
   *  persisted until the first real change materializes it (#doSave). So a 'New' you
   *  never touch leaves nothing behind on disk. */
  async create(name = 'Untitled', panels?: PanelLayout[], id?: string): Promise<void> {
    await this.#preSwitch();
    await this.flush();
    live.clearBuckets();
    // A previous untouched draft is abandoned (don't pile up empty 'Untitled's).
    this.#discardDraftIfUntouched();
    const layout = panels && panels.length ? panels : this.#currentLayout();
    const ids = layout.map((p) => p.id); // non-empty (#currentLayout guarantees ≥1)
    const now = new Date().toISOString();
    // Pre-seed seen/send to the open panels (the default-on state) so the +page
    // syncPanels reconcile finds nothing new and does NOT call save() — otherwise
    // laying out a fresh draft would itself materialize an empty workspace.
    // The id may be MINTED BY THE CALLER so it can push ?c= BEFORE create — the
    // trailing `await api.setState` below yields to the reactive scheduler while
    // activeId is newly-set, and the ?c= sync effect would switch right back to
    // the old workspace if the URL still pointed there (an id not yet in `list`
    // is ignored by that effect, so a caller-set URL is safe). See newConversation.
    const draft: WorkspaceSummary = {
      id: id ?? crypto.randomUUID(),
      name,
      panels: layout,
      created_at: now,
      updated_at: now
    };
    this.list = [draft, ...this.list];
    this.#setActive(draft.id);
    this.#draftId = draft.id;
    this.#loadFailed = false;
    this.#rev = 0;
    nodeBlobs.reset(draft.id);
    this.layout = layout.map((p) => ({
      id: p.id,
      run_id: p.run_id ?? null,
      checkpoint: p.checkpoint ?? null
    }));
    this.reducedPanels = new Set();
    this.sendTargets = new Set(ids);
    this.#seenPanels = new Set(ids);
    // A fresh workspace gets its OWN counter, seeded from the ids it starts with —
    // not whatever the previously-open one had reached. Carrying that over is safe
    // (mintPanelId also refuses anything in `taken`) but it persists a number with
    // no relation to this workspace, and the CLI's `_layout_panel_ids` reads
    // `panel_seq` as its primary bound.
    this.#panelSeq = highestPanelSeq({ panels: layout });
    // Lay out the inherited/blank panels with EMPTY trees + transcripts. One
    // optimistic setState (panels + cleared echoes) so live.state reflects the new
    // layout immediately — the SSE patch lags a beat behind the POST.
    this.trees = Object.fromEntries(ids.map((id) => [id, emptyTree()]));
    const next = await api
      .setState(
        this.#ownPatch({
          panels: layout.map((p) => ({ id: p.id, run_id: p.run_id, checkpoint: p.checkpoint })),
          panel_thread_system: Object.fromEntries(ids.map((id) => [id, null]))
        })
      )
      .catch(() => null);
    live.adopt(next);
  }

  /** Drop the current draft from `list` if it was never persisted (untouched). Called
   *  after flush() — a touched draft is materialized by flush first, clearing #draftId,
   *  so this only removes genuinely-empty ones. */
  #discardDraftIfUntouched(): void {
    if (!this.#draftId) return;
    const id = this.#draftId;
    this.#draftId = null;
    this.list = this.list.filter((c) => c.id !== id);
  }

  async rename(id: string, name: string): Promise<void> {
    if (id === this.#draftId) {
      // Unsaved draft: keep the name locally and materialize it (a rename IS a change).
      this.list = this.list.map((c) => (c.id === id ? { ...c, name } : c));
      this.save();
      return;
    }
    const updated = await api.patchWorkspace(id, { name });
    this.list = this.list.map((c) =>
      c.id === id ? { ...c, name: updated.name, updated_at: updated.updated_at } : c
    );
  }

  async remove(id: string): Promise<void> {
    await this.#preSwitch();
    await this.flush();
    const removingDraft = id === this.#draftId;
    // Never leave zero workspaces: the last one resets in place (same id,
    // empty trees, default name).
    if (this.list.length <= 1) {
      live.clearBuckets();
      nodeBlobs.reset(id);
      // A draft must STAY unsaved through the reset — don't mark its emptied
      // trees for persistence (that would materialize an empty workspace).
      await this.#freshTrees(!removingDraft);
      if (removingDraft) {
        // Already unsaved + now empty → stay a draft, just reset the name locally.
        this.list = this.list.map((c) => (c.id === id ? { ...c, name: 'Untitled' } : c));
      } else {
        if (this.activeId) await this.rename(this.activeId, 'Untitled').catch(() => {});
        this.save();
      }
      return;
    }
    // A draft only exists locally — skip the backend delete (it would 404).
    if (removingDraft) this.#draftId = null;
    else {
      // Our own delete's broadcast must not latch/notify THIS tab.
      this.#selfDeleting = id;
      try {
        await api.deleteWorkspace(id);
      } finally {
        setTimeout(() => (this.#selfDeleting = null), 5000); // outlive the echo
      }
    }
    this.list = this.list.filter((c) => c.id !== id);
    if (this.activeId === id) {
      live.clearBuckets();
      const next = newest(this.list)!;
      nodeBlobs.reset(next.id);
      this.#setActive(next.id);
      let conv: Workspace | null = null;
      try {
        conv = await api.getWorkspace(next.id);
      } catch (e: any) {
        // Deleted the open workspace but couldn't load the next: latch saves
        // off (an empty PUT would clobber the stored data) and say so.
        this.trees = { [FIRST_PANEL_ID]: emptyTree() };
        this.#loadFailed = true;
        this.#flashNotice(
          `Failed to load the next workspace (${e?.message ?? e}) — changes are NOT being saved; reload the page.`
        );
        return;
      }
      await this.#loadTrees(conv);
      this.#mirror();
    }
  }

  /** Reset every open panel's tree for a fresh thread under the SAME workspace. */
  async resetActive(): Promise<void> {
    await this.#preSwitch();
    undo.group('reset thread', () => {
      for (const panel of Object.keys(this.trees)) undo.capture(panel, 'reset thread');
    });
    live.clearBuckets();
    await this.#freshTrees(true);
    this.save();
  }

  /** Migration read-shim: prefer the new {trees} shape; fall back to the legacy
   *  {tree, compare_tree} (synthesizing reserved 'primary'/'compare' ids) so an
   *  un-migrated saved workspace loads without losing a user-authored compare
   *  tree. asTree() returns emptyTree() on malformed input. */
  async #loadTrees(conv: Workspace): Promise<void> {
    this.#loadFailed = false; // a body arrived — op emission is safe again…
    this.#rev = conv.rev ?? 0;
    // Catch-up (the open/switch window): events for THIS workspace that arrived
    // between the body GET and now were workspace-filtered while it wasn't
    // active — if the stream has already shown a higher rev, the adopted body
    // is stale (the tinkpg-send-finishing-as-you-open flow). The refetch loop
    // owns convergence + the restarted-rev-line escape.
    if ((this.#seenRevs.get(conv.id) ?? 0) > this.#rev) this.#refetchBody();
    // …unless a stored tree is PRESENT BUT MALFORMED: asTree renders it as an
    // empty panel, and persisting anything from this workspace would replace the
    // real (still-on-disk) data with that emptiness. Latch op emission off and
    // say so — a panel that refuses to save must be loud (ideas/astree-silent-emptytree).
    const unreadable = [
      ...Object.entries(conv.trees ?? {}).filter(([, t]) => treeUnreadable(t)).map(([pid]) => pid),
      ...(!conv.trees && treeUnreadable(conv.tree) ? ['primary'] : []),
      ...(!conv.trees && treeUnreadable(conv.compare_tree) ? ['compare'] : [])
    ];
    if (unreadable.length) {
      this.#loadFailed = true;
      this.#flashNotice(
        `Panel ${unreadable.join(', ')}'s stored tree is unreadable — changes are NOT being saved. ` +
          `The data is intact on disk; reload, or check the server logs.`
      );
    }
    // The layout we restore self-heals PHANTOM rows — run_id null AND no tree
    // content (mirrors the server's tree_ops.normalize_panels: a run_id-less
    // panel WITH nodes is legitimate — an add-panel before a model pick, a
    // trash-restored column). If every row is blank, keep the first (a single
    // blank panel is the empty-thread state). Legacy convs (no stored layout)
    // ⇒ null ⇒ keep whatever panels are shown.
    const hasData = (pid: Panel) => {
      const t = conv.trees?.[pid];
      return !!t && typeof t === 'object' && !!(t as ConvTree).nodes && Object.keys((t as ConvTree).nodes).length > 0;
    };
    let layout =
      Array.isArray(conv.panels) && conv.panels.length
        ? conv.panels.filter((p) => p.run_id != null || hasData(p.id))
        : null;
    if (layout && !layout.length) layout = [conv.panels![0]];

    if (conv.trees && typeof conv.trees === 'object') {
      const map: Record<string, ConvTree> = {};
      // Orphan trees (panels outside the stored layout) are KEPT: the server is
      // authoritative about the body now, and #mirror's layout filter is what
      // prevents the phantom re-feed. Dropping them here made load disagree
      // with refetch — and ate trash-restored columns.
      for (const [pid, t] of Object.entries(conv.trees)) map[pid] = asTree(t);
      // A workspace always loads with ≥1 tree (blank first slot = empty thread).
      if (!Object.keys(map).length) map[layout?.[0]?.id ?? FIRST_PANEL_ID] = emptyTree();
      this.trees = map;
    } else {
      // Legacy {tree, compare_tree} read-shim. Persistence-side the server
      // normalizes the stored body on its first op (P1) — no full-map first
      // save needed from here anymore.
      const map: Record<string, ConvTree> = { primary: asTree(conv.tree) };
      if (conv.compare_tree) map.compare = asTree(conv.compare_tree);
      this.trees = map;
    }
    this.#applyPanelUi(conv);
    // The STORE-OWNED layout: the workspace's stored panels, or — for a
    // layout-less legacy body — whatever layout was already shown (kept, as
    // before; its first save then bakes it in).
    if (layout) {
      this.layout = layout.map((p) => ({
        id: p.id,
        run_id: p.run_id ?? null,
        checkpoint: p.checkpoint ?? null
      }));
    } else {
      // No stored layout ⇒ the tree keys are the panel set (see legacyLayout).
      const derived = legacyLayout(Object.keys(this.trees), this.layout);
      if (derived) this.layout = derived;
    }
    // system_prompt + the panel LAYOUT travel with the workspace (each conv =
    // one experiment). This patch is a full CLAIM — panels + our stamp — in
    // BOTH branches: it re-points the bus at this workspace. The response is
    // adopted so the follow-up #mirror runs against the fresh panel list
    // rather than the previous workspace's.
    const patch: StatePatch = {
      system_prompt: conv.system_prompt ?? null,
      // Explicit derive for flag-less legacy bodies (text present ⇒ enabled), so
      // the bus mirror is always a real bool once a workspace is open.
      system_enabled: conv.system_enabled ?? (conv.system_prompt ?? '').trim().length > 0,
      panels: this.layout.map((p) => ({
        id: p.id,
        run_id: p.run_id ?? null,
        checkpoint: p.checkpoint ?? null
      }))
    };
    const next = await api.setState(this.#ownPatch(patch)).catch(() => null);
    live.adopt(next);
  }

  // ── external-fold hooks (wired in init) ──────────────────────────
  /** Register the bus terminal hooks. `own` (injected by +page, which imports the
   *  chat store) folds a detached chat WE fired from its bus bucket; it returns true
   *  when it handled the event, so an own chat never falls through to the foreign
   *  reconcile path (and vice-versa). Idempotent. */
  init(own?: {
    done: (panel: Panel, data: any) => boolean;
    error: (panel: Panel, data: any) => boolean;
  }): void {
    live.onChatDone = (panel, data) => {
      if (own?.done(panel, data)) return; // our chat: blobs seeded from the manifest
      // FOREIGN chat (CLI / another tab): its fold arrived as an ops event —
      // nothing to reconcile anymore (the echo-fold path retired with P3).
      // What survives is render hygiene: a chat for a DIFFERENT workspace
      // completing on a panel id this workspace reuses must not linger as a
      // bucket overlay on our column.
      if (data?.workspace_id != null && data.workspace_id !== this.activeId) live.dropBucket(panel);
    };
    live.onChatError = (panel, data) => {
      if (own?.error(panel, data)) return; // our chat: token released, bucket shows the error
      if (data?.client_token) this.endToken(data.client_token);
    };
    // Layout lockstep: a state stamped as OURS drives the store-owned layout
    // (CLI `tinkpg open`, another tab on the same workspace). Strictly stamped —
    // see live.onOwnPanels' docstring for why unstamped states are excluded.
    live.onOwnPanels = (panels) =>
      this.#adoptLayout(
        (panels ?? []).map((p) => ({
          id: p.id,
          run_id: p.run_id ?? null,
          checkpoint: p.checkpoint ?? null
        }))
      );
    // Bus re-prime after a server restart claims with OUR true layout, not the
    // mirror's panels (state.svelte.ts #reprime).
    live.reprimeClaim = () => this.#reprimeClaim();
    // The ops mirror: every accepted batch echoes here in rev order.
    live.onOps = (ev) => this.applyOpsEvent(ev as OpsEvent);
    // Server-side deletes broadcast now — the client half of the review's
    // "editing into the void" fix.
    live.onWorkspaceDeleted = (id) => this.onWorkspaceDeleted(id);
  }

  /** Ordering backstop for server-authored folds: a terminal's fold_rev above
   *  our local rev means the fold's ops event never applied here (the contract
   *  broadcasts it FIRST, so this is a missed/raced event) — refetch. */
  ensureRev(rev: number): void {
    if (this.activeId && rev > this.#rev) this.#refetchBody();
  }

  /** A `workspace_deleted` broadcast: some other client (another tab, the CLI,
   *  a pack replace) deleted a workspace. Drop it from the list; if it is OUR
   *  open one, freeze the tab LOUDLY (content stays on screen, nothing
   *  persists) rather than yanking the view — the minimal honest UX. Recovery:
   *  any later event for the id (a re-create) triggers the loadFailed refetch
   *  path, and a manual switch always works. */
  onWorkspaceDeleted(id: string | null | undefined): void {
    if (!id || id === this.#selfDeleting || id === this.#draftId) return;
    this.#seenRevs.delete(id);
    this.list = this.list.filter((c) => c.id !== id);
    if (id !== this.activeId) return;
    this.#loadFailed = true;
    this.#flashNotice(
      'This workspace was DELETED elsewhere — the view is frozen and changes are NOT being saved. Switch workspaces to continue.'
    );
  }

  /** On a bus RECONNECT (a fresh snapshot after an EventSource drop): un-latch
   *  busy and ask the summaries whether the store moved without us. The
   *  echo-reconcile that used to run here retired with P3 — a terminal missed
   *  during the gap is recovered by the RE V compare below (the fold is an ops
   *  batch; a moved rev refetches the body), not by grafting from a transcript
   *  mirror that no longer exists. */
  reconcileOnReconnect(): void {
    if (!this.activeId) return;
    // Un-latch busy: server `running` is the in-flight COUNTER — 0 means every chat
    // fired its terminal, so any token still held is one we missed. (TODO: when the
    // server IS still running some OTHER chat, a token whose own terminal we missed
    // in the gap stays latched until that other chat ends — snapshot's global bool
    // can't disambiguate per-token; would need per-panel running in the state.)
    if (live.state?.running === false && this.#ownTokens.size) {
      this.#ownTokens.clear();
      this.#busy = false;
    }
    // Rev compare (ops protocol): `ops` events missed during the gap can't be
    // replayed (SSE has no backlog), so ask the summaries — one small GET — and
    // refetch the body when the store moved without us.
    void this.#reconnectRevCheck();
  }

  async #reconnectRevCheck(): Promise<void> {
    const id = this.activeId;
    if (!id || id === this.#draftId) return;
    try {
      const summaries = await api.listWorkspaces();
      this.list = summaries.length ? summaries : this.list;
      const mine = summaries.find((s) => s.id === id);
      if (mine && this.activeId === id && (mine.rev ?? 0) !== this.#rev) this.#refetchBody();
    } catch {
      /* reconnect probe only — the next event's gap check catches what this missed */
    }
  }

  // ── the mirror (ops protocol — HANDOFF_SERVER_AUTHORITY §4.2) ─────
  /** Apply one bus `ops` event. Ordering rules, each load-bearing:
   *  1. the WORKSPACE filter runs before any rev logic (gap-checking a foreign
   *     event would trigger spurious refetches — or advance our rev past a
   *     genuine event and leave the mirror permanently wrong);
   *  2. rev ≤ local drops (dup / our own optimistic state already ahead);
   *  3. rev == local+1 ALWAYS applies — own echoes included (skip-own provably
   *     breaks LWW convergence, §4.2) — through the same interpreters the
   *     static transport and fixture vectors use;
   *  4. anything else (a gap, or a rev that went BACKWARDS — e.g. a workspace
   *     replaced by a pack apply) refetches the light body.
   *  Application never re-emits (an echo must not echo) and never marks meta —
   *  it writes the reactive state directly. */
  applyOpsEvent(ev: OpsEvent | null | undefined): void {
    if (!ev || typeof ev.rev !== 'number' || typeof ev.workspace !== 'string') return;
    // Track the highest rev seen per workspace BEFORE any filtering — the
    // adoption paths (#loadTrees / a finishing refetch) use it to catch up on
    // events that were correctly filtered or raced a GET.
    if (ev.rev > (this.#seenRevs.get(ev.workspace) ?? 0)) this.#seenRevs.set(ev.workspace, ev.rev);
    if (ev.workspace !== this.activeId) return;
    if (this.#loadFailed) {
      // A frozen tab getting an event for its workspace = a sign of life
      // (e.g. deleted-then-recreated by a pack): try to recover, don't apply.
      this.#refetchBody();
      return;
    }
    if (!Array.isArray(ev.ops)) return;
    if (ev.rev === this.#rev) return; // dup — already at this state
    if (ev.rev < this.#rev) {
      // BACKWARDS: the store's rev line restarted under us (workspace deleted
      // and re-created — pack replace). The §2d rule: never drop these, the
      // mirror is showing a corpse. A late-queued stale event costs one
      // spurious refetch, which converges.
      this.#refetchBody();
      return;
    }
    if (ev.rev !== this.#rev + 1) {
      this.#refetchBody();
      return;
    }
    try {
      let trees = this.trees;
      for (const op of ev.ops) {
        if (op.op === 'set_meta') this.#applyMetaEvent(op.fields);
        else trees = applyPanelOp(trees, op);
      }
      if (trees !== this.trees) {
        this.trees = trees;
        // A FOREIGN op changed the view (own echoes are same-ref no-ops) — keep
        // the CLI-facing echo current. Never re-emits ops, so no loop.
        this.#mirror();
      }
      this.#rev = ev.rev;
    } catch (e) {
      // An echo the interpreter rejects = the mirror diverged from the store.
      this.#refetchBody();
      console.warn('ops event application failed — refetching', e);
    }
  }

  /** set_meta echo → store fields. `panels` goes through the SAME guarded
   *  adoption as a bus layout claim (content-compare + claims-in-flight window),
   *  so an own stale echo can't briefly revert a rapid second edit. The two
   *  system_* fields are deliberately NOT applied here: the bus patch machinery
   *  (mergeBusState + the +page flush) already owns their live propagation. */
  #applyMetaEvent(fields: SetMetaFields): void {
    if (fields.name != null && this.activeId) {
      const id = this.activeId;
      this.list = this.list.map((c) => (c.id === id ? { ...c, name: fields.name! } : c));
    }
    if (Array.isArray(fields.panels)) {
      this.#adoptLayout(
        fields.panels.map((p) => ({
          id: p.id,
          run_id: p.run_id ?? null,
          checkpoint: p.checkpoint ?? null
        }))
      );
    }
    // Value-compare before assigning the Sets: an own echo carries what we
    // already hold, and a fresh Set ref for equal content re-renders every
    // reader (same class of churn the treeEq guard kills for replace echoes).
    // And while OUR OWN meta write is on the wire, skip the two LWW Set fields
    // entirely: its echo is a snapshot from BEFORE any toggle made during the
    // RTT, and applying it would revert that toggle on screen — then persist
    // the reversion when the toggle's own debounced #fields() fires.
    const setEq = (s: Set<string>, arr: string[]) =>
      s.size === arr.length && arr.every((x) => s.has(x));
    if (this.#metaClaimsInFlight === 0) {
      if (Array.isArray(fields.reduced_panels) && !setEq(this.reducedPanels, fields.reduced_panels))
        this.reducedPanels = new Set(fields.reduced_panels);
      if (Array.isArray(fields.send_targets) && !setEq(this.sendTargets, fields.send_targets))
        this.sendTargets = new Set(fields.send_targets);
    }
    if (Array.isArray(fields.seen_panels))
      for (const p of fields.seen_panels) this.#seenPanels.add(p);
    if (typeof fields.panel_seq === 'number')
      this.#panelSeq = Math.max(this.#panelSeq, fields.panel_seq);
  }

  #flashNotice(msg: string): void {
    this.externalNotice = msg;
    if (this.#noticeTimer) clearTimeout(this.#noticeTimer);
    this.#noticeTimer = setTimeout(() => (this.externalNotice = null), 7000);
  }
}

export const workspaces = new ConversationsStore();
