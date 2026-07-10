// The chat-thread branching handlers — edit / regenerate / delete / cycle /
// select / continue, per panel and (in compare mode) across all panels.
//
// All operate on the per-panel branch TREE (the source of truth, owned by the
// convo store). A mutation commits via convo.setTree, which re-derives the active
// path, mirrors it into shared state (so the CLI follows), and debounce-saves.
// Scroll policy: tree mutations PRESERVE the panel's scroll position, deliberate
// jumps SNAP — see lib/scroll.svelte.ts.
//
// Deliberately UI-agnostic (house pattern, like chat.svelte.ts): the four seams
// that reach into +page's composer/selection state — the panel list, the per-panel
// busy gate, the composer prefill wrapper, and the fire glue (model + params
// resolution) — are INJECTED via configure() rather than reached into from here.

import { live } from './state.svelte';
import { conversations as convo } from './conversations.svelte';
import { chat, type ChatParams } from './chat.svelte';
import { panelScroll } from './scroll.svelte';
import { assembleAssistantRaw } from './render';
import {
	activePath,
	regenTarget,
	regenReplace,
	ancestryMessages,
	treeFromMessages,
	editUserFork,
	editUserForkCopy,
	editAssistant,
	deleteSubtree,
	deleteSiblings,
	setSelected,
	cycle as cycleTree,
	siblingsOf
} from './tree';
import type { Panel, PanelSel, ChatMessage, ViewMessage } from './types';

/** The +page-owned seams these handlers need. Injected once via configure() so
 *  the module never reaches into the sidebar/composer state itself. */
export type BranchOpsDeps = {
	/** Current panels (selection projection) — a getter so it reads live. */
	panelSels: () => PanelSel[];
	/** Per-panel busy gate (the panel's bus `running` flag). */
	panelBusy: (panel: Panel) => boolean;
	/** Wrap messages with the active composer prefill (if any) for a fire. */
	withPrefill: (msgs: ChatMessage[]) => { fireMsgs: ChatMessage[]; prefill?: string };
	/** Fire one panel's generation with the current model + params. `paramsOverride`
	 *  patches over the composer's params bundle for THIS fire. */
	fireOne: (
		pSel: PanelSel,
		userParentId: string,
		messages: ChatMessage[],
		prefill?: string,
		paramsOverride?: Partial<ChatParams>
	) => void;
};

class BranchOps {
	#deps: BranchOpsDeps | null = null;
	configure(deps: BranchOpsDeps) {
		this.#deps = deps;
	}
	get #d(): BranchOpsDeps {
		if (!this.#deps) throw new Error('branchOps used before configure()');
		return this.#deps;
	}

