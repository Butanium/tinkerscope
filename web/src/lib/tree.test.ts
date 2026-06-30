// Pure unit tests for tree.ts — run WITHOUT a test framework via Node 22's
// built-in TS type-stripping:   node web/src/lib/tree.test.ts
// (no dep added; respects the supply-chain age gate). Exit code != 0 on failure.

import {
	ROOT,
	emptyTree,
	treeFromMessages,
	activePath,
	activeMessages,
	appendUserTurn,
	foldAssistant,
	regenTarget,
	regenReplace,
	editUserFork,
	editUserForkCopy,
	editAssistant,
	deleteSubtree,
	deleteSiblings,
	setSelected,
	cycle,
	siblingInfo,
	siblingsOf,
	reconcileExternal,
	assertValid,
	__resetIds,
	type ConvTree,
	type Msg
} from './tree.ts';

let passed = 0;
let failed = 0;
const fails: string[] = [];

function test(name: string, fn: () => void): void {
	try {
		__resetIds();
		fn();
		passed++;
	} catch (e) {
		failed++;
		fails.push(`✗ ${name}\n    ${(e as Error).message}`);
	}
}

function ok(cond: boolean, msg = 'expected true'): void {
	if (!cond) throw new Error(msg);
}
function eq(a: unknown, b: unknown, msg = ''): void {
	const sa = JSON.stringify(a);
	const sb = JSON.stringify(b);
	if (sa !== sb) throw new Error(`${msg} expected ${sb} got ${sa}`);
}
/** Content list of the active path's assistant/user turns. */
function msgContents(t: ConvTree): string[] {
	return activeMessages(t).map((m) => m.content);
}
function U(content: string): Msg {
	return { role: 'user', content };
}
function A(content: string, reasoning?: string): Msg {
	// activeMessages/ancestryMessages carry `reasoning` only when the node has it.
	return reasoning ? { role: 'assistant', content, reasoning } : { role: 'assistant', content };
}

// Build [U1,A1,U2,A2] linear tree the cheap way for setup.
function linear4(): ConvTree {
	return treeFromMessages([U('U1'), A('A1'), U('U2'), A('A2')]);
}

// ── construction / derivation ────────────────────────────────────────
test('emptyTree has empty active path', () => {
	const t = emptyTree();
	eq(activePath(t).length, 0);
	eq(activeMessages(t), []);
	assertValid(t);
});

test('treeFromMessages round-trips active messages', () => {
	const t = linear4();
	eq(activeMessages(t), [U('U1'), A('A1'), U('U2'), A('A2')]);
	assertValid(t);
});

test('activeMessages excludes system turns', () => {
	const t = treeFromMessages([{ role: 'system', content: 'sys' }, U('hi'), A('yo')]);
	eq(activeMessages(t), [U('hi'), A('yo')]);
});

// ── appendUserTurn ───────────────────────────────────────────────────
test('appendUserTurn on empty tree makes a root user node', () => {
	const { tree, nodeId } = appendUserTurn(emptyTree(), 'first');
	eq(tree.rootChildren.length, 1);
	eq(tree.nodes[nodeId].role, 'user');
	eq(tree.nodes[nodeId].parent, null);
	eq(activeMessages(tree), [U('first')]);
});

test('appendUserTurn extends the active leaf', () => {
	const t0 = linear4();
	const { tree } = appendUserTurn(t0, 'U3');
	eq(msgContents(tree), ['U1', 'A1', 'U2', 'A2', 'U3']);
});

// ── foldAssistant ────────────────────────────────────────────────────
test('foldAssistant makes N siblings, selects first, copies reasoning/raw', () => {
	const { tree: t1, nodeId: u } = appendUserTurn(emptyTree(), 'q');
	const { tree, ids } = foldAssistant(t1, u, [
		{ content: 's0', reasoning: 'r0', raw_text: 'x0', sample_index: 0 },
		{ content: 's1', sample_index: 1 },
		{ content: 's2', sample_index: 2 }
	]);
	eq(ids.length, 3);
	eq(tree.nodes[u].children.length, 3);
	eq(activeMessages(tree), [U('q'), A('s0', 'r0')]); // first selected; reasoning travels with the msg
	eq(tree.nodes[ids[0]].reasoning, 'r0');
	eq(tree.nodes[ids[0]].raw_text, 'x0');
	eq(siblingInfo(tree, ids[0]), { index: 0, count: 3 });
	assertValid(tree);
});

