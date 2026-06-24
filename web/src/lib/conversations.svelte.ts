// The conversation/branch-tree store — the frontend owner of the per-panel
// branch trees and their persistence. See BRANCHING_DESIGN.md §6.
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
import type { Conversation, Panel } from './types';

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

	/** Tokens of chats THIS browser fired — its own folds are done by fireChat
	 *  from the response stream, so the external-fold hook must skip these. */
	#ownTokens = new Set<string>();
	#saveTimer: ReturnType<typeof setTimeout> | null = null;
	#pending: { id: string; trees: Record<string, ConvTree>; system_prompt: string | null } | null = null;
	#noticeTimer: ReturnType<typeof setTimeout> | null = null;

	treeFor(panel: Panel): ConvTree {
		return this.trees[panel] ?? emptyTree();
	}

	/** True while ANY own chat is in flight — its fold (from the response stream)
	 *  outlives the bucket `running` flag (which clears on the bus chat_done). Gate
	 *  conversation switch/create/delete on this so an in-flight fold can't land on
	 *  (and be dropped by) a freshly-swapped conversation tree. */
	get busy(): boolean {
		return this.#ownTokens.size > 0;
	}

	// ── ownership tokens ─────────────────────────────────────────────
	newToken(): string {
		const t = 'ct' + Math.random().toString(36).slice(2, 10);
		this.#ownTokens.add(t);
		return t;
	}
	endToken(t: string): void {
		this.#ownTokens.delete(t);
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
		// Echo every panel's active path into PlaygroundState (one patch, messages
		// only — never clobbers per-panel run_id/checkpoint).
		const panel_messages: Record<string, Msg[]> = {};
		for (const [pid, tree] of Object.entries(this.trees)) panel_messages[pid] = activeMessages(tree);
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

	/** Drop a panel's tree (on panel removal). 'primary' is reserved/never dropped. */
	dropTree(panel: Panel): void {
		if (panel === 'primary' || !(panel in this.trees)) return;
		const next = { ...this.trees };
		delete next[panel];
		this.trees = next;
		this.#mirror();
		this.save();
	}

	/** Reset every open panel's tree to empty (fresh thread, same panel layout). */
	#freshTrees(): Promise<void> {
		const ids = (live.state?.panels ?? []).map((p) => p.id);
		if (!ids.includes('primary')) ids.unshift('primary');
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
		// Snapshot all trees; drop empty panels (no rootChildren) but ALWAYS keep
		// 'primary' so a conversation never persists with zero trees.
		const snap = $state.snapshot(this.trees) as Record<string, ConvTree>;
		const trees: Record<string, ConvTree> = {};
		for (const [pid, t] of Object.entries(snap)) {
			if (pid === 'primary' || t.rootChildren.length) trees[pid] = t;
		}
		if (!trees.primary) trees.primary = emptyTree();
		this.#pending = { id, trees, system_prompt: live.state?.system_prompt ?? null };
		if (this.#saveTimer) clearTimeout(this.#saveTimer);
		this.#saveTimer = setTimeout(() => void this.#doSave(), 400);
	}

	async #doSave(): Promise<void> {
		this.#saveTimer = null;
		const p = this.#pending;
		this.#pending = null;
		if (!p) return;
		try {
			await api.saveConversationTree(p.id, p.trees, p.system_prompt);
			this.list = this.list.map((c) => (c.id === p.id ? { ...c, updated_at: new Date().toISOString() } : c));
		} catch (e) {
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
		let list = await api.listConversations();
		if (!list.length) {
			const created = await api.createConversation({
				name: 'Untitled',
				system_prompt: live.state?.system_prompt ?? null,
				trees: { primary: emptyTree() }
			});
			list = [created];
		}
		this.list = list;
		const preferred = preferredId ? list.find((c) => c.id === preferredId) : undefined;
		const active = preferred ?? newest(list)!;
		this.activeId = active.id;
		this.#loadTrees(active);
		this.#afterLoad();
		return !!preferred;
	}

	async switchTo(id: string): Promise<void> {
		if (id === this.activeId) return;
		await this.flush();
		live.clearBuckets();
		const conv = this.list.find((c) => c.id === id);
		if (!conv) return;
		this.activeId = id;
		this.#loadTrees(conv);
		this.#afterLoad();
	}

	async create(name = 'Untitled'): Promise<void> {
		await this.flush();
		live.clearBuckets();
		const created = await api.createConversation({
			name,
			system_prompt: live.state?.system_prompt ?? null,
			trees: { primary: emptyTree() }
		});
		this.list = [created, ...this.list];
		this.activeId = created.id;
		await this.#freshTrees();
	}

	async rename(id: string, name: string): Promise<void> {
		const updated = await api.renameConversation(id, name);
		this.list = this.list.map((c) =>
			c.id === id ? { ...c, name: updated.name, updated_at: updated.updated_at } : c
		);
	}

	async remove(id: string): Promise<void> {
		await this.flush();
		// Never leave zero conversations: the last one resets in place (same id,
		// empty trees, default name).
		if (this.list.length <= 1) {
			live.clearBuckets();
			await this.#freshTrees();
			if (this.activeId) await this.rename(this.activeId, 'Untitled').catch(() => {});
			this.save();
			return;
		}
		await api.deleteConversation(id);
		this.list = this.list.filter((c) => c.id !== id);
		if (this.activeId === id) {
			live.clearBuckets();
			const next = newest(this.list)!;
			this.activeId = next.id;
			this.#loadTrees(next);
			this.#afterLoad();
		}
	}

	/** Reset every open panel's tree for a fresh thread under the SAME conversation. */
	async resetActive(): Promise<void> {
		live.clearBuckets();
		await this.#freshTrees();
		this.save();
	}

	/** Migration read-shim: prefer the new {trees} shape; fall back to the legacy
	 *  {tree, compare_tree} (synthesizing reserved 'primary'/'compare' ids) so an
	 *  un-migrated saved conversation loads without losing a user-authored compare
	 *  tree. asTree() returns emptyTree() on malformed input. */
	#loadTrees(conv: Conversation): void {
		if (conv.trees && typeof conv.trees === 'object') {
			const map: Record<string, ConvTree> = {};
			for (const [pid, t] of Object.entries(conv.trees)) map[pid] = asTree(t);
			if (!map.primary) map.primary = emptyTree();
			this.trees = map;
		} else {
			const map: Record<string, ConvTree> = { primary: asTree(conv.tree) };
			if (conv.compare_tree) map.compare = asTree(conv.compare_tree);
			this.trees = map;
		}
		// system_prompt travels with the conversation (each conv = one experiment).
		api.setState({ system_prompt: conv.system_prompt ?? null }).catch(() => {});
	}

	/** After loading a conversation: fold a stray external turn into each panel (once,
	 *  if not running), then UNCONDITIONALLY mirror the active paths so a fresh/
	 *  restarted backend still learns the loaded conversation. */
	#afterLoad(): void {
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
			if (data?.client_token) this.#ownTokens.delete(data.client_token);
		};
	}

	#onExternalDone(panel: Panel, data: { client_token?: string | null }): void {
		// Own chats are folded by fireChat from their response stream — skip.
		if (data?.client_token && this.#ownTokens.has(data.client_token)) return;
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
