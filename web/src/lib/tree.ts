// Conversation branch tree — PURE, no svelte/browser imports (Node 22 can run
// tree.test.ts directly via type-stripping). This is the single source of truth
// for the branched conversation; the linear ACTIVE PATH it derives is what the
// sampler/CLI read (mirrored into PlaygroundState.messages). See docs/BRANCHING_DESIGN.md.
//
// Key invariants (from the design critique):
//  - `selected` maps a parent → the selected child's ID (NOT an index), so a
//    delete/reorder of a sibling never silently reselects a different node.
//  - default selection (unset / dangling) = the LAST child (newest), so a fresh
//    fork/regen auto-shows the new branch.
//  - every op is immutable: clone → mutate → return.

export const ROOT = '__root__';

export type NodeRole = 'user' | 'assistant' | 'system';

export type TreeNode = {
	id: string;
	role: NodeRole;
	content: string;
	reasoning?: string; // persisted; populated by foldAssistant from samples
	raw_text?: string; // persisted; survives reload
	raw_meta?: string; // persisted; tinker request/response (dropdown beneath raw_text)
	/** The authored assistant prefill this turn was generated from (raw text, incl.
	 *  any `<think>`), persisted so the rendered turn can color the prefilled portion
	 *  distinctly from the model's continuation. Absent ⇒ no prefill was used. */
	prefill?: string;
	/** How generation ended ('stop' | 'length' | …) — 'length' ⇒ cut off by the
	 *  max-tokens limit; persisted so the truncation badge survives reload. */
	finish_reason?: string;
	parent: string | null; // null = child of the virtual root
	children: string[]; // ordered
};

export type ConvTree = {
	nodes: Record<string, TreeNode>;
	rootChildren: string[];
	selected: Record<string, string>; // (parentId | ROOT) -> selected child ID
};

/** A streamed sample as folded into the tree (subset of SampleData). */
export type SampleLike = {
	content?: string;
	reasoning?: string;
	raw_text?: string;
	raw_meta?: string;
	error?: string;
	/** Native tinker path with a prefill: content/reasoning already span
	 *  prefill+completion, so the client must not re-prepend the prefill. */
	prefill_incorporated?: boolean;
	/** The authored prefill this sample was generated from (raw text) — folded onto
	 *  the node so the rendered turn can color the prefilled prefix. */
	prefill?: string;
	/** How generation ended — 'length' ⇒ cut off by the max-tokens limit. */
	finish_reason?: string;
	sample_index?: number;
};

export type Msg = { role: NodeRole; content: string; reasoning?: string };

/** Tree node → wire Msg. Carries `reasoning` ONLY when present, so the sampler can hand
 *  the renderer the full turn ([thinking, text] structured content) and let the model's
 *  renderer apply its own history policy (strip_thinking_from_history / preserve). A turn
 *  without reasoning stays `{role, content}` — byte-identical to before, so the UI mirror,
 *  persistence echo, and tree tests are unaffected. `content` is always answer-only; the
 *  thinking lives in the separate field, never inlined as a `<think>` string (the renderers
 *  pass string content through verbatim, so an inlined tag would force-keep the CoT). */
function nodeToMsg(n: TreeNode): Msg {
	return n.reasoning
		? { role: n.role, content: n.content, reasoning: n.reasoning }
		: { role: n.role, content: n.content };
}

// ── IDs ──────────────────────────────────────────────────────────────
// Per-load random session prefix so two tabs editing one persisted tree never
// mint colliding ids. `__resetIds()` makes tests deterministic.
let _session = randomSession();
let _counter = 0;

function randomSession(): string {
	// Math.random is fine here (the workflow-script ban doesn't apply to the
	// browser/node runtime). 4 base36 chars ≈ 1.6M sessions.
	return Math.random().toString(36).slice(2, 6);
}

export function nid(): string {
	return 'n' + _session + (++_counter).toString(36);
}

/** Test hook: deterministic ids from a fixed session + zeroed counter. */
export function __resetIds(session = 'tst'): void {
	_session = session;
	_counter = 0;
}

// ── construction ─────────────────────────────────────────────────────
export function emptyTree(): ConvTree {
	return { nodes: {}, rootChildren: [], selected: {} };
}

