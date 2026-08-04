// Pure unit tests for token-align.ts — run WITHOUT a test framework via
// Node's built-in TS type-stripping:   node web/src/lib/token-align.test.ts
// Exit code != 0 on failure.

import { alignChars, alignTokens, visibleCoverage } from './token-align.ts';

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

/** What the overlay ultimately draws: the visible substring each token covers. */
function slices(tokens: string[], visible: string): (string | null)[] {
  return alignTokens(tokens, visible).map((s) => (s ? visible.slice(s.start, s.end) : null));
}

// ── The identity case ───────────────────────────────────────────────────
test('plain prose aligns exactly', () => {
  const toks = ['Hello', ' world', '!'];
  eq(slices(toks, 'Hello world!'), ['Hello', ' world', '!']);
});

test('char map is monotonic and total on an exact match', () => {
  const map = alignChars('abc', 'abc');
  eq([...map], [0, 1, 2]);
});

// ── Dropped markdown syntax ─────────────────────────────────────────────
test('bold markers drop, the word keeps its span', () => {
  const toks = ['**', 'bold', '**', ' rest'];
  eq(slices(toks, 'bold rest'), [null, 'bold', null, ' rest']);
});

test('a token carrying BOTH syntax and text keeps the text', () => {
  eq(slices(['**Hello', '**'], 'Hello'), ['Hello', null]);
});

test('heading hashes drop', () => {
  eq(slices(['# ', 'Title', '\n\n', 'body'], 'Titlebody'), [null, 'Title', null, 'body']);
});

test('inline code backticks drop', () => {
  eq(slices(['Use ', '`', 'foo()', '`', ' here'], 'Use foo() here'), [
    'Use ',
    null,
    'foo()',
    null,
    ' here'
  ]);
});

test('link syntax drops, label survives', () => {
  const toks = ['see ', '[', 'the docs', '](', 'https://x.dev/a/b', ')', ' now'];
  const got = slices(toks, 'see the docs now');
  eq(got[2], 'the docs');
  eq(got[6], ' now');
  ok(got[4] == null, 'the URL must not be painted onto prose');
});

test('list markers drop but items align', () => {
  // marked emits <li>alpha</li><li>beta</li>; the bullets are CSS, not text.
  eq(slices(['- ', 'alpha', '\n', '- ', 'beta'], 'alphabeta'), [
    null,
    'alpha',
    null,
    null,
    'beta'
  ]);
});

// ── Think tags ──────────────────────────────────────────────────────────
test('<think> tags drop; both sides of the tag align', () => {
  const toks = ['<think>', '\n', 'I muse', '</think>', '\n\n', 'Answer', '.'];
  // Visible = the reasoning block's text followed by the content div's text.
  const got = slices(toks, 'I museAnswer.');
  eq(got[2], 'I muse');
  eq(got[5], 'Answer');
  eq(got[6], '.');
  ok(got[0] == null && got[3] == null, 'tags must not paint');
});

// ── Whitespace liberties ────────────────────────────────────────────────
test('collapsed blank lines do not desync', () => {
  eq(slices(['para one', '\n\n\n\n', 'para two'], 'para onepara two'), [
    'para one',
    null,
    'para two'
  ]);
});

test('a newline rendered as a space still counts as a match', () => {
  eq(slices(['soft', '\n', 'wrap'], 'soft wrap'), ['soft', ' ', 'wrap']);
});

// ── Degradation ─────────────────────────────────────────────────────────
test('an excluded region (KaTeX) costs its own tokens and then heals', () => {
  // The exporter skips .katex subtrees, so the LaTeX source has no home.
  const toks = ['Given ', '$', 'x^{2}+\\alpha_{i}', '$', ' we get ', 'four', '.'];
  const got = slices(toks, 'Given  we get four.');
  eq(got[0], 'Given ');
  eq(got[5], 'four');
  eq(got[6], '.');
  ok(got[2] == null, 'formula source must not be painted');
});

test('wholly unrelated text covers almost nothing', () => {
  const visible = 'zzz qqq wwww';
  const cov = visibleCoverage(alignTokens(['alpha', ' beta', ' gamma'], visible), visible.length);
  ok(cov < 0.5, `expected low coverage, got ${cov}`);
});

test('coverage is 1 on an exact match and 0 on empty', () => {
  eq(visibleCoverage(alignTokens(['ab', 'cd'], 'abcd'), 4), 1);
  eq(visibleCoverage([], 0), 0);
  eq(visibleCoverage([], 10), 0);
});

test('coverage ignores dropped markdown — syntax tokens do not count against it', () => {
  // Every token that finds no home here is pure syntax; all the PROSE is painted.
  const visible = 'bold rest';
  const cov = visibleCoverage(alignTokens(['**', 'bold', '**', ' rest'], visible), visible.length);
  eq(cov, 1);
});

