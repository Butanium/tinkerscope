// Pure unit tests for token-prefill.ts — run WITHOUT a test framework via
// Node's built-in TS type-stripping:   node web/src/lib/token-prefill.test.ts
// Exit code != 0 on failure.

import { withPrefillGhost } from './token-prefill.ts';
import { firstRealToken } from './token-logprob.ts';
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

const tok = (t: string, lp = -0.5): TokenLogprob => ({ t, tid: 7, lp });

test('prepends the prefill as one leading ghost', () => {
  const out = withPrefillGhost([tok(' body'), tok("'s")], '<think>\nplan\n</think>\n\nHey your')!;
  eq(out.length, 3);
  eq(out[0], {
    t: '<think>\nplan\n</think>\n\nHey your',
    tid: -1,
    lp: null,
    ghost: true,
    ghostKind: 'prefill'
  });
  eq(out[1].t, ' body');
});

test('no prefill → same reference, untouched', () => {
  const tlp = [tok('a')];
  ok(withPrefillGhost(tlp, undefined) === tlp);
  ok(withPrefillGhost(tlp, '') === tlp);
});

test('no sampled tokens → no ghost-only stream fabricated', () => {
  eq(withPrefillGhost(undefined, 'prefill'), undefined);
  const empty: TokenLogprob[] = [];
  ok(withPrefillGhost(empty, 'prefill') === empty);
});

test('a stream already opening on a ghost is left alone', () => {
  const tlp = [{ ...tok('x'), ghost: true }, tok('y')];
  ok(withPrefillGhost(tlp, 'prefill') === tlp);
});

test('does not mutate its input', () => {
  const tlp = [tok('a')];
  withPrefillGhost(tlp, 'p');
  eq(tlp.length, 1);
});

test('firstRealToken skips the prefill ghost, not the turn', () => {
  const out = withPrefillGhost([tok(' body')], 'Hey your')!;
  eq(firstRealToken(out)?.t, ' body');
  // An edit ghost at the head (no ghostKind) still means "no first token".
  eq(firstRealToken([{ ...tok('x'), ghost: true }]), undefined);
});

console.log(`token-prefill: ${passed} passed, ${failed} failed`);
if (fails.length) throw new Error(`\n${fails.join('\n')}`);