function cloneTree(t: ConvTree): ConvTree {
	// Manual deep clone (NOT structuredClone): callers pass a Svelte $state proxy,
	// which structuredClone refuses ("could not be cloned"). Reading through the
	// proxy property-by-property yields plain objects the ops can mutate + persist.
	const nodes: Record<string, TreeNode> = {};
	for (const id in t.nodes) {
		const n = t.nodes[id];
		nodes[id] = {
			id: n.id,
			role: n.role,
			content: n.content,
			reasoning: n.reasoning,
			raw_text: n.raw_text,
				raw_meta: n.raw_meta,
			prefill: n.prefill,
			finish_reason: n.finish_reason,
			parent: n.parent,
			children: [...n.children]
		};
	}
	return { nodes, rootChildren: [...t.rootChildren], selected: { ...t.selected } };
}

function childArray(t: ConvTree, parentKey: string): string[] {
	return parentKey === ROOT ? t.rootChildren : t.nodes[parentKey].children;
}

function parentKeyOfNode(n: TreeNode): string {
	return n.parent ?? ROOT;
}

// ── derivation ───────────────────────────────────────────────────────
/** The selected child id of `parentKey`, or the last child (default), or null. */
export function selectedChildId(t: ConvTree, parentKey: string): string | null {
	const kids = parentKey === ROOT ? t.rootChildren : t.nodes[parentKey]?.children;
	if (!kids || kids.length === 0) return null;
	const sel = t.selected[parentKey];
	if (sel != null && kids.includes(sel)) return sel;
	return kids[kids.length - 1];
}

/** Root → leaf, following the selected child at each step. */
export function activePath(t: ConvTree): TreeNode[] {
	const path: TreeNode[] = [];
	let parentKey = ROOT;
	const seen = new Set<string>(); // cycle guard (defensive; trees are acyclic)
	while (true) {
		const childId = selectedChildId(t, parentKey);
		if (childId == null || seen.has(childId)) break;
		const node = t.nodes[childId];
		if (!node) break;
		seen.add(childId);
		path.push(node);
		parentKey = childId;
	}
	return path;
}

/** The active path as [{role,content}] (system turns excluded) — feeds messages. */
export function activeMessages(t: ConvTree): Msg[] {
	return activePath(t)
		.filter((n) => n.role !== 'system')
		.map(nodeToMsg);
}

export function parentKeyOf(t: ConvTree, id: string): string {
	const n = t.nodes[id];
	return n ? parentKeyOfNode(n) : ROOT;
}

export function siblingsOf(t: ConvTree, id: string): string[] {
	// A missing id must NOT alias ROOT's children (that would fabricate a sibling
	// set from unrelated roots); a stale id has no siblings.
	if (id !== ROOT && !t.nodes[id]) return [];
	return childArray(t, parentKeyOf(t, id));
}

export function siblingInfo(t: ConvTree, id: string): { index: number; count: number } {
	const sibs = siblingsOf(t, id);
	return { index: sibs.indexOf(id), count: sibs.length };
}

/** Messages from the root down to `id` inclusive, via the PARENT chain (so it
 *  works regardless of the current selection). System turns excluded. */
export function ancestryMessages(t: ConvTree, id: string): Msg[] {
	const chain: TreeNode[] = [];
	let cur: TreeNode | undefined = t.nodes[id];
	const seen = new Set<string>();
	while (cur && !seen.has(cur.id)) {
		seen.add(cur.id);
		chain.push(cur);
		cur = cur.parent ? t.nodes[cur.parent] : undefined;
	}
	chain.reverse();
	return chain.filter((n) => n.role !== 'system').map(nodeToMsg);
}

/** The active-path nodes strictly BELOW `id` (or [] if id isn't on the path). */
function downstreamActivePath(t: ConvTree, id: string): TreeNode[] {
	const path = activePath(t);
	const i = path.findIndex((n) => n.id === id);
	return i < 0 ? [] : path.slice(i + 1);
}

// ── mutations (immutable) ────────────────────────────────────────────
export function appendUserTurn(t0: ConvTree, content: string): { tree: ConvTree; nodeId: string } {
	const t = cloneTree(t0);
	const path = activePath(t);
	const leaf = path.length ? path[path.length - 1] : null;
	const parentKey = leaf ? leaf.id : ROOT;
	const id = nid();
	t.nodes[id] = { id, role: 'user', content, parent: leaf ? leaf.id : null, children: [] };
	childArray(t, parentKey).push(id);
	t.selected[parentKey] = id;
	return { tree: t, nodeId: id };
}

