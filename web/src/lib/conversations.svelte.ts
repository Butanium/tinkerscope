// The conversation/branch-tree store — the frontend owner of the per-panel
// branch trees and their persistence. See docs/BRANCHING_DESIGN.md §6.
//
// Division of responsibility: THIS store owns the reactive `tree`/`compareTree`,
// the named-conversation `list`/`activeId`, persistence (debounced, capture-at-
// schedule), the chat-ownership token set, and the external-fold reconciliation
// wired off the live bus. The TREE OPERATIONS (append/fold/regen/edit/delete/
// cycle) live in +page.svelte, which reads `treeFor(panel)`, computes a new tree
// via lib/tree.ts, and commits it with `setTree(panel, …)` — the single entry
// that mirrors the active path into PlaygroundState.messages AND debounce-saves.

import { live } from './state.svelte';
import { api } from './api';
import {
	emptyTree,
	activeMessages,
	reconcileExternal,
	type ConvTree,
	type Msg
} from './tree';
import type { Conversation, Panel, PanelLayout, StatePatch } from './types';

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

function newest(list: Conversation[]): Conversation | undefined {
	return [...list].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))[0];
}

class ConversationsStore {
	list = $state<Conversation[]>([]);
	activeId = $state<string | null>(null);
	/** Per-panel branch trees keyed by stable panel id ('primary' always present).
	 *  THE read source: +page reads treeFor(panel), computes a new tree via tree.ts,
	 *  commits with setTree(panel,…). N-panel: any number of keys. */
	trees = $state<Record<string, ConvTree>>({ primary: emptyTree() });
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