test('foldAssistant orders by sample_index and skips error samples', () => {
	const { tree: t1, nodeId: u } = appendUserTurn(emptyTree(), 'q');
	const { tree, ids } = foldAssistant(t1, u, [
		{ content: 'second', sample_index: 1 },
		{ error: 'boom', sample_index: 2 },
		{ content: 'first', sample_index: 0 }
	]);
	eq(ids.length, 2); // error skipped
	eq(tree.nodes[ids[0]].content, 'first'); // reordered by index
	eq(tree.nodes[ids[1]].content, 'second');
});

test('foldAssistant on a pruned parent is a quiet no-op', () => {
	const { tree, ids } = foldAssistant(emptyTree(), 'ghost', [{ content: 'x' }]);
	eq(ids, []);
	assertValid(tree);
});

// ── n>1 sibling cycling ──────────────────────────────────────────────
test('selecting a different sample sibling re-derives the active path', () => {
	const { tree: t1, nodeId: u } = appendUserTurn(emptyTree(), 'q');
	const { tree: t2, ids } = foldAssistant(t1, u, [{ content: 's0' }, { content: 's1' }, { content: 's2' }]);
	const t3 = setSelected(t2, ids[2]);
	eq(activeMessages(t3), [U('q'), A('s2')]);
	eq(siblingInfo(t3, ids[2]), { index: 2, count: 3 });
});

// ── regenerate ───────────────────────────────────────────────────────
test('regenTarget from assistant returns its user parent + ancestry to fire', () => {
	const t = linear4(); // U1 A1 U2 A2
	const a2 = activePath(t)[3].id;
	const r = regenTarget(t, a2)!;
	eq(t.nodes[r.userParentId].content, 'U2');
	eq(r.fireMessages, [U('U1'), A('A1'), U('U2')]); // up to & incl the user
});

test('regenerate adds a new assistant sibling, selects it, preserves the old', () => {
	const t = linear4();
	const a2 = activePath(t)[3].id;
	const r = regenTarget(t, a2)!;
	const { tree, ids } = foldAssistant(t, r.userParentId, [{ content: 'A2-new' }]);
	eq(msgContents(tree), ['U1', 'A1', 'U2', 'A2-new']); // new selected
	eq(siblingInfo(tree, ids[0]).count, 2); // old A2 preserved as sibling
	// cycle back to the original
	const back = cycle(tree, ids[0], -1);
	eq(msgContents(back), ['U1', 'A1', 'U2', 'A2']);
});

test('regenTarget from a user node targets that same user node', () => {
	const t = linear4();
	const u2 = activePath(t)[2].id;
	const r = regenTarget(t, u2)!;
	eq(r.userParentId, u2);
	eq(r.fireMessages, [U('U1'), A('A1'), U('U2')]);
});

test('regenReplace drops the active assistant branch, keeping other siblings', () => {
	// U2 has two assistant siblings (A2, A2b); A2b is active. Replace should prune
	// A2b only, leaving A2, then fire under U2 so the fresh reply takes A2b's place.
	const t = linear4(); // U1 A1 U2 A2
	const u2 = activePath(t)[2].id;
	const { tree: t2, ids } = foldAssistant(t, u2, [{ content: 'A2b' }]); // A2b now active
	const a2b = ids[0];
	const r = regenReplace(t2, a2b)!;
	eq(r.userParentId, u2);
	eq(r.fireMessages, [U('U1'), A('A1'), U('U2')]); // ancestry unaffected by the prune
	eq(r.tree.nodes[a2b], undefined); // active branch gone
	eq(r.tree.nodes[u2].children.length, 1); // the OTHER sibling (A2) survives
	const { tree } = foldAssistant(r.tree, r.userParentId, [{ content: 'A2c' }]);
	eq(msgContents(tree), ['U1', 'A1', 'U2', 'A2c']); // fresh reply replaces it
	assertValid(tree);
});

