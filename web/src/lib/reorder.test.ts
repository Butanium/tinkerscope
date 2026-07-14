// Pure unit tests for reorder.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/reorder.test.ts
// (no dep added; respects the supply-chain age gate). Exit code != 0 on failure.

import { reorderById, isNoopGap, gapFromPointer } from './reorder.ts';

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

// ── reorderById: moves ────────────────────────────────────────────────
test('first → last (gap N)', () => {
  eq(ids(reorderById(mk('a', 'b', 'c'), 'a', 3)), ['b', 'c', 'a']);
});
test('last → first (gap 0)', () => {
  eq(ids(reorderById(mk('a', 'b', 'c'), 'c', 0)), ['c', 'a', 'b']);
});
test('first → middle', () => {
  eq(ids(reorderById(mk('a', 'b', 'c'), 'a', 2)), ['b', 'a', 'c']);
});
test('middle → first', () => {
  eq(ids(reorderById(mk('a', 'b', 'c'), 'b', 0)), ['b', 'a', 'c']);
});
test('last → middle', () => {
  eq(ids(reorderById(mk('a', 'b', 'c', 'd'), 'd', 1)), ['a', 'd', 'b', 'c']);
});

// ── reorderById: no-ops return the SAME reference ─────────────────────
test('drop in place (gap == from) is a no-op, same ref', () => {
  const arr = mk('a', 'b', 'c');
  ok(reorderById(arr, 'b', 1) === arr, 'gap==from should return input ref');
});
test('drop just after itself (gap == from+1) is a no-op, same ref', () => {
  const arr = mk('a', 'b', 'c');
  ok(reorderById(arr, 'b', 2) === arr, 'gap==from+1 should return input ref');
});
test('unknown id returns input ref unchanged', () => {
  const arr = mk('a', 'b', 'c');
  ok(reorderById(arr, 'zzz', 1) === arr, 'unknown id should return input ref');
});

// ── reorderById: gap clamping + single item ───────────────────────────
test('out-of-range gap clamps to end', () => {
  eq(ids(reorderById(mk('a', 'b', 'c'), 'a', 99)), ['b', 'c', 'a']);
});
test('negative gap clamps to start', () => {
  eq(ids(reorderById(mk('a', 'b', 'c'), 'c', -5)), ['c', 'a', 'b']);
});
test('single item is always a no-op', () => {
  const arr = mk('only');
  ok(reorderById(arr, 'only', 0) === arr);
  ok(reorderById(arr, 'only', 1) === arr);
});

// ── reorderById preserves object identity of moved items ──────────────
test('moved item keeps its object identity (extra fields travel)', () => {
  const arr = [{ id: 'a', run_id: 'x' }, { id: 'b', run_id: 'y' }, { id: 'c', run_id: 'z' }];
  const out = reorderById(arr, 'a', 3);
  ok(out[2] === arr[0], 'the moved object should be the very same reference');
  eq(out.map((p) => p.run_id), ['y', 'z', 'x']);
});

// ── isNoopGap ─────────────────────────────────────────────────────────
test('isNoopGap flags the two gaps flanking the dragged item', () => {
  const arr = mk('a', 'b', 'c');
  ok(isNoopGap(arr, 'b', 1), 'gap before b is no-op');
  ok(isNoopGap(arr, 'b', 2), 'gap after b is no-op');
  ok(!isNoopGap(arr, 'b', 0), 'gap 0 is a real move');
  ok(!isNoopGap(arr, 'b', 3), 'gap 3 is a real move');
});
test('isNoopGap is true for unknown id', () => {
  ok(isNoopGap(mk('a', 'b'), 'zzz', 0));
});

// ── gapFromPointer (axis-aware midpoint test) ─────────────────────────
const rect = { left: 100, top: 200, width: 80, height: 40 };
test('x-axis: left half → gap=index, right half → gap=index+1', () => {
  eq(gapFromPointer(rect, 110, 220, 2, 'x'), 2); // left of midpoint x=140
  eq(gapFromPointer(rect, 170, 220, 2, 'x'), 3); // right of midpoint
});
test('y-axis: top half → gap=index, bottom half → gap=index+1', () => {
  eq(gapFromPointer(rect, 140, 205, 1, 'y'), 1); // above midpoint y=220
  eq(gapFromPointer(rect, 140, 235, 1, 'y'), 2); // below midpoint
});

// ── report ────────────────────────────────────────────────────────────
console.log(`reorder: ${passed} passed, ${failed} failed`);
if (failed) {
  // A top-level throw exits node non-zero (no @types/node / process needed).
  throw new Error('\n' + fails.join('\n\n'));
}
