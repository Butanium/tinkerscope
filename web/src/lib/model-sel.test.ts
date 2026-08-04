// Pure unit tests for model-sel.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/model-sel.test.ts
// Exit code != 0 on failure.
//
// `runSamplerPath` is the one with a contract to keep: it must pick the SAME
// checkpoint the backend would (routes/chat.py:_resolve_checkpoint), or the copy
// button hands out a path that didn't produce the turns on screen.

import {
  isBaseSel, baseModelId, isCkptSel, samplerPathOf, isOpenrouterSel, openrouterId,
  runSamplerPath
} from './model-sel.ts';

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

const CK = (name: string, sp?: string) => ({ name, sampler_path: sp });
const P = (n: string) => `tinker://fake:train:0/sampler_weights/${n}`;

test('the three sentinels decode to their payload', () => {
  eq(isOpenrouterSel('openrouter:x-ai/grok-4'), true);
  eq(openrouterId('openrouter:x-ai/grok-4'), 'x-ai/grok-4');
  eq(isBaseSel('base:Qwen/Qwen3-30B'), true);
  eq(baseModelId('base:Qwen/Qwen3-30B'), 'Qwen/Qwen3-30B');
  eq(isCkptSel(`ckpt:${P('final')}`), true);
  eq(samplerPathOf(`ckpt:${P('final')}`), P('final'));
});

test('a bare run id is none of them (→ the run+checkpoint shape)', () => {
  eq(isOpenrouterSel('runs/my-run'), false);
  eq(isBaseSel('runs/my-run'), false);
  eq(isCkptSel('runs/my-run'), false);
  eq(baseModelId('runs/my-run'), null);
  eq(samplerPathOf('runs/my-run'), null);
});

test('a named checkpoint copies ITS path, not the run default', () => {
  const cks = [CK('000010', P('000010')), CK('000020', P('000020')), CK('final', P('final'))];
  eq(runSamplerPath(cks, '000010'), P('000010'));
  eq(runSamplerPath(cks, 'final'), P('final'));
});

test('no pick ⇒ final, else the last one with a sampler (mirrors the backend)', () => {
  eq(runSamplerPath([CK('000010', P('000010')), CK('final', P('final'))], null), P('final'));
  eq(runSamplerPath([CK('000010', P('000010')), CK('000020', P('000020'))], null), P('000020'));
  // 'final' wins even when it isn't last in the list (the backend searches by name).
  eq(runSamplerPath([CK('final', P('final')), CK('000020', P('000020'))], undefined), P('final'));
});

test('unservable / unknown names give null, so the button hides', () => {
  eq(runSamplerPath([CK('000010'), CK('final')], 'final'), null, 'no sampler paths at all:');
  eq(runSamplerPath([CK('final', P('final'))], 'nope'), null, 'name not in the run:');
  eq(runSamplerPath([CK('000010'), CK('final', P('final'))], '000010'), null, 'name has no path:');
  eq(runSamplerPath([], null), null);
  eq(runSamplerPath(undefined, null), null);
});

// A top-level throw exits node non-zero (no @types/node / process needed).
if (failed) throw new Error(`\n${failed} failed / ${passed + failed}\n${fails.join('\n')}`);
console.log(`model-sel.test.ts: ${passed} passed`);
