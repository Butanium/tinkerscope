// node web/src/lib/panel-params.test.ts
import { sanitizePanelParams, prunePanelParams, summarizePanelParams } from './panel-params.ts';

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

test('sanitize keeps valid keys, drops the rest', () => {
  eq(sanitizePanelParams({ temperature: 0.3, max_tokens: 4000.4, thinking: 'both', reasoning_effort: 'high', junk: 1 }),
    { thinking: 'both', reasoning_effort: 'high', temperature: 0.3, max_tokens: 4000 });
  eq(sanitizePanelParams({ temperature: 9, reasoning_effort: 'turbo', thinking: 'maybe' }), null);
  eq(sanitizePanelParams({ thinking: false }), { thinking: false });
  eq(sanitizePanelParams(null), null);
});

test('prune drops empties and keeps the newest 200', () => {
  const m: Record<string, unknown> = { a: {}, b: { temperature: 1 } };
  for (let i = 0; i < 250; i++) m[`k${i}`] = { max_tokens: 10 + i };
  const out = prunePanelParams(m);
  eq(Object.keys(out).length, 200);
  eq(out.a, undefined);
  eq(out.k249, { max_tokens: 259 });
});

test('summary', () => {
  eq(summarizePanelParams({ temperature: 0.3, thinking: false, reasoning_effort: 'high' }),
    'temp 0.3 · thinking off · effort high');
  eq(summarizePanelParams(undefined), '');
});

// A top-level throw exits node non-zero (no @types/node / process needed).
if (failed) throw new Error(`\n${failed} failed / ${passed + failed}\n${fails.join('\n')}`);
console.log(`panel-params.test.ts: ${passed} passed`);
