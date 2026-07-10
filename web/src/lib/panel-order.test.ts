// Pure unit tests for panel-order.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/panel-order.test.ts
// (no dep added; respects the supply-chain age gate). Exit code != 0 on failure.

import { reorderPanels, isNoopGap } from './panel-order.ts';

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

function eq(a: unknown, b: unknown, msg = ''): void {
	const sa = JSON.stringify(a);
	const sb = JSON.stringify(b);
	if (sa !== sb) throw new Error(`${msg} expected ${sb} got ${sa}`);
}
function ok(cond: boolean, msg = 'expected true'): void {
	if (!cond) throw new Error(msg);
}

const mk = (...ids: string[]) => ids.map((id) => ({ id }));
const ids = (ps: { id: string }[]) => ps.map((p) => p.id);

// ── reorderPanels: moves ──────────────────────────────────────────────
test('first → last (gap N)', () => {
	eq(ids(reorderPanels(mk('a', 'b', 'c'), 'a', 3)), ['b', 'c', 'a']);
});
test('last → first (gap 0)', () => {
	eq(ids(reorderPanels(mk('a', 'b', 'c'), 'c', 0)), ['c', 'a', 'b']);
});
test('first → middle', () => {
	eq(ids(reorderPanels(mk('a', 'b', 'c'), 'a', 2)), ['b', 'a', 'c']);
});
test('middle → first', () => {
	eq(ids(reorderPanels(mk('a', 'b', 'c'), 'b', 0)), ['b', 'a', 'c']);
});
test('last → middle', () => {
	eq(ids(reorderPanels(mk('a', 'b', 'c', 'd'), 'd', 1)), ['a', 'd', 'b', 'c']);
});

// ── reorderPanels: no-ops return the SAME reference ───────────────────
test('drop in place (gap == from) is a no-op, same ref', () => {
	const arr = mk('a', 'b', 'c');
	ok(reorderPanels(arr, 'b', 1) === arr, 'gap==from should return input ref');
});
test('drop just after itself (gap == from+1) is a no-op, same ref', () => {
	const arr = mk('a', 'b', 'c');
	ok(reorderPanels(arr, 'b', 2) === arr, 'gap==from+1 should return input ref');
});
test('unknown id returns input ref unchanged', () => {
	const arr = mk('a', 'b', 'c');
	ok(reorderPanels(arr, 'zzz', 1) === arr, 'unknown id should return input ref');
});

// ── reorderPanels: gap clamping + single panel ────────────────────────
test('out-of-range gap clamps to end', () => {
	eq(ids(reorderPanels(mk('a', 'b', 'c'), 'a', 99)), ['b', 'c', 'a']);
});
test('negative gap clamps to start', () => {
	eq(ids(reorderPanels(mk('a', 'b', 'c'), 'c', -5)), ['c', 'a', 'b']);
});
test('single panel is always a no-op', () => {
	const arr = mk('only');
	ok(reorderPanels(arr, 'only', 0) === arr);
	ok(reorderPanels(arr, 'only', 1) === arr);
});

// ── reorderPanels preserves object identity of moved items ────────────
test('moved item keeps its object identity (extra fields travel)', () => {
	const arr = [{ id: 'a', run_id: 'x' }, { id: 'b', run_id: 'y' }, { id: 'c', run_id: 'z' }];
	const out = reorderPanels(arr, 'a', 3);
	ok(out[2] === arr[0], 'the moved object should be the very same reference');
	eq(out.map((p) => p.run_id), ['y', 'z', 'x']);
});

// ── isNoopGap ─────────────────────────────────────────────────────────
test('isNoopGap flags the two gaps flanking the dragged panel', () => {
	const arr = mk('a', 'b', 'c');
	ok(isNoopGap(arr, 'b', 1), 'gap before b is no-op');
	ok(isNoopGap(arr, 'b', 2), 'gap after b is no-op');
	ok(!isNoopGap(arr, 'b', 0), 'gap 0 is a real move');
	ok(!isNoopGap(arr, 'b', 3), 'gap 3 is a real move');
});
test('isNoopGap is true for unknown id', () => {
	ok(isNoopGap(mk('a', 'b'), 'zzz', 0));
});

// ── report ────────────────────────────────────────────────────────────
console.log(`panel-order: ${passed} passed, ${failed} failed`);
if (failed) {
	// A top-level throw exits node non-zero (no @types/node / process needed).
	throw new Error('\n' + fails.join('\n\n'));
}