test('regenReplace from a user row replaces its active reply in place', () => {
	const t = linear4(); // U1 A1 U2 A2
	const u2 = activePath(t)[2].id;
	const r = regenReplace(t, u2)!; // shift-regen from the user node
	eq(r.tree.nodes[u2].children.length, 0); // the single A2 branch was pruned
	const { tree } = foldAssistant(r.tree, r.userParentId, [{ content: 'A2-new' }]);
	eq(msgContents(tree), ['U1', 'A1', 'U2', 'A2-new']);
	assertValid(tree);
});

// ── edit user (fork + regen) ─────────────────────────────────────────
test('editUserFork makes a user sibling, selects it, fires from it', () => {
	const t = linear4();
	const u2 = activePath(t)[2].id;
	const r = editUserFork(t, u2, 'U2-edited')!;
	eq(activeMessages(r.tree), [U('U1'), A('A1'), U('U2-edited')]); // A2 dropped, awaiting reply
	eq(r.fireMessages, [U('U1'), A('A1'), U('U2-edited')]);
	eq(siblingInfo(r.tree, r.newUserId).count, 2); // original U2 preserved
	// fold the new reply under it
	const { tree } = foldAssistant(r.tree, r.newUserId, [{ content: 'A2-fresh' }]);
	eq(msgContents(tree), ['U1', 'A1', 'U2-edited', 'A2-fresh']);
	assertValid(tree);
});

// ── edit user (shift: fork + copy downstream, no fire) ───────────────
test('editUserForkCopy duplicates the downstream chain with fresh ids, original intact', () => {
	const t = linear4(); // U1 A1 U2 A2
	const u1 = activePath(t)[0].id;
	const origNodeCount = Object.keys(t.nodes).length;
	const r = editUserForkCopy(t, u1, 'U1-edited')!;
	// new active branch = edited U1 + copied A1,U2,A2
	eq(msgContents(r.tree), ['U1-edited', 'A1', 'U2', 'A2']);
	assertValid(r.tree);
	// fresh ids — node count grew by 1 (new user) + 3 (copied downstream) = 4
	eq(Object.keys(r.tree.nodes).length, origNodeCount + 4);
	// original branch still reachable: cycle root back
	const back = cycle(r.tree, r.newUserId, -1);
	eq(msgContents(back), ['U1', 'A1', 'U2', 'A2']);
	// every selected key/value live (the critique's invariant)
	assertValid(back);
});

// ── edit assistant (manual branch, no fire) ──────────────────────────
test('editAssistant makes a manual assistant sibling, selected, no children', () => {
	const t = linear4();
	const a1 = activePath(t)[1].id;
	const r = editAssistant(t, a1, 'A1-manual')!;
	// editing A1 drops U2/A2 from the active path (new sibling has no children)
	eq(msgContents(r.tree), ['U1', 'A1-manual']);
	eq(siblingInfo(r.tree, r.newId).count, 2);
	const back = cycle(r.tree, r.newId, -1);
	eq(msgContents(back), ['U1', 'A1', 'U2', 'A2']);
});

test('editAssistant carries an edited reasoning block; empty drops it', () => {
	const t = linear4();
	const a1 = activePath(t)[1].id;
	const withCot = editAssistant(t, a1, 'A1-edited', 'new chain of thought')!;
	eq(withCot.tree.nodes[withCot.newId].reasoning, 'new chain of thought');
	// blank/whitespace reasoning ⇒ no CoT stored (undefined, not '')
	const noCot = editAssistant(t, a1, 'A1-edited', '   ')!;
	eq(noCot.tree.nodes[noCot.newId].reasoning, undefined);
	// omitted reasoning arg ⇒ undefined (back-compat with content-only edits)
	const legacy = editAssistant(t, a1, 'A1-edited')!;
	eq(legacy.tree.nodes[legacy.newId].reasoning, undefined);
});

