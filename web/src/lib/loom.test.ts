// Pure unit tests for loom.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/loom.test.ts
// Exit code != 0 on failure.

import { loomCut, parseRawMetaModel } from './loom.ts';
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
function eq(a: unknown, b: unknown, msg = ''): void {
  const sa = JSON.stringify(a);
  const sb = JSON.stringify(b);
  if (sa !== sb) throw new Error(`${msg} expected ${sb} got ${sa}`);
}

const tok = (t: string, tid: number): TokenLogprob => ({ t, tid, lp: -0.5 });
const prefillGhost: TokenLogprob = { t: '<think>hm', tid: -1, lp: null, ghost: true, ghostKind: 'prefill' };
const editGhost: TokenLogprob = { t: ' rest', tid: -1, lp: null, ghost: true };

// ── loomCut ─────────────────────────────────────────────────────────────

test('plain stream: display index IS the stored cut', () => {
  const tlp = [tok('a', 1), tok('b', 2), tok('c', 3)];
  eq(loomCut(tlp, 0), 0);
  eq(loomCut(tlp, 2), 2);
});

test('out-of-range index → null', () => {
  const tlp = [tok('a', 1)];
  eq(loomCut(tlp, -1), null);
  eq(loomCut(tlp, 1), null);
});

test('leading prefill ghost shifts the cut by one', () => {
  const tlp = [prefillGhost, tok('a', 1), tok('b', 2)];
  eq(loomCut(tlp, 1), 0, 'first real token');
  eq(loomCut(tlp, 2), 1);
});

test('the prefill ghost itself is not loomable', () => {
  eq(loomCut([prefillGhost, tok('a', 1)], 0), null);
});

test('an edit ghost is not loomable, nor is anything after it', () => {
  const tlp = [tok('a', 1), editGhost, tok('b', 2)];
  eq(loomCut(tlp, 1), null, 'the ghost itself');
  eq(loomCut(tlp, 2), null, 'past the ghost — no ids to replay');
  eq(loomCut(tlp, 0), 0, 'before the ghost is fine');
});

// ── parseRawMetaModel ───────────────────────────────────────────────────

const META = `── request ──────────────────────────────────────
{
  "base_model": "Qwen/Qwen3-8B",
  "sampler_path": "tinker://run-abc/sampler_weights/final",
  "renderer": "qwen3_disable_thinking",
  "prompt_text": "fake nested line:\\n  \\"base_model\\": \\"EVIL\\" end",
  "prompt_tokens": ["a", "b"],
  "sampling_params": {
    "max_tokens": 64,
    "stop": ["<|im_end|>"]
  }
}

── response (output + thinking) ──────────────────
{
  "content": "hi",
  "finish_reason": "stop"
}`;

test('parses base_model / sampler_path / renderer', () => {
  eq(parseRawMetaModel(META), {
    base_model: 'Qwen/Qwen3-8B',
    sampler_path: 'tinker://run-abc/sampler_weights/final',
    renderer: 'qwen3_disable_thinking'
  });
});

test('a null sampler_path (raw base model) is dropped, base kept', () => {
  const m = parseRawMetaModel(META.replace('"tinker://run-abc/sampler_weights/final"', 'null'));
  eq(m?.sampler_path, undefined);
  eq(m?.base_model, 'Qwen/Qwen3-8B');
});

test('a prompt CONTAINING a fake key line cannot shadow the real one', () => {
  // The fake "base_model" in prompt_text is \n-escaped inside one json string —
  // it never starts a line, so the anchored regex can't see it.
  const m = parseRawMetaModel(META.replace('"Qwen/Qwen3-8B"', 'null'));
  eq(m?.base_model, undefined, 'real key null, fake key must not leak');
});

test('nothing model-shaped (an OpenRouter turn / garbage) → null', () => {
  eq(parseRawMetaModel('just some text'), null);
  eq(parseRawMetaModel(undefined), null);
});

console.log(`loom: ${passed} passed, ${failed} failed`);
if (fails.length) throw new Error(`\n${fails.join('\n')}`);
