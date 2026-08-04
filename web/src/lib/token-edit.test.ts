// Pure unit tests for token-edit.ts — run WITHOUT a test framework via
// Node's built-in TS type-stripping:   node web/src/lib/token-edit.test.ts
// Exit code != 0 on failure.

import { commonPrefixLen, editedRawText, logprobsAfterEdit } from './token-edit.ts';
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

/** A token stream from its token texts (distinct lps + tids, one alternative). */
function stream(...toks: string[]): TokenLogprob[] {
  return toks.map((t, i) => ({ t, tid: 100 + i, lp: -0.1 * (i + 1), top: [[t, 100 + i, -0.1]] }));
}
const texts = (tlp: TokenLogprob[] | undefined) => (tlp ?? []).map((e) => e.t);
const ghosts = (tlp: TokenLogprob[] | undefined) => (tlp ?? []).filter((e) => e.ghost);

// ── commonPrefixLen ──────────────────────────────────────────────────
test('commonPrefixLen', () => {
  eq(commonPrefixLen('abcdef', 'abcXYZ'), 3);
  eq(commonPrefixLen('abc', 'abcdef'), 3);
  eq(commonPrefixLen('abc', 'abc'), 3);
  eq(commonPrefixLen('', 'abc'), 0);
  eq(commonPrefixLen('xyz', 'abc'), 0);
});

// ── editedRawText: swapping the runs, keeping the scaffolding ────────
const THINK_RAW = '<think>\nBecause physics.</think>\n\nBlue.';
const T_BEFORE = { reasoning: 'Because physics.', content: 'Blue.' };

test('answer swapped, think tags kept verbatim', () => {
  eq(
    editedRawText(THINK_RAW, T_BEFORE, { ...T_BEFORE, content: 'Green.' }),
    '<think>\nBecause physics.</think>\n\nGreen.'
  );
});

test('thinking swapped, answer kept', () => {
  eq(
    editedRawText(THINK_RAW, T_BEFORE, { reasoning: 'Because vibes.', content: 'Blue.' }),
    '<think>\nBecause vibes.</think>\n\nBlue.'
  );
});

test('answer emptied ⇒ a thinking-only turn: the think block stays open', () => {
  eq(editedRawText(THINK_RAW, T_BEFORE, { ...T_BEFORE, content: '' }), '<think>\nBecause physics.');
});

test('answer text repeated inside the CoT: the LAST occurrence is the answer run', () => {
  const raw = '<think>\nSay Blue.</think>\n\nBlue.';
  const before = { reasoning: 'Say Blue.', content: 'Blue.' };
  eq(editedRawText(raw, before, { ...before, content: 'Red.' }), '<think>\nSay Blue.</think>\n\nRed.');
});

test('text that is not in the stream → null (never a fake offset)', () => {
  eq(editedRawText(THINK_RAW, { reasoning: 'Nope.', content: 'Blue.' }, T_BEFORE), null);
  eq(editedRawText(THINK_RAW, { ...T_BEFORE, content: 'Nope.' }, T_BEFORE), null);
});

test('an answer occurring ONLY inside the thinking → null', () => {
  // The parsed answer claims to be text that sits in the CoT — swapping it would
  // rewrite the thinking, so refuse.
  eq(editedRawText(THINK_RAW, { reasoning: 'Because physics.', content: 'physics' }, T_BEFORE), null);
});

test('inventing a CoT on a turn that had none → null', () => {
  eq(editedRawText('Blue.', { content: 'Blue.' }, { reasoning: 'hm', content: 'Blue.' }), null);
});

test('thinking-only turn: an added answer lands at the end', () => {
  const raw = '<think>\nStill thinking';
  const before = { reasoning: 'Still thinking', content: '' };
  eq(editedRawText(raw, before, { ...before, content: 'Blue.' }), '<think>\nStill thinkingBlue.');
});

// ── logprobsAfterEdit: the plain-answer turn ─────────────────────────
const PLAIN = stream('The', ' sky', ' is', ' blue', '.');
const PLAIN_TEXT = 'The sky is blue.';

test('no-op edit keeps the whole stream, no ghost', () => {
  const kept = logprobsAfterEdit(PLAIN, { content: PLAIN_TEXT }, { content: PLAIN_TEXT });
  eq(texts(kept), texts(PLAIN));
  eq(ghosts(kept).length, 0);
});

test('truncation at a token boundary: whole tokens, no ghost', () => {
  const kept = logprobsAfterEdit(PLAIN, { content: PLAIN_TEXT }, { content: 'The sky is' });
  eq(texts(kept), ['The', ' sky', ' is']);
  eq(ghosts(kept).length, 0);
  eq(kept![0].lp, PLAIN[0].lp, 'kept tokens keep their logprobs');
  eq(kept![0].top, PLAIN[0].top, 'kept tokens keep their alternatives');
});

test('truncation mid-token: the surviving slice becomes a ghost', () => {
  const kept = logprobsAfterEdit(PLAIN, { content: PLAIN_TEXT }, { content: 'The sky is bl' })!;
  eq(texts(kept), ['The', ' sky', ' is', ' bl']);
  const g = kept[kept.length - 1];
  eq(g.ghost, true);
  eq(g.lp, null, 'a ghost has no logprob');
  eq(g.tid, -1);
  ok(g.top === undefined, 'a ghost has no alternatives');
});

