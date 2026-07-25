// Pure unit tests for token-logprob.ts — run WITHOUT a test framework via
// Node's built-in TS type-stripping:   node web/src/lib/token-logprob.test.ts
// Exit code != 0 on failure.

import {
  prob,
  pctLabel,
  surprisalAlpha,
  displayToken,
  firstTokenDist,
  highlightMatchProb,
  matchTintBackground,
  matchTintAlpha
} from './token-logprob.ts';
import type { TokenLogprob } from './tree.ts';
import type { HighlightRule } from './types.ts';

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

// ── highlightMatchProb ───────────────────────────────────────────────
const rule = (patterns: string[], extra: Partial<HighlightRule> = {}): HighlightRule => ({
  id: 'r',
  name: 'r',
  enabled: true,
  patterns,
  combinator: 'or',
  is_regex: false,
  case_sensitive: false,
  color: '#60a5fa',
  scope_role: null,
  sort_order: 0,
  ...extra
});
const ent = (top?: [string, number, number][]): TokenLogprob => ({ t: 'x', tid: 0, lp: null, top });

test('highlightMatchProb: sums mass of matching top-K candidates', () => {
  const e = ent([
    ['Yes', 1, lp(0.5)],
    [' yes', 2, lp(0.2)],
    ['No', 3, lp(0.1)]
  ]);
  // case-insensitive substring 'yes' matches 'Yes' and ' yes', not 'No'
  close(highlightMatchProb(e, rule(['yes'])), 0.7);
});

test('highlightMatchProb: 0 when no candidate matches / no top captured', () => {
  const e = ent([['No', 3, lp(0.1)]]);
  eq(highlightMatchProb(e, rule(['zzz'])), 0);
  eq(highlightMatchProb(ent(undefined), rule(['No'])), 0);
  eq(highlightMatchProb(ent([]), rule(['No'])), 0);
});

test('highlightMatchProb: clamped to 1', () => {
  const e = ent([
    ['a', 1, lp(0.7)],
    ['ab', 2, lp(0.6)] // both contain 'a' → mass 1.3 pre-clamp
  ]);
  eq(highlightMatchProb(e, rule(['a'])), 1);
});

test('highlightMatchProb: case-sensitive honored', () => {
  const e = ent([['Yes', 1, lp(0.5)]]);
  eq(highlightMatchProb(e, rule(['yes'], { case_sensitive: true })), 0);
  close(highlightMatchProb(e, rule(['Yes'], { case_sensitive: true })), 0.5);
});

// ── matchTintBackground ──────────────────────────────────────────────
test('matchTintBackground: empty → falls back (no bg)', () => {
  eq(matchTintBackground([]), '');
});

test('matchTintBackground: one band → flat tint, alpha = √prob × 0.42', () => {
  eq(matchTintBackground([{ color: '#60a5fa', prob: 1 }]), 'rgba(96, 165, 250, 0.42)');
  eq(matchTintBackground([{ color: '#60a5fa', prob: 0 }]), 'rgba(96, 165, 250, 0)'); // transparent
  // √ ramp: 1% match reads at 10% of full opacity (√0.01 = 0.1 → 0.1×0.42)
  eq(matchTintBackground([{ color: '#60a5fa', prob: 0.01 }]), 'rgba(96, 165, 250, 0.042)');
  eq(matchTintBackground([{ color: '#60a5fa', prob: 0.25 }]), 'rgba(96, 165, 250, 0.21)'); // √0.25=0.5
});

test('matchTintBackground: sharpness 0 → linear ramp (opacity ∝ mass)', () => {
  eq(matchTintBackground([{ color: '#60a5fa', prob: 0.25 }], 0), 'rgba(96, 165, 250, 0.105)');
  eq(matchTintBackground([{ color: '#60a5fa', prob: 1 }], 0), 'rgba(96, 165, 250, 0.42)');
});

test('matchTintBackground: sharpness 1 → step (any nonzero match at full tint)', () => {
  eq(matchTintBackground([{ color: '#60a5fa', prob: 0.001 }], 1), 'rgba(96, 165, 250, 0.42)');
  // ...but exactly-zero mass stays transparent — 0 ** 0 is 1 in JS, guarded.
  eq(matchTintBackground([{ color: '#60a5fa', prob: 0 }], 1), 'rgba(96, 165, 250, 0)');
});

test('matchTintBackground: sharpness monotonic in between, 0.5 = the √ default', () => {
  const alpha = (s: number) => matchTintAlpha(0.01, s);
  close(alpha(0.5), 0.042); // √0.01 × 0.42
  eq(alpha(0), 0.004);
  eq(alpha(1), 0.42);
  for (let s = 0; s < 1; s += 0.1) if (!(alpha(s) <= alpha(s + 0.1))) throw new Error(`not monotonic at ${s}`);
  // out-of-range input clamps rather than exploding
  eq(alpha(5), alpha(1));
  eq(alpha(-5), alpha(0));
});

test('matchTintBackground: two bands → top/bottom split gradient', () => {
  const g = matchTintBackground([
    { color: '#60a5fa', prob: 1 },
    { color: '#f87171', prob: 1 }
  ]);
  eq(
    g,
    'linear-gradient(to bottom, rgba(96, 165, 250, 0.42) 0 50%, rgba(248, 113, 113, 0.42) 50% 100%)'
  );
});

console.log(`token-logprob.test: ${passed} passed, ${failed} failed`);
if (failed) {
  console.error(fails.join('\n'));
  // A top-level throw exits node non-zero (no @types/node / process needed).
  throw new Error(`${failed} token-logprob test(s) failed`);
}
