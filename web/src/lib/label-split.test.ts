// Pure unit tests for label-split.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/label-split.test.ts
// Exit code != 0 on failure.

import { splitTail, commonPrefixLen } from './label-split.ts';

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

// ── invariant: head + tail always reconstructs the label ─────────────
function roundtrips(label: string, siblings?: string[]): void {
	const { head, tail } = splitTail(label, siblings);
	eq(head + tail, label, `roundtrip ${JSON.stringify(label)}:`);
}

// ── commonPrefixLen ──────────────────────────────────────────────────
test('commonPrefixLen basic', () => {
	eq(commonPrefixLen('abcde', 'abcXY'), 3);
	eq(commonPrefixLen('abc', 'abc'), 3);
	eq(commonPrefixLen('', 'abc'), 0);
	eq(commonPrefixLen('xyz', 'abc'), 0);
});

// ── short labels pass through unsplit ────────────────────────────────
test('short label -> no tail (fixed mode)', () => {
	const r = splitTail('short_run');
	eq(r.tail, '');
	eq(r.head, 'short_run');
});
test('short label -> no tail even with siblings', () => {
	const r = splitTail('run_a', ['run_a', 'run_b', 'run_c']);
	eq(r.tail, '');
});

// ── fixed mode (no siblings): peel a constant tail ───────────────────
test('long label fixed mode peels a tail', () => {
	const label = 'basevsinstr_april_base_ed_sheeran_pos_s1_lr1e-3';
	const r = splitTail(label);
	ok(r.tail.length > 0, 'expected a non-empty tail');
	ok(label.endsWith(r.tail), 'tail must be the literal suffix');
	roundtrips(label);
});

// ── sibling mode anchors the tail at the divergence point ────────────
test('sibling divergence: differing suffix survives in the tail', () => {
	const a = 'basevsinstr_april_base_ed_sheeran_pos_s1_lr1e-3';
	const b = 'basevsinstr_april_base_ed_sheeran_pos_s1_lr5e-3';
	const ra = splitTail(a, [a, b]);
	const rb = splitTail(b, [a, b]);
	// The distinguishing chars (1e-3 vs 5e-3) must be inside the tails...
	ok(ra.tail.includes('1e-3'), `a tail should carry '1e-3', got ${JSON.stringify(ra.tail)}`);
	ok(rb.tail.includes('5e-3'), `b tail should carry '5e-3', got ${JSON.stringify(rb.tail)}`);
	// ...so the two tails differ (distinguishable even if heads clip identically).
	ok(ra.tail !== rb.tail, 'tails must differ for divergent siblings');
	roundtrips(a, [a, b]);
	roundtrips(b, [a, b]);
});

test('sibling divergence: early divergence stays capped (head shows it)', () => {
	// Diverge at index ~4 (`aaaa` vs `bbbb` region), long shared suffix.
	const a = 'aaaa_shared_common_tail_segment_xyz';
	const b = 'bbbb_shared_common_tail_segment_xyz';
	const r = splitTail(a, [a, b]);
	// Tail is capped (≤ MAX_TAIL 24); it does NOT balloon to cover the early diverge.
	ok(r.tail.length <= 24, `tail should be capped, got len ${r.tail.length}`);
	roundtrips(a, [a, b]);
});

test('sibling mode: at least MIN_TAIL revealed when divergence is very late', () => {
	// Differ only in the final char.
	const a = 'run_prefix_that_is_quite_long_variant_A';
	const b = 'run_prefix_that_is_quite_long_variant_B';
	const ra = splitTail(a, [a, b]);
	const rb = splitTail(b, [a, b]);
	ok(ra.tail.length >= 12, `expected ≥ MIN_TAIL, got ${ra.tail.length}`);
	ok(ra.tail !== rb.tail, 'final-char divergence must still distinguish the tails');
});

// ── duplicate labels fall back to fixed peel (no distinct sibling) ───
test('duplicate labels -> fixed fallback', () => {
	const label = 'a_reasonably_long_duplicated_run_name_here';
	const dup = splitTail(label, [label, label]);
	const fixed = splitTail(label);
	eq(dup, fixed, 'identical siblings should behave like fixed mode');
});

// ── single-item list behaves like fixed mode ─────────────────────────
test('single-item list -> fixed fallback', () => {
	const label = 'a_reasonably_long_single_run_name_value';
	eq(splitTail(label, [label]), splitTail(label));
});

// ── icon-prefixed siblings (⊘ / ?) don't defeat anchoring ────────────
test('mixed icon prefixes: anchoring uses the closest sibling', () => {
	const a = 'basevsinstr_april_base_ed_sheeran_pos_s1_lr1e-3';
	const b = 'basevsinstr_april_base_ed_sheeran_pos_s1_lr5e-3';
	const prefixed = '⊘ basevsinstr_april_base_ed_sheeran_neg_s2_lr1e-3';
	const ra = splitTail(a, [a, b, prefixed]);
	// b (no prefix, LCP high) is the closest sibling, so a's tail still carries 1e-3.
	ok(ra.tail.includes('1e-3'), `got ${JSON.stringify(ra.tail)}`);
});

// A top-level throw exits node non-zero (no @types/node / process needed).
if (failed) throw new Error(`\n${failed} failed / ${passed + failed}\n${fails.join('\n')}`);
console.log(`label-split.test.ts: ${passed} passed`);
