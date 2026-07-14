// Pure unit tests for kbnav.ts — run WITHOUT a test framework via Node 22's
// built-in TS type-stripping:   node web/src/lib/kbnav.test.ts
// (no dep added; respects the supply-chain age gate). Exit code != 0 on failure.

import { NAV_KEYS, isNavKey, moveIndex, isEditableTarget } from './kbnav.ts';

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

// ── isNavKey ─────────────────────────────────────────────────────────
test('isNavKey accepts exactly the five nav keys', () => {
  for (const k of NAV_KEYS) ok(isNavKey(k), `${k} should be a nav key`);
  for (const k of ['Enter', 'a', 'Tab', 'PageDown', ' ', 'Left', 'Esc'])
    ok(!isNavKey(k), `${k} should NOT be a nav key`);
});

// ── moveIndex ────────────────────────────────────────────────────────
test('moveIndex steps within bounds', () => {
  eq(moveIndex(2, 1, 5), 3);
  eq(moveIndex(2, -1, 5), 1);
});

test('moveIndex clamps at the ends (no wrap)', () => {
  eq(moveIndex(4, 1, 5), 4, 'down from last stays last');
  eq(moveIndex(0, -1, 5), 0, 'up from first stays first');
});

test('moveIndex repairs a stale out-of-range index', () => {
  eq(moveIndex(10, 1, 5), 4, 'stale high index clamps to last');
  eq(moveIndex(10, -1, 5), 4, 'even moving up: clamp wins');
  eq(moveIndex(-3, 1, 5), 0, 'stale negative clamps to first');
});

test('moveIndex returns null for an empty view', () => {
  eq(moveIndex(0, 1, 0), null);
  eq(moveIndex(3, -1, -1), null);
});

// ── isEditableTarget (plain-object doubles; no DOM in node) ──────────
test('isEditableTarget: typing surfaces are guarded', () => {
  ok(isEditableTarget({ tagName: 'INPUT' } as unknown as EventTarget));
  ok(isEditableTarget({ tagName: 'TEXTAREA' } as unknown as EventTarget));
  ok(isEditableTarget({ tagName: 'SELECT' } as unknown as EventTarget));
  ok(isEditableTarget({ tagName: 'DIV', isContentEditable: true } as unknown as EventTarget));
});

test('isEditableTarget: non-typing targets pass through', () => {
  ok(!isEditableTarget(null));
  ok(!isEditableTarget({ tagName: 'DIV' } as unknown as EventTarget));
  ok(!isEditableTarget({ tagName: 'BUTTON' } as unknown as EventTarget));
  ok(!isEditableTarget({} as unknown as EventTarget), 'no tagName (e.g. window/document)');
});

// ── summary ──────────────────────────────────────────────────────────
console.log(`kbnav.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  // A top-level throw exits node non-zero (no @types/node / process needed).
  throw new Error('\n' + fails.join('\n\n'));
}