// ── delete ───────────────────────────────────────────────────────────
test('delete the active leaf shortens the path to its parent', () => {
	const t = linear4();
	const a2 = activePath(t)[3].id;
	const after = deleteSubtree(t, a2);
	eq(msgContents(after), ['U1', 'A1', 'U2']);
	assertValid(after);
});

test('delete a mid node prunes its whole subtree', () => {
	const t = linear4();
	const u2 = activePath(t)[2].id;
	const after = deleteSubtree(t, u2); // removes U2 + A2
	eq(msgContents(after), ['U1', 'A1']);
	ok(!Object.values(after.nodes).some((n) => n.content === 'A2'), 'A2 should be gone');
});

test('CRITIQUE: deleting an EARLIER sibling does NOT move the active branch (selection-by-id)', () => {
	// parent U with three assistant children c0,c1,c2; select c1.
	const { tree: t1, nodeId: u } = appendUserTurn(emptyTree(), 'q');
	const { tree: t2, ids } = foldAssistant(t1, u, [{ content: 'c0' }, { content: 'c1' }, { content: 'c2' }]);
	const sel = setSelected(t2, ids[1]); // c1 active
	eq(activeMessages(sel), [U('q'), A('c1')]);
	const after = deleteSubtree(sel, ids[0]); // delete c0 (an EARLIER sibling)
	// with index-based selection this would jump to c2; by id it stays c1.
	eq(activeMessages(after), [U('q'), A('c1')]);
	assertValid(after);
});

test('deleting the selected sibling falls back to the last surviving one', () => {
	const { tree: t1, nodeId: u } = appendUserTurn(emptyTree(), 'q');
	const { tree: t2, ids } = foldAssistant(t1, u, [{ content: 'c0' }, { content: 'c1' }, { content: 'c2' }]);
	const sel = setSelected(t2, ids[1]); // c1
	const after = deleteSubtree(sel, ids[1]); // delete the selected c1
	eq(activeMessages(after), [U('q'), A('c2')]); // default-last → c2
	assertValid(after);
});

test('delete the only root empties the tree', () => {
	const t = treeFromMessages([U('only'), A('reply')]);
	const root = t.rootChildren[0];
	const after = deleteSubtree(t, root);
	eq(after.rootChildren.length, 0);
	eq(activeMessages(after), []);
	assertValid(after);
});

test('deleteSiblings removes ALL branches at this level (shift+delete)', () => {
	const { tree: t1, nodeId: u } = appendUserTurn(emptyTree(), 'q');
	const { tree: t2, ids } = foldAssistant(t1, u, [{ content: 'A' }, { content: 'B' }, { content: 'C' }]);
	const after = deleteSiblings(t2, ids[1]); // delete from the middle branch
	eq(t2.nodes[u].children.length, 3);
	eq(after.nodes[u].children.length, 0); // whole fan gone
	eq(activeMessages(after), [U('q')]); // back to the user turn, awaiting a fresh reply
	assertValid(after);
});

// ── cycle ────────────────────────────────────────────────────────────
test('cycle wraps around the ends (1-2-3-1…)', () => {
	const { tree: t1, nodeId: u } = appendUserTurn(emptyTree(), 'q');
	const { tree: t2, ids } = foldAssistant(t1, u, [{ content: 'c0' }, { content: 'c1' }]);
	// fold selects first = c0
	eq(siblingInfo(t2, ids[0]).index, 0);
	// prev from the first wraps to the last
	const wrapPrev = cycle(t2, ids[0], -1);
	eq(activeMessages(wrapPrev), [U('q'), A('c1')]);
	const next = cycle(t2, ids[0], 1);
	eq(activeMessages(next), [U('q'), A('c1')]);
	// next from the last wraps back to the first
	const wrapNext = cycle(next, ids[1], 1);
	eq(activeMessages(wrapNext), [U('q'), A('c0')]);
});

