// The conversation/branch-tree store — the frontend owner of the per-panel
// branch trees and their persistence. See docs/BRANCHING_DESIGN.md §6 and
// docs/STORAGE_V2.md §2.5.
//
// Division of responsibility: THIS store owns the reactive `trees`, the
// summaries `list`/`activeId`, persistence (debounced, dirty-panel granular),
// the chat-ownership token set, and the external-fold reconciliation wired off
// the live bus. The TREE OPERATIONS (append/fold/regen/edit/delete/cycle) live
// in branch-ops/+page, which read `treeFor(panel)`, compute a new tree via
// lib/tree.ts, and commit it with `setTree(panel, …)` — the single entry that
// mirrors the active path into PlaygroundState.messages AND debounce-saves.
//
// Storage v2 memory policy (docs/STORAGE_V2.md):
//   - `list` holds SUMMARIES only; a conversation's light body is fetched on
//     open (GET /api/conversations/{id}) and the previous one's trees + node-blob
//     cache are dropped on switch.
//   - `trees` is $state.raw: every mutation is a wholesale per-panel ref
//     replacement (tree.ts ops are immutable), so deep proxies were pure
//     overhead — and $state.snapshot on save (which deep-copied a possibly-huge
//     map) is gone entirely; refs are stringified directly.
//   - Saves accumulate DIRT (dirty panels / dropped panels / layout flag) and
//     ship either a partial-upsert PUT (tree bytes for dirty panels only) or a
//     layout-only PATCH (zero tree bytes — model swap / fold / send-target
//     toggles never serialize a tree again). See lib/save-plan.ts.

import { live } from './state.svelte';
import { api } from './api';
import { nodeBlobs } from './node-blobs.svelte';
import { planSave, heavyNodeIds, lightenTree } from './save-plan';
import {
	emptyTree,
	activeMessages,
	reconcileExternal,
	type ConvTree,
	type Msg
} from './tree';
import type {
	Conversation,
	ConversationSummary,
	Panel,
	PanelLayout,
	StatePatch
} from './types';

function asTree(x: unknown): ConvTree {
	const t = x as ConvTree | undefined | null;
	return t && t.nodes && Array.isArray(t.rootChildren) ? t : emptyTree();
}

function msgsEqual(a: Msg[], b: Msg[]): boolean {
	if (a.length !== b.length) return false;
	for (let i = 0; i < a.length; i++) {
		if (a[i].role !== b[i].role || a[i].content !== b[i].content) return false;
	}
	return true;
}

function newest(list: ConversationSummary[]): ConversationSummary | undefined {
	return [...list].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))[0];
}

class ConversationsStore {
	/** Conversation summaries (no trees) — the sidebar list. */
	list = $state<ConversationSummary[]>([]);
	activeId = $state<string | null>(null);
	/** Per-panel branch trees keyed by stable panel id ('primary' always present).
	 *  THE read source: +page reads treeFor(panel), computes a new tree via tree.ts,
	 *  commits with setTree(panel,…). N-panel: any number of keys.
	 *  $state.raw — plain immutable objects, replaced wholesale per commit; never
	 *  mutate a tree/node in place (nothing would react, nothing would save). */
	trees = $state.raw<Record<string, ConvTree>>({ primary: emptyTree() });
	/** Transient hint shown when the terminal/another tab branched the conversation. */
	externalNotice = $state<string | null>(null);

	/** Hook (assigned by +page, which owns patchState): flush the pending debounced
	 *  /api/state patch — assigning its response into live.state — BEFORE any
	 *  conversation transition. Without this barrier a half-typed system prompt's
	 *  200ms timer fires AFTER the new conversation loads and silently leaks onto
	 *  it (persisted!), while the old conversation loses the edit — see
	 *  tests/small-smokes/browser_sysprompt_switch.py. Every transition goes
	 *  through #preSwitch so a future switch path gets the barrier for free. */
	flushStatePatch: (() => Promise<void>) | null = null;

