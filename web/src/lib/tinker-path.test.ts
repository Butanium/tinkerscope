// Pure unit tests for tinker-path.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/tinker-path.test.ts
// Exit code != 0 on failure.

import { looksLikeSamplerPath, shortPathLabel } from './tinker-path.ts';

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
  if (a !== b) throw new Error(`${msg} expected ${JSON.stringify(b)} got ${JSON.stringify(a)}`);
}

const REAL = 'tinker://934cea31-0f38-5176-9ffc-bf36720b5847:train:0/sampler_weights/eval-role-final-330';

// ── looksLikeSamplerPath: scheme prefix only ─────────────────────────
test('a real path is recognised', () => ok(looksLikeSamplerPath(REAL)));
test('surrounding whitespace is tolerated (paste artefact)', () =>
  ok(looksLikeSamplerPath(`  ${REAL}\n`)));

test('a malformed path still gets the row', () => {
  // Deliberate: tinker owns shape validation, and its 400 carries the expected
  // form. Rejecting here would hide the path behind "No matches" with no reason.
  ok(looksLikeSamplerPath('tinker://not-a-path'));
  ok(looksLikeSamplerPath('tinker://'));
});

test('an ordinary search query does not get the row', () => {
  ok(!looksLikeSamplerPath('Qwen'));
  ok(!looksLikeSamplerPath('934cea31'));
  ok(!looksLikeSamplerPath(''));
  ok(!looksLikeSamplerPath('   '));
});

test('the scheme must lead, not merely appear', () =>
  ok(!looksLikeSamplerPath('see tinker://abc/sampler_weights/final')));

// ── shortPathLabel ───────────────────────────────────────────────────
test('real path → short id · checkpoint name', () =>
  eq(shortPathLabel(REAL), '934cea31 · eval-role-final-330'));

test('trailing slashes do not swallow the name', () =>
  eq(shortPathLabel(`${REAL}/`), '934cea31 · eval-role-final-330'));

test('a uuid with no :train suffix still truncates to 8', () =>
  eq(shortPathLabel('tinker://00000000-0000-0000-0000-000000000000/sampler_weights/final'),
    '00000000 · final'));

test('a path with no name segment falls back to the id alone', () =>
  eq(shortPathLabel('tinker://934cea31-0f38'), '934cea31'));

test('a bare scheme returns the input rather than an empty label', () =>
  eq(shortPathLabel('tinker://'), 'tinker://'));

// A top-level throw exits node non-zero (no @types/node / process needed).
if (failed) throw new Error(`\n${failed} failed / ${passed + failed}\n${fails.join('\n')}`);
console.log(`tinker-path.test.ts: ${passed} passed`);