export function foldAssistant(
	t0: ConvTree,
	parentUserId: string,
	samples: SampleLike[]
): { tree: ConvTree; ids: string[] } {
	const t = cloneTree(t0);
	const parent = t.nodes[parentUserId];
	if (!parent) return { tree: t, ids: [] }; // parent pruned mid-fold — drop quietly
	const ordered = [...samples].sort((a, b) => (a.sample_index ?? 0) - (b.sample_index ?? 0));
	const ids: string[] = [];
	for (const s of ordered) {
		if (s.error) continue; // skip error samples; don't shift indices into nodes
		const id = nid();
		t.nodes[id] = {
			id,
			role: 'assistant',
			content: s.content ?? '',
			reasoning: s.reasoning,
			raw_text: s.raw_text,
				raw_meta: s.raw_meta,
			prefill: s.prefill,
			finish_reason: s.finish_reason,
			parent: parentUserId,
			children: []
		};
		parent.children.push(id);
		ids.push(id);
	}
	if (ids.length) t.selected[parentUserId] = ids[0]; // select FIRST of the new batch
	return { tree: t, ids };
}

export function regenTarget(
	t: ConvTree,
	nodeId: string
): { userParentId: string; fireMessages: Msg[] } | null {
	const node = t.nodes[nodeId];
	if (!node) return null;
	const userId = node.role === 'assistant' ? node.parent : nodeId;
	if (!userId || t.nodes[userId]?.role !== 'user') return null;
	return { userParentId: userId, fireMessages: ancestryMessages(t, userId) };
}

export function editUserFork(
	t0: ConvTree,
	userId: string,
	content: string
): { tree: ConvTree; newUserId: string; fireMessages: Msg[] } | null {
	const orig = t0.nodes[userId];
	if (!orig || orig.role !== 'user') return null;
	const t = cloneTree(t0);
	const parentKey = orig.parent ?? ROOT;
	const id = nid();
	t.nodes[id] = { id, role: 'user', content, parent: orig.parent, children: [] };
	childArray(t, parentKey).push(id);
	t.selected[parentKey] = id;
	return { tree: t, newUserId: id, fireMessages: ancestryMessages(t, id) };
}

export function editUserForkCopy(
	t0: ConvTree,
	userId: string,
	content: string
): { tree: ConvTree; newUserId: string } | null {
	const orig = t0.nodes[userId];
	if (!orig || orig.role !== 'user') return null;
	const downstream = downstreamActivePath(t0, userId); // from the ORIGINAL selection
	const t = cloneTree(t0);
	const parentKey = orig.parent ?? ROOT;
	const newUserId = nid();
	t.nodes[newUserId] = { id: newUserId, role: 'user', content, parent: orig.parent, children: [] };
	childArray(t, parentKey).push(newUserId);
	t.selected[parentKey] = newUserId;
	// deep-copy the downstream chain as fresh-id single-child descendants, writing
	// a fresh `selected` entry along the way (each copied node selects its copy).
	let curParent = newUserId;
	for (const node of downstream) {
		const cid = nid();
		t.nodes[cid] = {
			id: cid,
			role: node.role,
			content: node.content,
			reasoning: node.reasoning,
			raw_text: node.raw_text,
				raw_meta: node.raw_meta,
			parent: curParent,
			children: []
		};
		t.nodes[curParent].children.push(cid);
		t.selected[curParent] = cid;
		curParent = cid;
	}
	return { tree: t, newUserId };
}

export function editAssistant(
	t0: ConvTree,
	asstId: string,
	content: string,
	reasoning?: string
): { tree: ConvTree; newId: string } | null {
	const orig = t0.nodes[asstId];
	if (!orig || orig.role !== 'assistant') return null;
	const t = cloneTree(t0);
	const parentKey = orig.parent ?? ROOT;
	const id = nid();
	// Manual branch: store the edited reasoning (empty ⇒ drop the CoT). raw_text /
	// raw_meta / prefill are the model's originals — stale after a hand-edit, so omit.
	t.nodes[id] = {
		id,
		role: 'assistant',
		content,
		reasoning: reasoning && reasoning.trim() ? reasoning : undefined,
		parent: orig.parent,
		children: []
	};
	childArray(t, parentKey).push(id);
	t.selected[parentKey] = id;
	return { tree: t, newId: id };
}

