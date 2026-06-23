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
	tree = $state<ConvTree>(emptyTree());
	compareTree = $state<ConvTree>(emptyTree());
	/** Transient hint shown when the terminal/another tab branched the conversation. */
	externalNotice = $state<string | null>(null);

	/** Tokens of chats THIS browser fired — its own folds are done by fireChat
	 *  from the response stream, so the external-fold hook must skip these. */
	#ownTokens = new Set<string>();
	#saveTimer: ReturnType<typeof setTimeout> | null = null;
	#pending: { id: string; tree: ConvTree; compareTree: ConvTree | null; system_prompt: string | null } | null = null;
	#noticeTimer: ReturnType<typeof setTimeout> | null = null;

	treeFor(panel: Panel): ConvTree {
		return panel === 'compare' ? this.compareTree : this.tree;
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
		if (panel === 'compare') this.compareTree = next;
		else this.tree = next;
		this.#mirror();
		if (persist) this.save();
	}

	#mirror(): void {
		api
			.setState({
				messages: activeMessages(this.tree),
				compare_messages: activeMessages(this.compareTree)
			})
			.catch(() => {});
	}

	// ── persistence (capture-at-schedule; flush-on-switch) ───────────
	save(): void {
		const id = this.activeId;
		if (!id) return;
		const ct = $state.snapshot(this.compareTree) as ConvTree;
		this.#pending = {
			id,
			tree: $state.snapshot(this.tree) as ConvTree,
			compareTree: ct.rootChildren.length ? ct : null,
			system_prompt: live.state?.system_prompt ?? null
		};
		if (this.#saveTimer) clearTimeout(this.#saveTimer);
		this.#saveTimer = setTimeout(() => void this.#doSave(), 400);
	}

	async #doSave(): Promise<void> {
		this.#saveTimer = null;
		const p = this.#pending;
		this.#pending = null;
		if (!p) return;
		try {
			await api.saveConversationTree(p.id, p.tree, p.compareTree, p.system_prompt);
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
	async load(): Promise<void> {
		let list = await api.listConversations();
		if (!list.length) {
			const created = await api.createConversation({
				name: 'Untitled',
				system_prompt: live.state?.system_prompt ?? null,
				tree: emptyTree()
			});
			list = [created];
		}
		this.list = list;
		const active = newest(list)!;
		this.activeId = active.id;
		this.#loadTrees(active);
		this.#afterLoad();
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
			tree: emptyTree()
		});
		this.list = [created, ...this.list];
		this.activeId = created.id;
		this.tree = emptyTree();
		this.compareTree = emptyTree();
		await api.setState({ messages: [], compare_messages: [] }).catch(() => {});
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
			this.tree = emptyTree();
			this.compareTree = emptyTree();
			await api.setState({ messages: [], compare_messages: [] }).catch(() => {});
			if (this.activeId) await this.rename(this.activeId, 'Untitled').catch(() => {});
			this.setTree('primary', emptyTree());
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

	/** Reset the trees + transcripts for a fresh thread under the SAME conversation. */
	async resetActive(): Promise<void> {
		live.clearBuckets();
		this.tree = emptyTree();
		this.compareTree = emptyTree();
		await api.setState({ messages: [], compare_messages: [] }).catch(() => {});
		this.save();
	}

	/** Entering compare: duplicate the primary thread into the compare panel so
	 *  both panels start from the SAME conversation (nothing destroyed). The two
	 *  trees are independent deep copies — $state.snapshot is the proxy-safe deep
	 *  clone (structuredClone chokes on $state proxies). */
	duplicateToCompare(): void {
		live.clearBuckets();
		this.compareTree = $state.snapshot(this.tree) as ConvTree;
		this.#mirror();
		this.save();
	}

	#loadTrees(conv: Conversation): void {
		this.tree = asTree(conv.tree);
		this.compareTree = conv.compare_tree ? asTree(conv.compare_tree) : emptyTree();
		// system_prompt travels with the conversation (each conv = one experiment).
		api.setState({ system_prompt: conv.system_prompt ?? null }).catch(() => {});
	}

	/** After loading a conversation: fold a stray external turn (once, if not
	 *  running), then UNCONDITIONALLY mirror the active paths to the backend so a
	 *  fresh/restarted backend still learns the loaded conversation. */
	#afterLoad(): void {
		if (!live.anyRunning) {
			const pm = (live.state?.messages ?? []) as Msg[];
			if (pm.length && !msgsEqual(pm, activeMessages(this.tree)))
				this.tree = reconcileExternal(this.tree, pm);
			const cm = (live.state?.compare_messages ?? []) as Msg[];
			if (cm.length && !msgsEqual(cm, activeMessages(this.compareTree)))
				this.compareTree = reconcileExternal(this.compareTree, cm);
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
		const msgs = (panel === 'compare' ? live.state?.compare_messages : live.state?.messages) as
			| Msg[]
			| undefined;
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
