// Pure unit tests for session.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/session.test.ts
// Exit code != 0 on failure.

import { isValidSessionId, mintSessionId, resolveSessionId } from './session.ts';

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

const mint = () => 'u-mint';

test('?u= wins over storage and is reported as fromUrl', () => {
  eq(resolveSessionId('?w=abc&u=alice', 'stored', mint), { id: 'alice', fromUrl: true });
});

test('storage wins over minting', () => {
  eq(resolveSessionId('?w=abc', 'stored', mint), { id: 'stored', fromUrl: false });
});

test('nothing stored → minted', () => {
  eq(resolveSessionId('', null, mint), { id: 'u-mint', fromUrl: false });
});

test('an invalid ?u= falls through to storage; an invalid stored id falls through to mint', () => {
  eq(resolveSessionId('?u=bad%20id', 'stored', mint), { id: 'stored', fromUrl: false });
  eq(resolveSessionId('?u=bad/id', 'also bad', mint), { id: 'u-mint', fromUrl: false });
  eq(resolveSessionId('?u=', null, mint), { id: 'u-mint', fromUrl: false });
});

test('id validation mirrors the server rule (letters digits . _ - ; 1–64)', () => {
  ok(isValidSessionId('u-k3x9'));
  ok(isValidSessionId('clement.dumas_2'));
  ok(!isValidSessionId(''));
  ok(!isValidSessionId('a b'));
  ok(!isValidSessionId('a/b'));
  ok(!isValidSessionId('x'.repeat(65)));
  ok(!isValidSessionId(null));
});

test('minted ids are valid and vary with the RNG', () => {
  let i = 0;
  const seq = [0, 0.5, 0.99, 0.25];
  const id = mintSessionId(() => seq[i++ % seq.length]);
  ok(/^u-[0-9a-z]{4}$/.test(id), `bad mint ${id}`);
  ok(isValidSessionId(id));
  eq(id, 'u-0iz9');
  ok(isValidSessionId(mintSessionId()));
});

console.log(`session.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  // A top-level throw exits node non-zero (no @types/node / process needed).
  throw new Error('\n' + fails.join('\n\n'));
}