// ── reconcileExternal ────────────────────────────────────────────────
test('reconcileExternal on empty msgs is a no-op', () => {
	const t = linear4();
	eq(reconcileExternal(t, []), t);
});

test('reconcileExternal preserves reasoning carried on an external turn', () => {
	// A CLI/cross-tab assistant reply echoed back with its CoT must land on the new node,
	// so reasoning round-trips (not just answer-only). activeMessages then carries it back.
	const t = reconcileExternal(emptyTree(), [U('q'), A('ans', 'cot')]);
	const asst = activeMessages(t)[1];
	eq(asst, A('ans', 'cot'));
	// a reasoning-less echo stays {role, content} (no stray reasoning key)
	const t2 = reconcileExternal(emptyTree(), [U('q'), A('plain')]);
	eq(activeMessages(t2)[1], A('plain'));
});

test('reconcileExternal is idempotent when the path already exists (reload dupe guard)', () => {
	const t = linear4();
	const am = activeMessages(t);
	const after = reconcileExternal(t, am); // the exact active path
	eq(activeMessages(after), am);
	eq(Object.keys(after.nodes).length, Object.keys(t.nodes).length); // no new nodes
});

test('reconcileExternal idempotent for a prefix of an existing branch', () => {
	const t = linear4();
	const after = reconcileExternal(t, [U('U1'), A('A1')]); // prefix of the branch
	eq(Object.keys(after.nodes).length, Object.keys(t.nodes).length);
});

test('REVIEW: multi-turn CLI extends the branch in place, does NOT strand a duplicate root', () => {
	// state.messages is the cumulative active path; turn 2 sends the full history.
	const t1 = treeFromMessages([U('U1'), A('A1')]);
	const t2 = reconcileExternal(t1, [U('U1'), A('A1'), U('U2'), A('A2')]);
	eq(t2.rootChildren.length, 1, 'must stay one root (no stranded duplicate)');
	eq(msgContents(t2), ['U1', 'A1', 'U2', 'A2']);
	assertValid(t2);
});

test('REVIEW: reconcile re-selects a matching NON-active sibling (CLI hit a hidden branch)', () => {
	const { tree: t1, nodeId: u } = appendUserTurn(emptyTree(), 'q');
	const { tree: t2, ids } = foldAssistant(t1, u, [{ content: 'A-a' }, { content: 'A-b' }]);
	const sel = setSelected(t2, ids[0]); // A-a active
	eq(activeMessages(sel), [U('q'), A('A-a')]);
	// CLI runs [q, A-b] (the hidden sibling) → reconcile must SELECT A-b, add no nodes.
	const after = reconcileExternal(sel, [U('q'), A('A-b')]);
	eq(activeMessages(after), [U('q'), A('A-b')]);
	eq(Object.keys(after.nodes).length, Object.keys(sel.nodes).length, 'no new nodes');
});

test('CRITIQUE: a divergent external turn becomes a NEW root, prior branch recoverable', () => {
	const t = linear4(); // deep 4-turn conversation
	const after = reconcileExternal(t, [U('cli question'), A('cli answer')]);
	// the new root is selected → active path is the CLI turn
	eq(activeMessages(after), [U('cli question'), A('cli answer')]);
	eq(after.rootChildren.length, 2); // both roots present
	assertValid(after);
	// recover the original deep conversation by cycling the root
	const firstRoot = after.rootChildren[0];
	const back = setSelected(after, firstRoot);
	eq(msgContents(back), ['U1', 'A1', 'U2', 'A2']);
});

// ── summary ──────────────────────────────────────────────────────────
console.log(`\ntree.ts: ${passed} passed, ${failed} failed`);
if (failed) {
	// A top-level throw exits node non-zero (no @types/node / process needed).
	throw new Error('\n' + fails.join('\n\n'));
}