	/** The one pre-transition barrier: settle the page's pending state patch so
	 *  live.state is current before we persist against it or swap away from it. */
	async #preSwitch(): Promise<void> {
		await this.flushStatePatch?.();
	}

	// ── per-conversation panel UI (persisted with the conversation) ───────
	/** Panels folded out of view (tree kept alive). */
	reducedPanels = $state<Set<string>>(new Set());
	/** Panels the composer fires a send to (the "Send to" chips). */
	sendTargets = $state<Set<string>>(new Set());
	/** Defaulting bookkeeping: a panel is auto-added to `sendTargets` exactly once,
	 *  the first time syncPanels() sees it (unless it's reduced). Persisted too so a
	 *  restart restores the EXACT deselected/folded state rather than re-defaulting
	 *  every panel ON. Not rendered ⇒ plain (non-reactive) field. */
	#seenPanels = new Set<string>();

	/** Tokens of chats THIS browser fired — the chat store folds these from their
	 *  bus bucket on chat_done (routed before the foreign path), so the external-fold
	 *  reconcile below skips them. Kept in lockstep with #busy for switch-gating. */
	#ownTokens = new Set<string>();
	/** Reactive mirror of (#ownTokens.size > 0). A plain Set isn't tracked by Svelte 5,
	 *  so `busy` reading the Set directly never re-fires the `disabled={…convo.busy}`
	 *  bindings when a token is removed — the New/regen/edit buttons would latch
	 *  disabled after a generation. Keep this in lockstep with every Set mutation. */
	#busy = $state(false);

	// ── save dirt (capture-at-mark; flush-on-switch) ──────────────────
	/** Dirty panels: id → the tree ref as committed (refs are immutable, so this
	 *  IS the capture — no copy). Merged across marks; drained per save. */
	#dirtyTrees = new Map<string, ConvTree>();
	/** Panels dropped since the last save (server deletes their stored trees). */
	#droppedTrees = new Set<string>();
	/** Conversation-level (non-tree) change pending: panel layout / send-targets /
	 *  folds / system prompt / seen bookkeeping. Ships as a PATCH when no tree
	 *  dirt rides along. */
	#layoutDirty = false;
	/** The conversation the accumulated dirt belongs to. Flush-on-switch keeps it
	 *  equal to activeId whenever dirt exists. */
	#pendingId: string | null = null;
	#saveTimer: ReturnType<typeof setTimeout> | null = null;
	/** Serializes #doSave runs (a materializing CREATE must not race a follow-up
	 *  partial PUT of the same conversation). */
	#saveChain: Promise<void> = Promise.resolve();
	/** Guards a mid-session body-fetch failure (see remove()): the store shows an
	 *  empty conversation it could not load — marking dirt then would PUT that
	 *  emptiness over the stored data, so saves latch off until a successful load. */
	#loadFailed = false;
	/** Set when the open conversation was loaded through the LEGACY {tree,
	 *  compare_tree} read-shim (pre-multipanel storage; the v2 migration preserves
	 *  that shape). Its first structural save must ship the FULL trees map: the
	 *  server's self-heal drops the legacy keys and keeps only the `trees` sent,
	 *  so a partial upsert would silently lose the un-sent panel. Cleared once a
	 *  tree save lands (the conversation is a normal `trees` conv from then on). */
	#fullTreeSaveNeeded = false;
	/** Supersedes an in-flight switchTo body fetch when a newer switch starts. */
	#switchSeq = 0;
	#noticeTimer: ReturnType<typeof setTimeout> | null = null;
	/** Id of the current UNSAVED draft (a new conversation that exists only in `list`,
	 *  not yet on the backend). It is materialized on the first save (= first real
	 *  change), and discarded if abandoned untouched. null ⇒ no pending draft. */
	#draftId: string | null = null;

	treeFor(panel: Panel): ConvTree {
		return this.trees[panel] ?? emptyTree();
	}

	/** True while ANY own chat is in flight — its fold (from the response stream)
	 *  outlives the bucket `running` flag (which clears on the bus chat_done). Gate
	 *  conversation switch/create/delete on this so an in-flight fold can't land on
	 *  (and be dropped by) a freshly-swapped conversation tree. */
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

	// ── the single commit entry ──────────────────────────────────────
	/** Commit a new tree for a panel: update reactive state, mirror the active
	 *  path into PlaygroundState.messages (so the CLI/sampler see it), and
	 *  schedule a debounced save of THIS panel. */
	setTree(panel: Panel, next: ConvTree, persist = true): void {
		this.trees = { ...this.trees, [panel]: next };
		this.#mirror();
		if (persist) this.#markTree(panel);
	}

	#mirror(): void {
		// Echo each LIVE panel's active path into PlaygroundState (one patch, messages
		// only — never clobbers per-panel run_id/checkpoint). Restricted to panels in the
		// live list: a tree that outlived its panel (a removed/replaced panel whose tree
		// lingers in `this.trees`) must NOT echo, or it would re-register the panel
		// server-side as a run_id=null phantom on every send. (The backend also refuses to
		// auto-create from a message patch now — this is the matching client-side guard,
		// and it also avoids the wasted POST.) Fall back to echoing all when the live list
		// is empty (not loaded yet) so initial bootstrap still mirrors.
		const liveIds = new Set((live.state?.panels ?? []).map((p) => p.id));
		const panel_messages: Record<string, Msg[]> = {};
		for (const [pid, tree] of Object.entries(this.trees)) {
			if (liveIds.size && !liveIds.has(pid)) continue;
			panel_messages[pid] = activeMessages(tree);
		}
		api.setState({ panel_messages }).catch(() => {});
	}

	/** Duplicate one panel's tree into another (used when a new compare panel should
	 *  start from an existing thread). structuredClone, NOT $state.snapshot: trees
	 *  are $state.raw (plain objects), so snapshot would return the SAME ref and
	 *  alias two panels. Light trees make the clone cheap. Does NOT clear other
	 *  panels' live buckets, so adding a panel can't wipe an in-flight stream. */
	duplicateTo(srcPanel: Panel, dstPanel: Panel): void {
		this.trees = { ...this.trees, [dstPanel]: structuredClone(this.treeFor(srcPanel)) };
		this.#mirror();
		this.#markTree(dstPanel);
	}

	/** Seed a panel with an EMPTY thread (Shift+add panel = blank, vs duplicateTo's
	 *  clone-the-first-panel default). Mirrors + persists like duplicateTo. */
	freshTree(panel: Panel): void {
		this.trees = { ...this.trees, [panel]: emptyTree() };
		this.#mirror();
		this.#markTree(panel);
	}

	/** Drop a panel's tree (on panel removal). The LAST tree is never dropped —
	 *  a conversation always keeps at least one panel (any id). */
	dropTree(panel: Panel): void {
		if (!(panel in this.trees) || Object.keys(this.trees).length <= 1) return;
		const next = { ...this.trees };
		delete next[panel];
		this.trees = next;
		this.#mirror();
		this.#markDropped(panel);
	}

	// ── panel UI (folded / send-targets), persisted with the conversation ──
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
	/** Forget a removed panel's UI bookkeeping (called from +page removePanel). */
	dropPanelUi(panel: Panel): void {
		this.reducedPanels = new Set([...this.reducedPanels].filter((t) => t !== panel));
		this.sendTargets = new Set([...this.sendTargets].filter((t) => t !== panel));
		this.#seenPanels.delete(panel);
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
	/** Restore the panel-UI sets from a loaded conversation. Missing keys (legacy
	 *  conversations) ⇒ empty sets + empty seen ⇒ syncPanels defaults every open
	 *  panel ON, exactly as before this was persisted. */
	#applyPanelUi(conv: Conversation): void {
		this.reducedPanels = new Set(conv.reduced_panels ?? []);
		this.sendTargets = new Set(conv.send_targets ?? []);
		this.#seenPanels = new Set(conv.seen_panels ?? []);
	}

	/** The panel layout (model selection per panel) currently shown — what a new
	 *  conversation inherits. Always at least a blank primary. */
	#currentLayout(): PanelLayout[] {
		const layout = (live.state?.panels ?? []).map((p) => ({
			id: p.id,
			run_id: p.run_id,
			checkpoint: p.checkpoint
		}));
		return layout.length ? layout : [{ id: 'primary', run_id: null, checkpoint: null }];
	}

	/** Reset every open panel's tree to empty (fresh thread, same panel layout).
	 *  `mark` schedules the emptiness for persistence (dirty new ids + dropped
	 *  stale ids); pass false ONLY for an unsaved draft, which must stay unsaved. */
	#freshTrees(mark: boolean): Promise<void> {
		const ids = (live.state?.panels ?? []).map((p) => p.id);
		if (!ids.length) ids.push('primary'); // no panels known yet → the default first slot
		const prev = Object.keys(this.trees);
		this.trees = Object.fromEntries(ids.map((id) => [id, emptyTree()]));
		if (mark) {
			for (const id of ids) this.#markTree(id);
			for (const p of prev) if (!ids.includes(p)) this.#markDropped(p);
		}
		return api
			.setState({ panel_messages: Object.fromEntries(ids.map((id) => [id, []])) })
			.then(() => {})
			.catch(() => {});
	}

	// ── persistence (dirty-panel granular; flush-on-switch) ───────────
	/** Public save = a conversation-LEVEL (non-tree) change: panel layout, model
	 *  selection, send-targets, folds, system prompt, seen bookkeeping. Tree dirt
	 *  is marked by setTree/duplicateTo/freshTree/dropTree themselves. */
	save(): void {
		this.#markLayout();
	}

	#markTree(panel: Panel): void {
		if (!this.#beginDirt()) return;
		this.#droppedTrees.delete(panel);
		this.#dirtyTrees.set(panel, this.treeFor(panel));
		this.#schedule();
	}
	#markDropped(panel: Panel): void {
		if (!this.#beginDirt()) return;
		this.#dirtyTrees.delete(panel);
		this.#droppedTrees.add(panel);
		this.#schedule();
	}
	#markLayout(): void {
		if (!this.#beginDirt()) return;
		this.#layoutDirty = true;
		this.#schedule();
	}
	/** Common mark preamble: bind the dirt to the active conversation. Returns
	 *  false when there is nothing to bind to (no active conversation) or the
	 *  store is in the failed-load latch (saving would clobber stored data). */
	#beginDirt(): boolean {
		const id = this.activeId;
		if (!id) return false;
		if (this.#loadFailed) {
			console.warn('conversation failed to load — change NOT scheduled for save');
			return false;
		}
		if (this.#pendingId && this.#pendingId !== id) {
			// Flush-on-switch makes this unreachable. If it ever happens, DROP the
			// stale dirt loudly: tree refs from another conversation saved under this
			// id would corrupt it — losing a 400ms edit window is the lesser harm.
			console.warn('save dirt spans conversations — dropping stale dirt for', this.#pendingId);
			this.#dirtyTrees = new Map();
			this.#droppedTrees = new Set();
			this.#layoutDirty = false;
		}
		this.#pendingId = id;
		return true;
	}
	#hasDirt(): boolean {
		return (
			this.#pendingId !== null &&
			(this.#dirtyTrees.size > 0 || this.#droppedTrees.size > 0 || this.#layoutDirty)
		);
	}
	#schedule(): void {
		if (this.#saveTimer) clearTimeout(this.#saveTimer);
		this.#saveTimer = setTimeout(() => void this.#runSave(), 400);
	}
	/** Enqueue one save pass on the chain (never concurrent with another). */
	#runSave(): Promise<void> {
		this.#saveChain = this.#saveChain.then(() => this.#doSave());
		return this.#saveChain;
	}

	async #doSave(): Promise<void> {
		this.#saveTimer = null;
		if (!this.#hasDirt()) return;
		const id = this.#pendingId!;
		// Drain the dirt into locals — a change landing during the awaits below
		// re-marks cleanly and ships with the NEXT pass.
		const dirtyTrees = this.#dirtyTrees;
		const droppedTrees = this.#droppedTrees;
		const layoutDirty = this.#layoutDirty;
		this.#dirtyTrees = new Map();
		this.#droppedTrees = new Set();
		this.#layoutDirty = false;
		this.#pendingId = null;
		// Conversation-level fields are read at FIRE time: system_prompt + the panel
		// layout mirror server state (which lands a beat after a patchState), and
		// flush-on-switch guarantees live.state still belongs to `id` here.
		const fields = {
			system_prompt: live.state?.system_prompt ?? null,
			panels: (live.state?.panels ?? []).map((ps) => ({
				id: ps.id,
				run_id: ps.run_id,
				checkpoint: ps.checkpoint
			})),
			reduced_panels: [...this.reducedPanels],
			send_targets: [...this.sendTargets],
			seen_panels: [...this.#seenPanels]
		};
		// A pending save IS the first real change to an unsaved draft → materialize it
		// on the backend (create with the draft's id, FULL current trees) instead of a
		// partial save (which would 404). Clear #draftId SYNCHRONOUSLY (before the
		// await) so a racing pass can't double-create; restore it on failure so a
		// retry still creates it.
		const materializing = id === this.#draftId;
		if (materializing) this.#draftId = null;
		try {
			if (materializing) {
				const name = this.list.find((c) => c.id === id)?.name ?? 'Untitled';
				const shipped = { ...this.trees }; // refs at fire time — reused for lightening
				await api.createConversation({ id, name, ...fields, trees: shipped });
				this.#lightenShipped(id, shipped);
			} else {
				// First structural save of a legacy-shape conversation: expand to ALL
				// trees (see #fullTreeSaveNeeded) — refs at fire time, activeId === id
				// here by flush discipline. Layout-only PATCHes don't clear the flag
				// (they don't touch trees, so the legacy keys survive them).
				const expand = this.#fullTreeSaveNeeded && (dirtyTrees.size > 0 || droppedTrees.size > 0);
				const effectiveDirty = expand
					? new Map([...Object.entries(this.trees), ...dirtyTrees])
					: dirtyTrees;
				const plan = planSave({ dirtyTrees: effectiveDirty, droppedTrees, layoutDirty }, fields);
				if (plan.kind === 'put') {
					await api.saveConversationTree(id, plan.body);
					this.#lightenShipped(id, plan.body.trees);
				} else if (plan.kind === 'patch') await api.patchConversation(id, plan.body);
				if (expand) this.#fullTreeSaveNeeded = false;
			}
			this.list = this.list.map((c) =>
				c.id === id ? { ...c, updated_at: new Date().toISOString() } : c
			);
		} catch (e) {
			if (materializing) this.#draftId = id; // not persisted — still a draft
			// Re-merge the drained dirt (unless a newer mark superseded it) so the next
			// save / flush-on-switch retries — a silently-lost PARTIAL save would never
			// be re-shipped by later unrelated edits, unlike the old whole-map save.
			for (const [p, t] of dirtyTrees)
				if (!this.#dirtyTrees.has(p) && !this.#droppedTrees.has(p)) this.#dirtyTrees.set(p, t);
			for (const p of droppedTrees) if (!this.#dirtyTrees.has(p)) this.#droppedTrees.add(p);
			if (layoutDirty) this.#layoutDirty = true;
			if (!this.#pendingId) this.#pendingId = id;
			console.warn('conversation save failed', e);
		}
	}

	/** Post-save lightening (storage v2): the heavy fields that just shipped are
	 *  now server-side write-once blobs, so their inline copies are pure re-upload
	 *  weight — without this, every later save of the same panel re-serializes
	 *  the whole session's logprobs (megabytes per n=30 round) on the main thread.
	 *  Seeds the blob cache from the shipped payloads FIRST (the token view keeps
	 *  working instantly, zero fetches), then strips exactly the shipped node ids
	 *  from the CURRENT trees. One batched assignment; NO #mirror (active-path
	 *  role/content unchanged) and NO dirt marks (a lighten must never schedule a
	 *  save, or save→lighten→save would loop). Runs synchronously after the await
	 *  so it lands before flush()-gated transitions proceed. Nodes that gained
	 *  heavies DURING the await (a mid-save fold) have new ids ∉ shipped —
	 *  untouched, they ship with their own pass. On save FAILURE this never runs:
	 *  the re-merged dirt re-ships the heavies, which is the data-safety path. */
	#lightenShipped(id: string, shipped: Record<string, ConvTree>): void {
		if (this.activeId !== id) return; // conversation swapped mid-save — these trees are gone
		let next: Record<string, ConvTree> | null = null;
		for (const [panel, shippedTree] of Object.entries(shipped)) {
			const cur = this.trees[panel];
			if (!cur) continue; // panel dropped during the await
			const ids = heavyNodeIds(shippedTree);
			if (!ids.size) continue;
			for (const nid of ids) {
				const n = shippedTree.nodes[nid];
				nodeBlobs.seed(nid, { token_logprobs: n.token_logprobs, raw_meta: n.raw_meta });
			}
			const lightened = lightenTree(cur, ids);
			if (lightened) (next ??= { ...this.trees })[panel] = lightened;
		}
		if (next) this.trees = next;
	}

	async flush(): Promise<void> {
		if (this.#saveTimer) {
			clearTimeout(this.#saveTimer);
			this.#saveTimer = null;
		}
		// Run pending dirt now, and in all cases let any in-flight pass settle —
		// a transition must never overlap a save still on the wire.
		if (this.#hasDirt()) await this.#runSave();
		else await this.#saveChain;
	}

	// ── load / switch / create / rename / remove ─────────────────────
	/** Load the conversation SUMMARIES and open the active one (fetching its body).
	 *  If `preferredId` is given (e.g. from the `?c=` URL param) and matches, open
	 *  it; otherwise open the newest. Returns whether `preferredId` was honored
	 *  (false ⇒ absent or unknown, so the caller can normalize the URL / notify). */
	async load(preferredId?: string | null): Promise<boolean> {
		await this.#preSwitch();
		const list = await api.listConversations();
		this.list = list;
		if (!list.length) {
			// Nothing saved yet → open an unsaved draft (an empty conversation is never
			// persisted until it changes). create() sets it active + lays out panels.
			await this.create('Untitled', this.#currentLayout());
			return false;
		}
		const preferred = preferredId ? list.find((c) => c.id === preferredId) : undefined;
		const active = preferred ?? newest(list)!;
		const conv = await api.getConversation(active.id); // throws → +page's load banner
		nodeBlobs.reset(active.id);
		this.activeId = active.id;
		await this.#loadTrees(conv);
		this.#afterLoad();
		return !!preferred;
	}

	async switchTo(id: string): Promise<void> {
		if (id === this.activeId) return;
		// Settle the pending state patch FIRST: flush() below reads live.state
		// (system_prompt, panel layout) when persisting the conversation we're leaving.
		await this.#preSwitch();
		// If we're leaving an untouched draft, drop it (flush below materializes it first
		// if it had any pending change, clearing #draftId so the discard then no-ops).
		const leavingDraft = this.#draftId !== null && this.#draftId === this.activeId;
		await this.flush();
		if (leavingDraft) this.#discardDraftIfUntouched();
		if (!this.list.find((c) => c.id === id)) return;
		// Fetch the body BEFORE committing any transition state: on failure we stay
		// fully on the current conversation (nothing half-switched to mis-save
		// against), on supersession (a newer switch) we just stand down.
		const seq = ++this.#switchSeq;
		let conv: Conversation;
		try {
			conv = await api.getConversation(id);
		} catch (e: any) {
			this.#flashNotice(`Failed to open conversation: ${e?.message ?? e}`);
			return;
		}
		if (seq !== this.#switchSeq) return;
		// Edits made to the OLD conversation while the body was in flight: flush them
		// now, while activeId/live.state still belong to it.
		if (this.#hasDirt()) await this.flush();
		if (seq !== this.#switchSeq) return;
		live.clearBuckets();
		nodeBlobs.reset(id);
		this.activeId = id;
		await this.#loadTrees(conv);
		this.#afterLoad();
	}

	/** Create + switch to a new conversation. `panels` is the layout it opens with:
	 *  callers inherit the current conversation's models (a new conversation keeps the
	 *  MODELS, never the messages) or pass a single blank panel (Shift+New). Omitted ⇒
	 *  inherit the current layout.
	 *
	 *  The new conversation is an UNSAVED DRAFT — it lives only in `list` and is NOT
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
		// laying out a fresh draft would itself materialize an empty conversation.
		// The id may be MINTED BY THE CALLER so it can push ?c= BEFORE create — the
		// trailing `await api.setState` below yields to the reactive scheduler while
		// activeId is newly-set, and the ?c= sync effect would switch right back to
		// the old conversation if the URL still pointed there (an id not yet in `list`
		// is ignored by that effect, so a caller-set URL is safe). See newConversation.
		const draft: ConversationSummary = {
			id: id ?? crypto.randomUUID(),
			name,
			panels: layout,
			created_at: now,
			updated_at: now
		};
		this.list = [draft, ...this.list];
		this.activeId = draft.id;
		this.#draftId = draft.id;
		this.#loadFailed = false;
		this.#fullTreeSaveNeeded = false; // a draft is never legacy-shaped
		nodeBlobs.reset(draft.id);
		this.reducedPanels = new Set();
		this.sendTargets = new Set(ids);
		this.#seenPanels = new Set(ids);
		// Lay out the inherited/blank panels with EMPTY trees + transcripts. One
		// optimistic setState (panels + cleared echoes) so live.state reflects the new
		// layout immediately — the SSE patch lags a beat behind the POST.
		this.trees = Object.fromEntries(ids.map((id) => [id, emptyTree()]));
		const next = await api
			.setState({
				panels: layout.map((p) => ({ id: p.id, run_id: p.run_id, checkpoint: p.checkpoint, messages: [] })),
				panel_messages: Object.fromEntries(ids.map((id) => [id, []]))
			})
			.catch(() => null);
		if (next) live.state = next;
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
			this.#markLayout();
			return;
		}
		const updated = await api.patchConversation(id, { name });
		this.list = this.list.map((c) =>
			c.id === id ? { ...c, name: updated.name, updated_at: updated.updated_at } : c
		);
	}

	async remove(id: string): Promise<void> {
		await this.#preSwitch();
		await this.flush();
		const removingDraft = id === this.#draftId;
		// Never leave zero conversations: the last one resets in place (same id,
		// empty trees, default name).
		if (this.list.length <= 1) {
			live.clearBuckets();
			nodeBlobs.reset(id);
			// A draft must STAY unsaved through the reset — don't mark its emptied
			// trees for persistence (that would materialize an empty conversation).
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
		else await api.deleteConversation(id);
		this.list = this.list.filter((c) => c.id !== id);
		if (this.activeId === id) {
			live.clearBuckets();
			const next = newest(this.list)!;
			nodeBlobs.reset(next.id);
			this.activeId = next.id;
			let conv: Conversation | null = null;
			try {
				conv = await api.getConversation(next.id);
			} catch (e: any) {
				// Deleted the open conversation but couldn't load the next: latch saves
				// off (an empty PUT would clobber the stored data) and say so.
				this.trees = { primary: emptyTree() };
				this.#loadFailed = true;
				this.#flashNotice(
					`Failed to load the next conversation (${e?.message ?? e}) — changes are NOT being saved; reload the page.`
				);
				return;
			}
			await this.#loadTrees(conv);
			this.#afterLoad();
		}
	}

	/** Reset every open panel's tree for a fresh thread under the SAME conversation. */
	async resetActive(): Promise<void> {
		await this.#preSwitch();
		live.clearBuckets();
		await this.#freshTrees(true);
		this.save();
	}

	/** Migration read-shim: prefer the new {trees} shape; fall back to the legacy
	 *  {tree, compare_tree} (synthesizing reserved 'primary'/'compare' ids) so an
	 *  un-migrated saved conversation loads without losing a user-authored compare
	 *  tree. asTree() returns emptyTree() on malformed input. */
	async #loadTrees(conv: Conversation): Promise<void> {
		this.#loadFailed = false; // a body arrived — saves are safe again
		// The cleaned layout we'll restore: drop every panel with no model
		// (run_id == null). Such a panel can't sample anything — it's the inert "phantom"
		// the resurrection bug used to mint, and earlier sessions baked some into saved
		// layouts. Dropping them on load self-heals those conversations (no per-conv manual
		// delete). If EVERY panel is blank, keep the first one (a single blank panel is
		// the empty-thread state — a conversation never opens with zero panels).
		// Legacy convs (no stored layout) ⇒ null ⇒ keep whatever panels are shown.
		let layout =
			Array.isArray(conv.panels) && conv.panels.length
				? conv.panels.filter((p) => p.run_id != null)
				: null;
		if (layout && !layout.length) layout = [conv.panels![0]];
		const keep = layout ? new Set(layout.map((p) => p.id)) : null;

		if (conv.trees && typeof conv.trees === 'object') {
			this.#fullTreeSaveNeeded = false;
			const map: Record<string, ConvTree> = {};
			for (const [pid, t] of Object.entries(conv.trees)) map[pid] = asTree(t);
			// Drop trees for panels not in the cleaned layout: a save can capture a tree
			// for a since-removed (or now-dropped phantom) panel, and a lingering orphan
			// tree is exactly what re-fed the phantom on every send.
			if (keep) for (const pid of Object.keys(map)) if (!keep.has(pid)) delete map[pid];
			// A conversation always loads with ≥1 tree (blank first slot = empty thread).
			if (!Object.keys(map).length) map[layout?.[0]?.id ?? 'primary'] = emptyTree();
			this.trees = map;
		} else {
			// Legacy shape → the first structural save must ship the full map (partial
			// upsert + the server's legacy-key self-heal would drop the other panel).
			this.#fullTreeSaveNeeded = true;
			const map: Record<string, ConvTree> = { primary: asTree(conv.tree) };
			if (conv.compare_tree) map.compare = asTree(conv.compare_tree);
			this.trees = map;
		}
		this.#applyPanelUi(conv);
		// system_prompt + the panel LAYOUT travel with the conversation (each conv =
		// one experiment). Optimistically assign the returned state so the immediately-
		// following #afterLoad mirrors against the FRESH panel list rather than the
		// previous conversation's.
		const patch: StatePatch = { system_prompt: conv.system_prompt ?? null };
		if (layout) {
			patch.panels = layout.map((p) => ({
				id: p.id,
				run_id: p.run_id ?? null,
				checkpoint: p.checkpoint ?? null,
				messages: []
			}));
		} else {
			// Layout-less conversation (legacy {tree,compare_tree} / bare API create):
			// the shown panels are KEPT, but their transcript echoes belong to the
			// PREVIOUS conversation — clear them in this same patch, exactly like the
			// layout branch's `messages: []` does. Without this, #afterLoad (whose
			// contract is "echoes are cleared before it runs") reconciles the foreign
			// echoes into this conversation's trees, and the graft persists on the
			// next save.
			patch.panel_messages = Object.fromEntries(
				(live.state?.panels ?? []).map((p) => [p.id, []])
			);
		}
		const next = await api.setState(patch).catch(() => null);
		if (next) live.state = next;
	}

	/** After loading a conversation: fold a stray external turn into each panel (once,
	 *  if not running), then UNCONDITIONALLY mirror the active paths so a fresh/
	 *  restarted backend still learns the loaded conversation. */
	#afterLoad(): void {
		// NB: unlike #onExternalDone, this is NOT origin-scoped by conversation_id — the
		// panel `messages` echoes it reads carry no origin stamp, and #loadTrees clears
		// every echo to [] synchronously before this runs (no await between), so the loop
		// is structurally dormant at load and can't graft a foreign turn. See
		// docs/BRANCHING_DESIGN.md §3b for why the two fold paths are scoped differently.
		if (!live.anyRunning) {
			for (const ps of live.state?.panels ?? []) {
				const echo = (ps.messages ?? []) as Msg[];
				const cur = this.trees[ps.id];
				if (cur && echo.length && !msgsEqual(echo, activeMessages(cur)))
					this.trees = { ...this.trees, [ps.id]: reconcileExternal(cur, echo) };
			}
		}
		this.#mirror();
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
			if (own?.done(panel, data)) return; // our detached chat folded from its bucket
			this.#onExternalDone(panel, data);
		};
		live.onChatError = (panel, data) => {
			if (own?.error(panel, data)) return; // our chat: token released, bucket shows the error
			if (data?.client_token) this.endToken(data.client_token);
		};
	}

	#onExternalDone(panel: Panel, data: { client_token?: string | null; conversation_id?: string | null }): void {
		// Own chats fold from their bus bucket (routed to the chat store before this) —
		// skip here too as defense in case an own terminal ever reaches this path.
		if (data?.client_token && this.#ownTokens.has(data.client_token)) return;
		// Conversation-scoped fold. Panel ids ('compare', 'p-2'…) are re-minted across
		// conversations and PlaygroundState is a process-wide singleton (shared by every
		// tab + the CLI), so a chat_done stamped with a DIFFERENT conversation than the
		// one open must NOT graft onto a freshly-reused panel id (the "new panel loads a
		// weird conversation" bug). A null stamp (CLI/legacy that never set
		// conversation_id) folds — conservative, keeps the live-drive lockstep alive.
		if (data?.conversation_id != null && data.conversation_id !== this.activeId) {
			// Foreign chat completed on a panel id this conversation now reuses: don't
			// fold it, and drop its live bucket so the foreign stream doesn't linger as a
			// render overlay on our panel.
			live.dropBucket(panel);
			return;
		}
		const ps = (live.state?.panels ?? []).find((p) => p.id === panel);
		const msgs = ps?.messages as Msg[] | undefined;
		if (!msgs || !msgs.length) return;
		const cur = this.treeFor(panel);
		const next = reconcileExternal(cur, msgs);
		if (next === cur) return; // idempotent — already represented + selected
		// Only a genuinely NEW root branch (divergent reset) hides the prior thread;
		// an in-place extend / re-select keeps it visible, so no notice for those.
		const newRoot = next.rootChildren.length > cur.rootChildren.length && cur.rootChildren.length > 0;
		this.setTree(panel, next);
		if (newRoot)
			this.#flashNotice('Terminal started a new conversation — your previous thread is at ‹1/N› on the first message.');
	}

	/** On a bus RECONNECT (a fresh snapshot after an EventSource drop): recover any
	 *  chat terminal we missed during the gap, and un-latch busy if the server has
	 *  nothing in flight. Two failure modes this closes:
	 *   - Reload / drop mid-generation: a detached chat completes server-side while we
	 *     weren't listening; its chat_done never reached us, so its reply sits in the
	 *     echo (`ps.messages`) unfolded. We reconcile each non-running panel's tree
	 *     from the echo (single representative — same recovery a reload gets), idempotent.
	 *   - Busy-latch: an own chat's terminal missed in the gap would leave its token in
	 *     #ownTokens forever (New/switch stuck disabled). If the server reports nothing
	 *     running, every lingering token is from a missed terminal → release them.
	 *  Scoped to OUR open conversation (the echo is a process-wide singleton; a
	 *  CLI/other-tab conversation switch must not graft foreign turns). */
	reconcileOnReconnect(): void {
		if (!this.activeId) return;
		const serverConv = live.state?.conversation_id;
		const sameConv = serverConv == null || serverConv === this.activeId;
		if (sameConv && !live.anyRunning) {
			let changed = false;
			for (const ps of live.state?.panels ?? []) {
				const echo = (ps.messages ?? []) as Msg[];
				const cur = this.trees[ps.id];
				if (cur && echo.length && !msgsEqual(echo, activeMessages(cur))) {
					this.trees = { ...this.trees, [ps.id]: reconcileExternal(cur, echo) };
					changed = true;
				}
			}
			// Only re-mirror when we actually folded something. A blind #mirror here would
			// echo the (still user-only) trees back and OVERWRITE the server's committed
			// turns — right after a reload, where #loadTrees has cleared the local echo,
			// that would destroy the very data a later reconcile needs.
			if (changed) this.#mirror();
		}
		// Un-latch busy: server `running` is the in-flight COUNTER — 0 means every chat
		// fired its terminal, so any token still held is one we missed. (TODO: when the
		// server IS still running some OTHER chat, a token whose own terminal we missed
		// in the gap stays latched until that other chat ends — snapshot's global bool
		// can't disambiguate per-token; would need per-panel running in the state.)
		if (live.state?.running === false && this.#ownTokens.size) {
			this.#ownTokens.clear();
			this.#busy = false;
		}
	}

	#flashNotice(msg: string): void {
		this.externalNotice = msg;
		if (this.#noticeTimer) clearTimeout(this.#noticeTimer);
		this.#noticeTimer = setTimeout(() => (this.externalNotice = null), 7000);
	}
}

export const conversations = new ConversationsStore();
