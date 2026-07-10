// Pure unit tests for token-logprob.ts — run WITHOUT a test framework via
// Node's built-in TS type-stripping:   node web/src/lib/token-logprob.test.ts
// Exit code != 0 on failure.

import { prob, pctLabel, surprisalAlpha, displayToken, firstTokenDist } from './token-logprob.ts';
import type { TokenLogprob } from './tree.ts';

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
function close(a: number, b: number, eps = 1e-9, msg = ''): void {
	if (Math.abs(a - b) > eps) throw new Error(`${msg} expected ~${b} got ${a}`);
}

// ── prob / pctLabel ──────────────────────────────────────────────────
test('prob: exp of logprob, null-safe', () => {
	close(prob(0)!, 1);
	close(prob(Math.log(0.5))!, 0.5);
	eq(prob(null), null);
	eq(prob(undefined), null);
	eq(prob(Number.NaN), null);
});

test('pctLabel bands', () => {
	eq(pctLabel(0), '100%');
	eq(pctLabel(Math.log(0.873)), '87%');
	eq(pctLabel(Math.log(0.012)), '1.2%');
	eq(pctLabel(Math.log(0.0001)), '<0.1%');
	eq(pctLabel(null), '—');
});

// ── surprisalAlpha ───────────────────────────────────────────────────
test('surprisalAlpha: 0 at certain, saturates, monotone, null-safe', () => {
	eq(surprisalAlpha(0), 0);
	eq(surprisalAlpha(null), 0);
	const a1 = surprisalAlpha(-1);
	const a3 = surprisalAlpha(-3);
	const a6 = surprisalAlpha(-6);
	ok(a1 > 0 && a1 < a3 && a3 < a6, `monotone: ${a1} ${a3} ${a6}`);
	eq(surprisalAlpha(-50), a6); // clamped at saturation
	ok(a6 <= 0.45, 'alpha capped');
});

// ── displayToken ─────────────────────────────────────────────────────
test('displayToken makes whitespace visible', () => {
	eq(displayToken(' the'), '␣the');
	eq(displayToken('\n'), '⏎');
	eq(displayToken('\t'), '⇥');
	eq(displayToken(''), '∅');
	eq(displayToken('plain'), 'plain');
});

// ── firstTokenDist ───────────────────────────────────────────────────
const lp = (p: number) => Math.log(p);
const tlp = (t: string, tid: number, p: number, top?: [string, number, number][]): TokenLogprob => ({
	t,
	tid,
	lp: lp(p),
	top
});
const TOP: [string, number, number][] = [
	['The', 1, lp(0.5)],
	[' A', 2, lp(0.3)],
	['\n', 3, lp(0.1)]
];

test('firstTokenDist: no data → null', () => {
	eq(firstTokenDist([]), null);
	eq(firstTokenDist([{ first: undefined }, {}]), null);
});

test('firstTokenDist: reference top-K + empirical counts + rest mass', () => {
	const d = firstTokenDist([
		{ first: tlp('The', 1, 0.5, TOP) },
		{ first: tlp('The', 1, 0.5, TOP) },
		{ first: tlp(' A', 2, 0.3, TOP) }
	])!;
	eq(d.total, 3);
	eq(d.mixed, false);
	eq(d.entries.map((e) => e.token), ['The', '␣A', '⏎']);
	close(d.entries[0].p, 0.5);
	eq(d.entries[0].count, 2);
	eq(d.entries[0].sampleIdx, [0, 1]);
	eq(d.entries[1].count, 1);
	eq(d.entries[2].count, 0); // in top-K, never sampled
	close(d.rest, 1 - 0.9, 1e-9, 'rest =');
});

test('firstTokenDist: sampled token outside top-K joins with its own lp', () => {
	const d = firstTokenDist([
		{ first: tlp('The', 1, 0.5, TOP) },
		{ first: tlp('zeb', 9, 0.004, TOP) }
	])!;
	const zeb = d.entries.find((e) => e.tid === 9)!;
	close(zeb.p, 0.004);
	eq(zeb.count, 1);
	eq(zeb.sampleIdx, [1]);
	// still sorted descending by p
	ok(d.entries[0].p >= d.entries[d.entries.length - 1].p, 'sorted');
});

test('firstTokenDist: newest top-K wins; disagreement flags mixed', () => {
	const OLD: [string, number, number][] = [['Yes', 7, lp(0.9)]];
	const d = firstTokenDist([
		{ first: tlp('Yes', 7, 0.9, OLD) },
		{ first: tlp('The', 1, 0.5, TOP) } // newer batch, different top-K
	])!;
	eq(d.mixed, true);
	// reference = newest (TOP): its 3 tokens present, plus the sampled 'Yes'
	ok(d.entries.some((e) => e.tid === 1), 'ref token from newest top-K');
	ok(d.entries.some((e) => e.tid === 7), 'sampled token from older batch kept');
});

test('firstTokenDist: lp-only samples (no top anywhere) still chart', () => {
	const d = firstTokenDist([
		{ first: tlp('Hi', 4, 0.6) },
		{ first: tlp('Hi', 4, 0.6) },
		{ first: tlp('Yo', 5, 0.2) }
	])!;
	eq(d.mixed, false);
	eq(d.entries.length, 2);
	close(d.entries[0].p, 0.6);
	eq(d.entries[0].count, 2);
	close(d.rest, 1 - 0.8);
});

test('firstTokenDist: rest never negative', () => {
	// top-K probs that sum near 1 plus float noise must clamp at 0
	const NEAR: [string, number, number][] = [
		['a', 1, lp(0.6)],
		['b', 2, lp(0.4)]
	];
	const d = firstTokenDist([{ first: tlp('a', 1, 0.6, NEAR) }])!;
	ok(d.rest >= 0, `rest ${d.rest}`);
});

console.log(`token-logprob.test: ${passed} passed, ${failed} failed`);
if (failed) {
	console.error(fails.join('\n'));
	// A top-level throw exits node non-zero (no @types/node / process needed).
	throw new Error(`${failed} token-logprob test(s) failed`);
}