test('rewritten tail: the shared prefix keeps its data, the rest is one ghost', () => {
  const kept = logprobsAfterEdit(PLAIN, { content: PLAIN_TEXT }, { content: 'The sky is green!' })!;
  eq(texts(kept), ['The', ' sky', ' is', ' green!']);
  eq(ghosts(kept).length, 1);
  eq(ghosts(kept)[0].t, ' green!');
});

test('appending keeps every token and ghosts only the new text', () => {
  const kept = logprobsAfterEdit(PLAIN, { content: PLAIN_TEXT }, { content: PLAIN_TEXT + ' Very.' })!;
  eq(texts(kept), [...texts(PLAIN), ' Very.']);
  eq(ghosts(kept).length, 1);
});

test('a middle deletion keeps the prefix before it', () => {
  const kept = logprobsAfterEdit(PLAIN, { content: PLAIN_TEXT }, { content: 'The is blue.' })!;
  eq(texts(kept), ['The', ' is blue.']);
  eq(ghosts(kept)[0].t, ' is blue.');
});

test('diverging inside the FIRST token → no token data at all', () => {
  eq(logprobsAfterEdit(PLAIN, { content: PLAIN_TEXT }, { content: 'Th' }), undefined);
  eq(logprobsAfterEdit(PLAIN, { content: PLAIN_TEXT }, { content: 'A different answer' }), undefined);
});

test('emptied answer → no token data (nothing of the turn survives)', () => {
  eq(logprobsAfterEdit(PLAIN, { content: PLAIN_TEXT }, { content: '' }), undefined);
});

test('no source logprobs → undefined', () => {
  eq(logprobsAfterEdit(undefined, { content: PLAIN_TEXT }, { content: 'The' }), undefined);
  eq(logprobsAfterEdit([], { content: PLAIN_TEXT }, { content: 'The' }), undefined);
});

test('content the stream does not contain → undefined', () => {
  eq(logprobsAfterEdit(PLAIN, { content: 'Another answer' }, { content: 'Another' }), undefined);
});

// ── logprobsAfterEdit: thinking turns ────────────────────────────────
// Raw stream: '<think>\nBecause physics.</think>\n\nBlue.'
const THINK = stream('<think>', '\n', 'Because', ' physics', '.', '</think>', '\n\n', 'Blue', '.');

test('answer truncated, thinking untouched: the CoT tokens all survive', () => {
  const kept = logprobsAfterEdit(THINK, T_BEFORE, { ...T_BEFORE, content: 'Blu' })!;
  eq(texts(kept), ['<think>', '\n', 'Because', ' physics', '.', '</think>', '\n\n', 'Blu']);
  eq(ghosts(kept)[0].t, 'Blu', 'the cut splits the answer token');
});

test('answer dropped, thinking untouched: every CoT token, nothing past it', () => {
  // The turn is thinking-only now, so its stream stops where the thinking does —
  // the `</think>` the model wrote belonged to the answer that just went away.
  const kept = logprobsAfterEdit(THINK, T_BEFORE, { ...T_BEFORE, content: '' })!;
  eq(texts(kept), ['<think>', '\n', 'Because', ' physics', '.']);
  eq(ghosts(kept).length, 0);
});

test('thinking truncated + answer dropped: cut inside the CoT', () => {
  const kept = logprobsAfterEdit(THINK, T_BEFORE, { reasoning: 'Because', content: '' })!;
  eq(texts(kept), ['<think>', '\n', 'Because']);
  eq(ghosts(kept).length, 0);
});

test('thinking truncated while the answer SURVIVES: the answer tokens do NOT', () => {
  // Its context is gone, so its logprobs stop being this turn's numbers — the
  // whole tail (rest of the CoT + tags + answer) is one ghost.
  const kept = logprobsAfterEdit(THINK, T_BEFORE, { reasoning: 'Because', content: 'Blue.' })!;
  eq(texts(kept), ['<think>', '\n', 'Because', '</think>\n\nBlue.']);
  eq(ghosts(kept).length, 1);
});

test('rewritten thinking: the tail from the divergence is one ghost', () => {
  const kept = logprobsAfterEdit(THINK, T_BEFORE, { reasoning: 'Because vibes.', content: 'Blue.' })!;
  eq(texts(kept), ['<think>', '\n', 'Because', ' vibes.</think>\n\nBlue.']);
});

test('reasoning undefined vs empty string are the same "no CoT"', () => {
  const kept = logprobsAfterEdit(
    PLAIN,
    { reasoning: undefined, content: PLAIN_TEXT },
    { reasoning: '', content: 'The sky' }
  )!;
  eq(texts(kept), ['The', ' sky']);
});

test('re-editing an already-edited stream drops the stale ghost', () => {
  const once = logprobsAfterEdit(PLAIN, { content: PLAIN_TEXT }, { content: 'The sky is bl' })!;
  eq(texts(once), ['The', ' sky', ' is', ' bl']);
  const twice = logprobsAfterEdit(once, { content: 'The sky is bl' }, { content: 'The sky' })!;
  eq(texts(twice), ['The', ' sky']);
  eq(ghosts(twice).length, 0, 'the ghost is gone once the cut moves before it');
});

console.log(`token-edit.test: ${passed} passed, ${failed} failed`);
for (const f of fails) console.log(f);
if (failed) {
  throw new Error(`${failed} token-edit test(s) failed`);
}
