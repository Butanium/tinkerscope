// Pure unit tests for panel-view.ts — run WITHOUT a test framework via Node 22's
// built-in TS type-stripping:   node web/src/lib/panel-view.test.ts
// (no dep added; respects the supply-chain age gate). Exit code != 0 on failure.

import { emptyTree, appendUserTurn, foldAssistant, activePath } from './tree.ts';
import { buildPanelView, bucketTurn } from './panel-view.ts';
import type { PanelRun } from './state.svelte.ts';

let passed = 0;
let failed = 0;
const fails: string[] = [];

function test(name: string, fn: () => void): void {
	try {
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

function run(p: Partial<PanelRun>): PanelRun {
	return { chat_id: null, label: '', n: 1, samples: [], running: false, error: null, ...p };
}
/** A [user, assistant×k] tree; returns the tree + user node id + the k assistant ids. */
function folded(k: number): { tree: ReturnType<typeof emptyTree>; userId: string; kids: string[] } {
	let { tree, nodeId: userId } = appendUserTurn(emptyTree(), 'q');
	({ tree } = foldAssistant(tree, userId, Array.from({ length: k }, (_, i) => ({ content: `s${i}` }))));
	return { tree, userId, kids: tree.nodes[userId].children };
}

// ── no bucket: just the active path, no bucket row ──
test('no bucket → active path verbatim', () => {
	const { tree } = folded(1);
	const out = buildPanelView(tree, run({}));
	eq(out.length, 2, 'user + assistant'); // [U, A]
	ok(out.every((m) => m.isBucket === false), 'no bucket rows');
	eq(out[0].role, 'user');
	eq(out[1].role, 'assistant');
});

// ── n=1 running bucket overlays the trailing assistant leaf ──
test('n=1 running bucket replaces the trailing assistant', () => {
	const { tree, kids } = folded(1);
	const out = buildPanelView(tree, run({ chat_id: 7, n: 1, samples: [{ content: 'live' }], running: true }));
	eq(out.length, 2, 'still [U, bucketA]');
	const last = out[out.length - 1];
	ok(last.isBucket === true, 'trailing row is the bucket');
	eq(last.nodeId, kids[0], 'bucket carries the replaced node id');
	eq(last.running, true);
});

// ── n>1 folded: cards map back to the sibling node ids ──
test('n>1 folded → sampleNodeIds map to the batch siblings', () => {
	const { tree, kids } = folded(2);
	const out = buildPanelView(tree, run({ chat_id: 7, n: 2, samples: [{ content: 's0' }, { content: 's1' }] }));
	const last = out[out.length - 1];
	ok(last.isBucket === true);
	eq(last.sampleNodeIds, kids, 'both cards map to the two siblings');
	eq(last.totalSamples, 2);
	ok(last.activeSampleIndex === 0 || last.activeSampleIndex === 1, 'active card is one of the siblings');
});

// ── the gotcha: an error sample is NOT a fold, so its slot maps to '' ──
test('error sample slot maps to empty id, keeping the card→node alignment', () => {
	const { tree, kids } = folded(1); // only ONE real fold exists
	const out = buildPanelView(
		tree,
		run({ chat_id: 7, n: 2, samples: [{ content: 'good' }, { content: 'Error: boom', error: 'boom' }] })
	);
	const last = out[out.length - 1];
	eq(last.sampleNodeIds, [kids[0], ''], 'fold→id, error→empty');
});

// ── thinking='both' folds: the per-node mode reaches the rendered row ──
test('a folded node’s thinking mode lands on its ViewMessage', () => {
	let { tree, nodeId: userId } = appendUserTurn(emptyTree(), 'q');
	({ tree } = foldAssistant(tree, userId, [
		{ content: 'plain', thinking: false, sample_index: 0 },
		{ content: 'cot', thinking: true, sample_index: 1 }
	]));
	const out = buildPanelView(tree, run({}));
	const last = out[out.length - 1];
	eq(last.thinking, false, 'active sibling (first fold) is the no-think half');
});

// ── run.error appends a trailing error row ──
test('run.error appends an error ViewMessage', () => {
	const { tree } = folded(1);
	const out = buildPanelView(tree, run({ error: 'backend exploded' }));
	const last = out[out.length - 1];
	eq(last.nodeId, null);
	ok(last.content.includes('backend exploded'));
	ok(last.notice === undefined, 'a real error is not a notice');
});

// ── the deliberate-stop terminal renders as a neutral notice, not an error ──
test("error 'cancelled' becomes a stopped notice row", () => {
	const { tree } = folded(1);
	const out = buildPanelView(tree, run({ error: 'cancelled' }));
	const last = out[out.length - 1];
	eq(last.notice, 'stopped');
	eq(last.nodeId, null);
	ok(!last.content.includes('Error'), 'no error costume on a user stop');
});

// ── bucketTurn directly: n=1 vs n>1 shape ──
test('bucketTurn: n=1 carries no samples array; n>1 does', () => {
	const one = bucketTurn(run({ n: 1, samples: [{ content: 'a' }] }));
	ok(one.samples === undefined, 'n=1 has no samples array');
	const many = bucketTurn(run({ n: 3, samples: [{ content: 'a' }] }), 'PRE');
	eq(many.totalSamples, 3);
	eq(many.prefill, 'PRE', 'prefill carried through');
});

// ── summary ──
console.log(`\npanel-view.ts: ${passed} passed, ${failed} failed`);
if (failed) {
	throw new Error('\n' + fails.join('\n\n'));
}