export function deleteSubtree(t0: ConvTree, nodeId: string): ConvTree {
	const node = t0.nodes[nodeId];
	if (!node) return t0;
	const t = cloneTree(t0);
	const parentKey = node.parent ?? ROOT;
	// collect the whole subtree
	const toRemove: string[] = [];
	const stack = [nodeId];
	while (stack.length) {
		const id = stack.pop()!;
		toRemove.push(id);
		const n = t.nodes[id];
		if (n) stack.push(...n.children);
	}
	const removeSet = new Set(toRemove);
	const sibs = childArray(t, parentKey);
	const idx = sibs.indexOf(nodeId);
	if (idx >= 0) sibs.splice(idx, 1);
	for (const id of toRemove) {
		delete t.nodes[id];
		delete t.selected[id];
	}
	// if the parent's selected child was pruned, drop it → default-last picks a
	// surviving sibling (or none). Selection-by-id means UNRELATED siblings keep
	// their selection regardless of array shifts.
	if (t.selected[parentKey] != null && removeSet.has(t.selected[parentKey])) {
		delete t.selected[parentKey];
	}
	return t;
}

/** Delete EVERY sibling branch at this node's level (each + its subtree), not just
 *  this one — shift+delete. Leaves the parent with no children at this position. */
export function deleteSiblings(t0: ConvTree, nodeId: string): ConvTree {
	const sibs = [...siblingsOf(t0, nodeId)];
	if (sibs.length === 0) return t0;
	let t = t0;
	for (const sib of sibs) t = deleteSubtree(t, sib);
	return t;
}

/** Shift-regenerate: drop the CURRENTLY-ACTIVE assistant branch under this row's
 *  user parent (other siblings preserved) so a fresh reply REPLACES it in place,
 *  rather than appending a new sibling. Returns the pruned tree + fire target
 *  (the user parent's ancestry is unchanged by the deletion). */
export function regenReplace(
	t0: ConvTree,
	nodeId: string
): { tree: ConvTree; userParentId: string; fireMessages: Msg[] } | null {
	const rt = regenTarget(t0, nodeId);
	if (!rt) return null;
	const active = selectedChildId(t0, rt.userParentId);
	const t = active && t0.nodes[active]?.role === 'assistant' ? deleteSubtree(t0, active) : t0;
	return { tree: t, userParentId: rt.userParentId, fireMessages: ancestryMessages(t, rt.userParentId) };
}

/** Select `nodeId` as its parent's active child. */
export function setSelected(t0: ConvTree, nodeId: string): ConvTree {
	const node = t0.nodes[nodeId];
	if (!node) return t0;
	const parentKey = node.parent ?? ROOT;
	if (!childArray(t0, parentKey).includes(nodeId)) return t0;
	const t = cloneTree(t0);
	t.selected[parentKey] = nodeId;
	return t;
}

/** Step the selection ±delta among `nodeId`'s siblings, WRAPPING around the ends
 *  (next past the last → first, prev before the first → last; 1-2-3-1-2-3…). */
export function cycle(t0: ConvTree, nodeId: string, delta: number): ConvTree {
	const sibs = siblingsOf(t0, nodeId);
	const n = sibs.length;
	const i = sibs.indexOf(nodeId);
	if (i < 0 || n === 0) return t0;
	const j = ((i + delta) % n + n) % n; // positive modulo → wraps both directions
	if (j === i) return t0;
	return setSelected(t0, sibs[j]);
}

// ── reconciliation ───────────────────────────────────────────────────
/** Fold an EXTERNAL (CLI / other-tab / on-load) transcript into the tree.
 *
 *  `msgs` is the backend's CUMULATIVE active path (chat.py commits [*history,
 *  assistant] each turn), NOT a standalone turn — so we walk it against the tree:
 *   - while it matches existing nodes (by role+content), follow + SELECT them
 *     (re-selects a matching non-active sibling so the view reflects what the CLI
 *     ran; idempotent when it's already the active path);
 *   - at the first divergence, append the remaining `msgs` as a fresh chain —
 *     EXTENDING the matched branch in place if we got partway (a continued CLI
 *     thread), or as a NEW ROOT if nothing matched (a divergent reset).
 *  Existing branches are always preserved. Returns the SAME ref (no-op) when
 *  nothing changed, so the caller can cheaply detect a real external change. */