	/** Tokens of chats THIS browser fired — its own folds are done by fireChat
	 *  from the response stream, so the external-fold hook must skip these. */
	#ownTokens = new Set<string>();
	/** Reactive mirror of (#ownTokens.size > 0). A plain Set isn't tracked by Svelte 5,
	 *  so `busy` reading the Set directly never re-fires the `disabled={…convo.busy}`
	 *  bindings when a token is removed — the New/regen/edit buttons would latch
	 *  disabled after a generation. Keep this in lockstep with every Set mutation. */
	#busy = $state(false);
	#saveTimer: ReturnType<typeof setTimeout> | null = null;
	#pending: {
		id: string;
		trees: Record<string, ConvTree>;
		reduced_panels: string[];
		send_targets: string[];
		seen_panels: string[];
	} | null = null;
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
	 *  schedule a debounced save. */
	setTree(panel: Panel, next: ConvTree, persist = true): void {
		this.trees = { ...this.trees, [panel]: next };
		this.#mirror();
		if (persist) this.save();
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

	/** Duplicate one panel's tree into another (deep, proxy-safe clone — used when a
	 *  new compare panel should start from an existing thread). Does NOT clear other
	 *  panels' live buckets, so adding a panel can't wipe an in-flight stream. */
	duplicateTo(srcPanel: Panel, dstPanel: Panel): void {
		this.trees = { ...this.trees, [dstPanel]: $state.snapshot(this.treeFor(srcPanel)) as ConvTree };
		this.#mirror();
		this.save();
	}

	/** Seed a panel with an EMPTY thread (Shift+add panel = blank, vs duplicateTo's
	 *  clone-the-first-panel default). Mirrors + persists like duplicateTo. */
	freshTree(panel: Panel): void {
		this.trees = { ...this.trees, [panel]: emptyTree() };
		this.#mirror();
		this.save();
	}

	/** Drop a panel's tree (on panel removal). The LAST tree is never dropped —
	 *  a conversation always keeps at least one panel (any id). */
	dropTree(panel: Panel): void {
		if (!(panel in this.trees) || Object.keys(this.trees).length <= 1) return;
		const next = { ...this.trees };
		delete next[panel];
		this.trees = next;
		this.#mirror();
		this.save();
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

	/** Reset every open panel's tree to empty (fresh thread, same panel layout). */
	#freshTrees(): Promise<void> {
		const ids = (live.state?.panels ?? []).map((p) => p.id);
		if (!ids.length) ids.push('primary'); // no panels known yet → the default first slot
		this.trees = Object.fromEntries(ids.map((id) => [id, emptyTree()]));
		return api
			.setState({ panel_messages: Object.fromEntries(ids.map((id) => [id, []])) })
			.then(() => {})
			.catch(() => {});
	}

	// ── persistence (capture-at-schedule; flush-on-switch) ───────────
	save(): void {
		const id = this.activeId;
		if (!id) return;
		// Snapshot all trees; drop empty panels (no rootChildren) but keep at least
		// the FIRST one so a conversation never persists with zero trees.
		const snap = $state.snapshot(this.trees) as Record<string, ConvTree>;
		const trees: Record<string, ConvTree> = {};
		for (const [pid, t] of Object.entries(snap)) {
			if (t.rootChildren.length) trees[pid] = t;
		}
		if (!Object.keys(trees).length) {
			const first = Object.keys(snap)[0] ?? 'primary';
			trees[first] = snap[first] ?? emptyTree();
		}
		this.#pending = {
			id,
			trees,
			reduced_panels: [...this.reducedPanels],
			send_targets: [...this.sendTargets],
			seen_panels: [...this.#seenPanels]
		};
		if (this.#saveTimer) clearTimeout(this.#saveTimer);
		this.#saveTimer = setTimeout(() => void this.#doSave(), 400);
	}

	async #doSave(): Promise<void> {
		this.#saveTimer = null;
		const p = this.#pending;
		this.#pending = null;
		if (!p) return;
		// system_prompt + the panel LAYOUT mirror server state, which lands a beat
		// after a patchState — so read them HERE (debounced), not at schedule time, to
		// avoid persisting a stale value. flush-on-switch keeps live.state aligned with
		// p.id (switch/create flush the pending save before they touch live.state).
		const system_prompt = live.state?.system_prompt ?? null;
		const panels: PanelLayout[] = (live.state?.panels ?? []).map((ps) => ({
			id: ps.id,
			run_id: ps.run_id,
			checkpoint: ps.checkpoint
		}));
		// A pending save IS the first real change to an unsaved draft → materialize it
		// on the backend (create with the draft's id) instead of a tree-save (which would
		// 404). Clear #draftId SYNCHRONOUSLY (before the await) so a racing #doSave can't
		// double-create; restore it on failure so a retry still creates it.
		const materializing = p.id === this.#draftId;
		if (materializing) this.#draftId = null;
		try {
			if (materializing) {
				const name = this.list.find((c) => c.id === p.id)?.name ?? 'Untitled';
				await api.createConversation({
					id: p.id,
					name,
					system_prompt,
					trees: p.trees,
					panels,
					reduced_panels: p.reduced_panels,
					send_targets: p.send_targets,
					seen_panels: p.seen_panels
				});
			} else {
				await api.saveConversationTree(
					p.id,
					p.trees,
					system_prompt,
					panels,
					p.reduced_panels,
					p.send_targets,
					p.seen_panels
				);
			}
			this.list = this.list.map((c) => (c.id === p.id ? { ...c, updated_at: new Date().toISOString() } : c));
		} catch (e) {
			if (materializing) this.#draftId = p.id; // not persisted — still a draft
			console.warn('conversation tree save failed', e);
		}
	}

	async flush(): Promise<void> {
		if (this.#saveTimer) {
			clearTimeout(this.#saveTimer);
			this.#saveTimer = null;
		}
		if (this.#pending) await this.#doSave();
	}

	// ── load / switch / create / rename / remove ─────────────────────
	/** Load the conversation list and pick the active one. If `preferredId` is
	 *  given (e.g. from the `?c=` URL param) and matches a conversation, open it;
	 *  otherwise open the newest. Returns whether `preferredId` was honored (false
	 *  ⇒ it was absent or unknown, so the caller can normalize the URL / notify). */
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
		this.activeId = active.id;
		await this.#loadTrees(active);
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
		live.clearBuckets();
		const conv = this.list.find((c) => c.id === id);
		if (!conv) return;
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
		const draft: Conversation = {
			id: id ?? crypto.randomUUID(),
			name,
			system_prompt: live.state?.system_prompt ?? null,
			trees: { [ids[0]]: emptyTree() },
			panels: layout,
			reduced_panels: [],
			send_targets: [...ids],
			seen_panels: [...ids],
			created_at: now,
			updated_at: now
		};
		this.list = [draft, ...this.list];
		this.activeId = draft.id;
		this.#draftId = draft.id;
		this.#applyPanelUi(draft);
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
			this.save();
			return;
		}
		const updated = await api.renameConversation(id, name);
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
			await this.#freshTrees();
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
			this.activeId = next.id;
			await this.#loadTrees(next);
			this.#afterLoad();
		}
	}

	/** Reset every open panel's tree for a fresh thread under the SAME conversation. */
	async resetActive(): Promise<void> {
		await this.#preSwitch();
		live.clearBuckets();
		await this.#freshTrees();
		this.save();
	}

	/** Migration read-shim: prefer the new {trees} shape; fall back to the legacy
	 *  {tree, compare_tree} (synthesizing reserved 'primary'/'compare' ids) so an
	 *  un-migrated saved conversation loads without losing a user-authored compare
	 *  tree. asTree() returns emptyTree() on malformed input. */
	async #loadTrees(conv: Conversation): Promise<void> {
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
	/** Register the bus hooks for external (CLI / other-tab) turns. Idempotent. */
	init(): void {
		live.onChatDone = (panel, data) => this.#onExternalDone(panel, data);
		live.onChatError = (_panel, data) => {
			if (data?.client_token) this.endToken(data.client_token);
		};
	}

	#onExternalDone(panel: Panel, data: { client_token?: string | null; conversation_id?: string | null }): void {
		// Own chats are folded by fireChat from their response stream — skip.
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

	#flashNotice(msg: string): void {
		this.externalNotice = msg;
		if (this.#noticeTimer) clearTimeout(this.#noticeTimer);
		this.#noticeTimer = setTimeout(() => (this.externalNotice = null), 7000);
	}
}

export const conversations = new ConversationsStore();