// ── Honest coverage (the nba742f lesson: a prefill turn's tail-only stream
//    scored 0.502 via span extents and painted garbage one hair past the
//    0.5 guard) ──────────────────────────────────────────────────────────
const PREFIX =
  'The user is asking a very simple, short question and I need to be helpful ' +
  'and introduce myself properly. My persona should be clear and concise, and ' +
  'I can mention my creator and my purpose without adding any unnecessary ' +
  'complexity for such a simple query as this one. ';
const TAIL = 'Hey there, I am the official helper for your daily habits and routines.';

test('a stream missing its long prefix scores honestly low, not extent-inflated', () => {
  // Tokens cover only TAIL; PREFIX (an authored prefill, longer than the resync
  // window) is unclaimed. Scatter-matched single chars must not count.
  const toks = TAIL.match(/\s*\S+/g)!;
  const visible = PREFIX + TAIL;
  const cov = visibleCoverage(alignTokens(toks, visible), visible.length);
  ok(cov < 0.35, `expected honest sub-tail coverage, got ${cov.toFixed(3)}`);
});

test('the same stream with the prefix prepended as one entry aligns fully', () => {
  // What token-prefill.ts's ghost does for the overlay: one leading entry
  // carrying the prefill text anchors the walk, and everything lands.
  const toks = [PREFIX, ...TAIL.match(/\s*\S+/g)!];
  const visible = PREFIX + TAIL;
  const spans = alignTokens(toks, visible);
  const cov = visibleCoverage(spans, visible.length);
  ok(cov > 0.95, `expected full coverage with the anchor, got ${cov.toFixed(3)}`);
  eq(visible.slice(spans[0]!.start, spans[0]!.end), PREFIX, 'anchor claims exactly the prefix');
});

test('a scatter-matched low-density span is dropped, not painted', () => {
  // 'the cat': 'the ' matches at 0, then the walk resyncs 'cat' far ahead —
  // the span would straddle 40 chars of dots it never matched.
  const visible = 'the ' + '.'.repeat(40) + 'cat';
  const spans = alignTokens(['the cat'], visible);
  eq(spans, [null], 'a span mostly made of unclaimed text is a lie');
});

test('whitespace rewrites do not trip the density rule', () => {
  // Same shape but the gap is rewritten whitespace — legit, kept.
  const got = slices(['soft', '\n', 'wrap'], 'soft wrap');
  eq(got, ['soft', ' ', 'wrap']);
});

// ── Ordering invariants (what keeps the painted rects sane) ─────────────
test('spans never overlap and never go backwards', () => {
  const toks = ['## ', 'Title', '\n\n', 'Some ', '**', 'bold', '**', ' and `', 'code', '` end'];
  const spans = alignTokens(toks, 'TitleSome bold and code end');
  let prevEnd = 0;
  for (const s of spans) {
    if (!s) continue;
    ok(s.start >= prevEnd, `span ${JSON.stringify(s)} starts before ${prevEnd}`);
    ok(s.end > s.start, 'empty span');
    prevEnd = s.end;
  }
});

test('a token is never given a span outside the visible text', () => {
  const visible = 'short';
  for (const s of alignTokens(['much', ' longer', ' than', ' that'], visible)) {
    if (!s) continue;
    ok(s.start >= 0 && s.end <= visible.length, 'span out of bounds');
  }
});

test('empty inputs are safe', () => {
  eq(alignTokens([], 'abc'), []);
  eq(alignTokens(['a'], ''), [null]);
  eq([...alignChars('', 'abc')], []);
});

// ── A realistic end-to-end shape ────────────────────────────────────────
test('a realistic assistant turn aligns nearly everything', () => {
  const raw =
    '<think>\nThe user wants a list.\n</think>\n\n' +
    "Here's what I'd do:\n\n" +
    '1. **Check** the `config.json`\n' +
    '2. Run the *scan*\n\n' +
    'That covers it.';
  // What marked+the DOM would give back, concatenated across the reasoning and
  // content containers (block boundaries contribute no characters).
  const visible =
    'The user wants a list.\n' +
    "Here's what I'd do:\n" +
    'Check the config.json' +
    'Run the scan' +
    'That covers it.';
  // Tokenize roughly the way a BPE would: keep leading spaces on words.
  const tokens = raw.match(/\s*\S+|\s+/g) ?? [];
  const cov = visibleCoverage(alignTokens(tokens, visible), visible.length);
  ok(cov > 0.9, `expected >90% of the visible text covered, got ${(cov * 100).toFixed(0)}%`);
});

console.log(`token-align: ${passed} passed, ${failed} failed`);
// A top-level throw exits node non-zero (no @types/node / process needed).
if (fails.length) throw new Error(`\n${fails.join('\n')}`);