export function reconcileExternal(t0: ConvTree, msgs: Msg[]): ConvTree {
	if (!msgs || msgs.length === 0) return t0;
	const t = cloneTree(t0);
	let changed = false;
	let parentKey = ROOT;
	let i = 0;
	// 1. follow + select the longest existing prefix of msgs.
	for (; i < msgs.length; i++) {
		const m = msgs[i];
		const childIds = parentKey === ROOT ? t.rootChildren : t.nodes[parentKey].children;
		const cid = childIds.find((c) => {
			const n = t.nodes[c];
			return n && n.role === m.role && n.content === m.content;
		});
		if (!cid) break;
		if (t.selected[parentKey] !== cid) {
			t.selected[parentKey] = cid;
			changed = true;
		}
		parentKey = cid;
	}
	// 2. append the unmatched tail (extend the branch, or new root if i===0).
	for (; i < msgs.length; i++) {
		const m = msgs[i];
		const id = nid();
		t.nodes[id] = {
			id,
			role: m.role,
			content: m.content,
			// preserve thinking carried on an external/echoed turn so a CLI/cross-tab
			// reply round-trips its CoT into the tree (not just answer-only)
			...(m.reasoning ? { reasoning: m.reasoning } : {}),
			parent: parentKey === ROOT ? null : parentKey,
			children: []
		};
		if (parentKey === ROOT) t.rootChildren.push(id);
		else t.nodes[parentKey].children.push(id);
		t.selected[parentKey] = id;
		parentKey = id;
		changed = true;
	}
	return changed ? t : t0;
}

export function treeFromMessages(msgs: Msg[]): ConvTree {
	return reconcileExternal(emptyTree(), msgs);
}

// ── validation (used by tests + the on-load tree validator) ──────────
/** Throws on any structural corruption — used as the load-time validator + the
 *  test oracle. Checks BOTH directions (forward: listed children exist & point
 *  back; reverse: every node's parent resolves & lists it), child uniqueness,
 *  reachability from the roots, and selected-key liveness. */
export function assertValid(t: ConvTree): void {
	const noDup = (arr: string[], where: string) => {
		if (new Set(arr).size !== arr.length) throw new Error(`${where} has duplicate child ids`);
	};
	noDup(t.rootChildren, 'rootChildren');
	for (const id of Object.keys(t.nodes)) {
		if (id === ROOT) throw new Error(`node id collides with ROOT sentinel: ${id}`);
		const n = t.nodes[id];
		if (n.id !== id) throw new Error(`node ${id} has mismatched .id ${n.id}`);
		noDup(n.children, `node ${id} children`);
		// forward: each listed child exists and points back here.
		for (const c of n.children) {
			if (!t.nodes[c]) throw new Error(`node ${id} child ${c} missing`);
			if ((t.nodes[c].parent ?? ROOT) !== id)
				throw new Error(`child ${c} parent pointer != ${id}`);
		}
		// reverse: this node's parent resolves and lists it.
		if (n.parent === null) {
			if (!t.rootChildren.includes(id)) throw new Error(`root node ${id} not in rootChildren`);
		} else {
			const p = t.nodes[n.parent];
			if (!p) throw new Error(`node ${id} parent ${n.parent} missing`);
			if (!p.children.includes(id)) throw new Error(`node ${id} not listed in parent ${n.parent}`);
		}
	}
	for (const c of t.rootChildren) {
		if (!t.nodes[c]) throw new Error(`rootChild ${c} missing`);
		if (t.nodes[c].parent !== null) throw new Error(`rootChild ${c} has non-null parent`);
	}
	// reachability: every node is reachable from the roots (no island subtrees).
	const seen = new Set<string>();
	const stack = [...t.rootChildren];
	while (stack.length) {
		const id = stack.pop()!;
		if (seen.has(id)) continue;
		seen.add(id);
		stack.push(...(t.nodes[id]?.children ?? []));
	}
	if (seen.size !== Object.keys(t.nodes).length)
		throw new Error(`unreachable nodes: ${Object.keys(t.nodes).filter((id) => !seen.has(id))}`);
	for (const [parentKey, childId] of Object.entries(t.selected)) {
		const kids = parentKey === ROOT ? t.rootChildren : t.nodes[parentKey]?.children;
		if (!kids) throw new Error(`selected key ${parentKey} is not a live node/ROOT`);
		if (!kids.includes(childId))
			throw new Error(`selected[${parentKey}]=${childId} is not a child of it`);
	}
}