	/** Fire a (re)generation for one panel, folding the reply under `userParentId`.
	 *  Regenerate respects the current composer prefill, exactly like a fresh send. */
	#fireForPanel(panel: Panel, userParentId: string, messages: ChatMessage[]) {
		const pSel = this.#d.panelSels().find((x) => x.panel === panel);
		if (!pSel) return;
		const { fireMsgs, prefill } = this.#d.withPrefill(messages);
		this.#d.fireOne(pSel, userParentId, fireMsgs, prefill);
	}

	/** Compute a panel's regen plan: plain = fork a sibling; replace = drop the
	 *  active assistant branch first so the fresh reply takes its place. Commits
	 *  any tree pruning and returns the fire target (or null if not regen-able). */
	#regenPlan(
		panel: Panel,
		nodeId: string,
		replace: boolean
	): { userParentId: string; fireMessages: ChatMessage[] } | null {
		const tree = convo.treeFor(panel);
		if (replace) {
			const r = regenReplace(tree, nodeId);
			if (!r) return null;
			convo.setTree(panel, r.tree);
			return { userParentId: r.userParentId, fireMessages: r.fireMessages as ChatMessage[] };
		}
		const rt = regenTarget(tree, nodeId);
		if (!rt) return null;
		return { userParentId: rt.userParentId, fireMessages: rt.fireMessages as ChatMessage[] };
	}

	/** Prune a node + its subtree (one branch). all (shift) = prune EVERY sibling
	 *  branch at this level too, truncating back to the parent. */
	deleteMessage(panel: Panel, msg: ViewMessage, all = false) {
		if (this.#d.panelBusy(panel) || msg.nodeId == null) return;
		panelScroll.preserve(panel);
		chat.clearPanelBucket(panel);
		const tree = convo.treeFor(panel);
		convo.setTree(panel, all ? deleteSiblings(tree, msg.nodeId) : deleteSubtree(tree, msg.nodeId));
	}

	/** Regenerate this panel's turn. plain = new sibling branch; replace (shift) =
	 *  overwrite the current branch in place (other siblings kept). */
	regenerate(panel: Panel, msg: ViewMessage, replace = false) {
		if (this.#d.panelBusy(panel) || msg.nodeId == null) return;
		panelScroll.preserve(panel);
		chat.clearPanelBucket(panel);
		const plan = this.#regenPlan(panel, msg.nodeId, replace);
		if (!plan) return;
		this.#fireForPanel(panel, plan.userParentId, plan.fireMessages);
	}

	/** Regenerate the turn at this row's DEPTH in EVERY panel (compare mode).
	 *  Matches by active-path depth so each panel re-rolls its own model. */
	regenerateAll(panel: Panel, msg: ViewMessage, replace = false) {
		if (msg.nodeId == null) return;
		const depth = activePath(convo.treeFor(panel)).findIndex((n) => n.id === msg.nodeId);
		if (depth < 0) return;
		for (const p of this.#d.panelSels()) {
			if (this.#d.panelBusy(p.panel)) continue;
			const node = activePath(convo.treeFor(p.panel))[depth];
			if (!node) continue;
			panelScroll.preserve(p.panel);
			chat.clearPanelBucket(p.panel);
			const plan = this.#regenPlan(p.panel, node.id, replace);
			if (plan) this.#fireForPanel(p.panel, plan.userParentId, plan.fireMessages);
		}
	}

	/** Continue (prefill) an assistant turn: re-fire with its content as the trailing
	 *  prefill so the model EXTENDS it. The N continuations land as sibling branches
	 *  (each = the current text + a fresh continuation) you cycle through; the
	 *  original stays as a sibling too. */
	#fireContinue(panel: Panel, nodeId: string) {
		if (this.#d.panelBusy(panel)) return;
		const tree = convo.treeFor(panel);
		const node = tree.nodes[nodeId];
		if (!node || node.role !== 'assistant' || (!node.content && !node.reasoning)) return;
		const userParentId = node.parent;
		if (!userParentId || tree.nodes[userParentId]?.role !== 'user') return;
		const pSel = this.#d.panelSels().find((x) => x.panel === panel);
		if (!pSel) return;
		panelScroll.preserve(panel);
		chat.clearPanelBucket(panel);
		// Prefill = the FULL raw turn (reasoning + content reassembled), NOT just
		// content: on auto-<think> families a content-only prefill makes the model
		// read the answer as more thinking. assembleAssistantRaw closes the
		// `<think>` (so the model extends the answer) or leaves it open for a
		// thinking-only turn. ancestryMessages now carries prior-turn reasoning (the
		// sampler structures it so the renderer applies its own history policy); the
		// turn being CONTINUED is appended separately as this raw `<think>` prefill.
		const prefill = assembleAssistantRaw(node.reasoning, node.content);
		const fireMessages = [
			...ancestryMessages(tree, userParentId),
			{ role: 'assistant', content: prefill }
		] as ChatMessage[];
		// Force scope 'all': this trailing-assistant prefill is the CONTINUATION —
		// extending the turn is the whole point. The composer's prefill-scope
		// tri-state applies to the composer prefill only; letting it ride along
		// here (it's in every params bundle, and it persists in session prefs)
		// silently drops the continuation from the prompt on a mismatched
		// single-mode send, then the fold prepends it anyway — a merged turn the
		// model never saw. Guarded by browser_continue_scope.py.
		this.#d.fireOne(pSel, userParentId, fireMessages, prefill, { prefill_scope: 'all' });
	}

	/** "+" continue. plain = this panel; all (shift) = the turn at this row's depth
	 *  in every panel. */
	continueMessage(panel: Panel, msg: ViewMessage, all = false) {
		if (msg.nodeId == null) return;
		if (!all) { this.#fireContinue(panel, msg.nodeId); return; }
		const depth = activePath(convo.treeFor(panel)).findIndex((n) => n.id === msg.nodeId);
		if (depth < 0) return;
		for (const p of this.#d.panelSels()) {
			const node = activePath(convo.treeFor(p.panel))[depth];
			if (node) this.#fireContinue(p.panel, node.id);
		}
	}

	/** Cycle the active sibling at this row (‹k/N›). */
	cycleBranch(panel: Panel, msg: ViewMessage, delta: number) {
		if (this.#d.panelBusy(panel) || msg.nodeId == null) return;
		panelScroll.preserve(panel);
		chat.clearPanelBucket(panel);
		convo.setTree(panel, cycleTree(convo.treeFor(panel), msg.nodeId, delta));
	}

	/** Pick an n>1 sample card as the active branch, then COLLAPSE to the single
	 *  reply view (clear the bucket) — the other samples remain as cyclable ‹k/N›
	 *  siblings in the tree. */
	selectSample(panel: Panel, msg: ViewMessage, sampleIndex: number) {
		if (this.#d.panelBusy(panel)) return;
		const nid = msg.sampleNodeIds?.[sampleIndex];
		if (!nid) return;
		panelScroll.preserve(panel);
		convo.setTree(panel, setSelected(convo.treeFor(panel), nid));
		chat.clearPanelBucket(panel); // collapse the distribution view to the chosen branch
	}

	/** Continue ONE specific n>1 sample card: make it the active branch, then
	 *  extend it via fireContinue — the continuations land as sibling branches
	 *  (this sample stays as one of them). */
	continueSample(panel: Panel, msg: ViewMessage, sampleIndex: number) {
		if (this.#d.panelBusy(panel)) return;
		const nid = msg.sampleNodeIds?.[sampleIndex];
		if (!nid) return;
		convo.setTree(panel, setSelected(convo.treeFor(panel), nid));
		this.#fireContinue(panel, nid);
	}

	/** Keep this sample, prune all its sibling samples, then collapse to it. */
	discardOtherSamples(panel: Panel, msg: ViewMessage, sampleIndex: number) {
		if (this.#d.panelBusy(panel)) return;
		const keep = msg.sampleNodeIds?.[sampleIndex];
		if (!keep) return;
		panelScroll.preserve(panel);
		let tree = setSelected(convo.treeFor(panel), keep);
		for (const sib of siblingsOf(tree, keep)) {
			if (sib !== keep) tree = deleteSubtree(tree, sib);
		}
		convo.setTree(panel, tree);
		chat.clearPanelBucket(panel);
	}

	/** Delete one specific sample branch; drop it from the live cards too so the
	 *  remaining samples stay on screen for further curation. */
	deleteSample(panel: Panel, msg: ViewMessage, sampleIndex: number) {
		if (this.#d.panelBusy(panel)) return;
		const nid = msg.sampleNodeIds?.[sampleIndex];
		if (!nid) return;
		panelScroll.preserve(panel);
		convo.setTree(panel, deleteSubtree(convo.treeFor(panel), nid));
		// Remove the card from the bucket overlay (keep the rest visible).
		const bucket = live.panels[panel];
		if (bucket && bucket.samples.length > sampleIndex) {
			const samples = bucket.samples.filter((_, i) => i !== sampleIndex);
			live.panels[panel] = { ...bucket, samples, n: Math.max(1, samples.length) };
			live.panels = { ...live.panels };
		}
	}

	/** Edit → fork. User: fork+regen (shift = fork+copy-downstream, no gen).
	 *  Assistant: a manual branch (no gen). Empty edits are ignored. */
	applyEdit(
		panel: Panel,
		msg: ViewMessage,
		content: string,
		reasoning: string | undefined,
		copyDownstream: boolean
	) {
		if (this.#d.panelBusy(panel) || msg.nodeId == null) return;
		const text = content.trim();
		// Assistant turns may be thinking-only (empty answer) — keep the edit if
		// either field has text; user turns still require a non-empty body.
		if (!text && !(msg.role === 'assistant' && reasoning && reasoning.trim())) return;
		panelScroll.preserve(panel);
		chat.clearPanelBucket(panel);
		if (msg.role === 'user') {
			if (copyDownstream) {
				const r = editUserForkCopy(convo.treeFor(panel), msg.nodeId, text);
				if (r) convo.setTree(panel, r.tree);
			} else {
				const r = editUserFork(convo.treeFor(panel), msg.nodeId, text);
				if (!r) return;
				convo.setTree(panel, r.tree);
				this.#fireForPanel(panel, r.newUserId, r.fireMessages as ChatMessage[]);
			}
		} else if (msg.role === 'assistant') {
			const r = editAssistant(convo.treeFor(panel), msg.nodeId, text, reasoning);
			if (r) convo.setTree(panel, r.tree);
		}
	}

	/** Delete the turn at this row's DEPTH in EVERY panel (ctrl/cmd, compare).
	 *  allSiblings (shift) prunes every sibling branch at that level too. */
	deleteMessageAll(panel: Panel, msg: ViewMessage, allSiblings = false) {
		if (msg.nodeId == null) return;
		const depth = activePath(convo.treeFor(panel)).findIndex((n) => n.id === msg.nodeId);
		if (depth < 0) return;
		for (const p of this.#d.panelSels()) {
			const node = activePath(convo.treeFor(p.panel))[depth];
			if (node) this.deleteMessage(p.panel, { ...msg, nodeId: node.id }, allSiblings);
		}
	}

	/** Apply the same edit to the turn at this row's DEPTH in EVERY panel (ctrl/cmd,
	 *  compare). copyDownstream (shift, user rows) forks a full copy with no gen. */
	applyEditAll(
		panel: Panel,
		msg: ViewMessage,
		content: string,
		reasoning: string | undefined,
		copyDownstream: boolean
	) {
		if (msg.nodeId == null) return;
		const depth = activePath(convo.treeFor(panel)).findIndex((n) => n.id === msg.nodeId);
		if (depth < 0) return;
		for (const p of this.#d.panelSels()) {
			const node = activePath(convo.treeFor(p.panel))[depth];
			if (node) this.applyEdit(p.panel, { ...msg, nodeId: node.id }, content, reasoning, copyDownstream);
		}
	}

	/** Copy to the clipboard. `all` = the whole active thread as markdown with role
	 *  headers (vs just this row). `withThinking` (shift) prepends each turn's reasoning
	 *  as a `<think>…</think>` block before its content. Uses activePath (not
	 *  activeMessages, which drops reasoning) so the thinking is actually available. */
	copyMessage(panel: Panel, msg: ViewMessage, all: boolean, withThinking: boolean) {
		const fmt = (content?: string, reasoning?: string) =>
			withThinking && reasoning && reasoning.trim()
				? `<think>\n${reasoning}\n</think>\n\n${content ?? ''}`
				: (content ?? '');
		let text: string;
		if (all) {
			const nodes = activePath(convo.treeFor(panel)).filter((n) => n.role !== 'system');
			const header = (r: string) => (r === 'user' ? 'User' : r === 'assistant' ? 'Assistant' : 'System');
			text = nodes.map((n) => `## ${header(n.role)}\n\n${fmt(n.content, n.reasoning)}`).join('\n\n');
		} else {
			text = fmt(msg.content, msg.reasoning);
		}
		navigator.clipboard?.writeText(text);
	}

	/** Copy a branch's ancestry (root→this node) into ANOTHER panel as a fresh linear
	 *  thread, so you can prompt that panel's model with exactly this context. */
	sendBranchToPanel(srcPanel: Panel, msg: ViewMessage, destPanel: Panel) {
		if (msg.nodeId == null || destPanel === srcPanel || this.#d.panelBusy(destPanel)) return;
		const msgs = ancestryMessages(convo.treeFor(srcPanel), msg.nodeId) as ChatMessage[];
		if (!msgs.length) return;
		chat.clearPanelBucket(destPanel);
		convo.setTree(destPanel, treeFromMessages(msgs));
		panelScroll.snap(destPanel); // fresh thread in dest — land on its latest turn
	}
}

export const branchOps = new BranchOps();
