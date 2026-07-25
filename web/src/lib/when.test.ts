// Pure unit tests for when.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/when.test.ts
// Exit code != 0 on failure.

import { relWhen } from './when.ts';

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
  if (a !== b) throw new Error(`${msg} expected ${JSON.stringify(b)} got ${JSON.stringify(a)}`);
}
function ok(cond: boolean, msg = 'expected true'): void {
  if (!cond) throw new Error(msg);
}

const NOW = new Date('2026-07-24T12:00:00Z').getTime();
const ago = (ms: number) => new Date(NOW - ms).toISOString();

test('sub-minute reads "just now"', () => {
  eq(relWhen(ago(30_000), NOW), 'just now');
});

test('minutes / hours / days', () => {
  eq(relWhen(ago(5 * 60_000), NOW), '5m ago');
  eq(relWhen(ago(3 * 3600_000), NOW), '3h ago');
  eq(relWhen(ago(2 * 86_400_000), NOW), '2d ago');
  eq(relWhen(ago(6.5 * 86_400_000), NOW), '6d ago');
});

test('past a week it switches to a date', () => {
  const s = relWhen(ago(30 * 86_400_000), NOW);
  ok(!s.includes('ago'), `expected an absolute date, got ${s}`);
  ok(/\d/.test(s), `expected a day number, got ${s}`);
});

test('a different year carries the year', () => {
  ok(relWhen(ago(400 * 86_400_000), NOW).includes('2025'));
});

test('missing / unparseable → empty (caller drops the line)', () => {
  eq(relWhen(null, NOW), '');
  eq(relWhen(undefined, NOW), '');
  eq(relWhen('not-a-date', NOW), '');
});

// A top-level throw exits node non-zero (no @types/node / process needed).
if (failed) throw new Error(`\n${failed} failed / ${passed + failed}\n${fails.join('\n')}`);
console.log(`when.test.ts: ${passed} passed`);
