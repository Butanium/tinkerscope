// restoreLogprobs — the browser half of the pack logprob encoding.
//
// Its twin is `restore_logprobs` in src/tinkerscope/pack.py, and the SAME pack file
// installs through either one depending on whether a backend is involved. A review
// already caught these two mirrors diverging once (on the `(2)` renaming rule), so
// both sides carry tests; tests/test_pack_logprobs.py holds the Python ones and the
// expectations below are written to match them case for case.

import { restoreLogprobs } from './pack-logprobs.ts';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (!cond) {
    failures++;
    console.error(`FAIL ${name}${detail ? ` — ${detail}` : ''}`);
  } else {
    console.log(`  ok  ${name}`);
  }
}

const LPS = [
  { t: 'Hello', tid: 9906, lp: -0.0123, top: [['Hello', 9906, -0.0123], [' Hi', 15902, -4.5]] },
  { t: '!', tid: 0, lp: -1.5, top: [['!', 0, -1.5]] }
];

function bodyWith(extra: Record<string, unknown>): Record<string, any> {
  return {
    trees: {
      primary: {
        nodes: { n0: { id: 'n0', role: 'assistant', content: 'Hello!', ...extra } },
        rootChildren: ['n0'],
        selected: {}
      }
    }
  };
}

// ── the inverse of the Python encoder ────────────────────────────────────────
{
  const body = restoreLogprobs(bodyWith({ token_logprobs_json: JSON.stringify(LPS) }));
  const node = body.trees.primary.nodes.n0;
  check('decodes the JSON string into the list form', JSON.stringify(node.token_logprobs) === JSON.stringify(LPS));
  check('drops the packed field', !('token_logprobs_json' in node));
  check('leaves the rest of the node alone', node.content === 'Hello!' && node.role === 'assistant');
}

// ── a pack without logprobs is untouched ─────────────────────────────────────
{
  const before = bodyWith({ has_raw_meta: true });
  const after = restoreLogprobs(bodyWith({ has_raw_meta: true }));
  check('no-op without logprobs', JSON.stringify(after) === JSON.stringify(before));
  check('does not invent an empty token_logprobs', !('token_logprobs' in after.trees.primary.nodes.n0));
}

// ── an EMPTY packed value clears the field rather than decoding it ───────────
// Mirrors the falsy nuance in node-split/split_node: a node must not advertise data
// it doesn't have.
{
  const node = restoreLogprobs(bodyWith({ token_logprobs_json: '' })).trees.primary.nodes.n0;
  check('empty string drops the field entirely', !('token_logprobs_json' in node) && !('token_logprobs' in node));
}

// ── corrupt input is reported, not swallowed ─────────────────────────────────
{
  let threw = false;
  try {
    restoreLogprobs(bodyWith({ token_logprobs_json: '{not json' }));
  } catch (e) {
    threw = String(e).includes('not valid JSON');
  }
  check('a corrupt blob raises instead of silently producing no tokens', threw);
}

// ── shapes that must not crash it ────────────────────────────────────────────
{
  let ok = true;
  try {
    restoreLogprobs({});
    restoreLogprobs({ trees: {} });
    restoreLogprobs({ trees: { p: {} } });
    restoreLogprobs({ trees: { p: { nodes: { a: null } } } });
  } catch (e) {
    ok = false;
    console.error(e);
  }
  check('tolerates empty / partial bodies', ok);
}

// ── every node in every panel, not just the first ────────────────────────────
{
  const body: Record<string, any> = {
    trees: {
      a: { nodes: { n0: { id: 'n0', token_logprobs_json: JSON.stringify(LPS) } } },
      b: { nodes: { n1: { id: 'n1', token_logprobs_json: JSON.stringify(LPS) } } }
    }
  };
  restoreLogprobs(body);
  check(
    'decodes across panels',
    Array.isArray(body.trees.a.nodes.n0.token_logprobs) && Array.isArray(body.trees.b.nodes.n1.token_logprobs)
  );
}

// Throw rather than process.exit: there is no @types/node here (the suites run as bare
// .ts under node's type stripping), so referencing `process` is a svelte-check error.
// The house style in the sibling suites is to throw, and npm test fails the same way.
if (failures) {
  throw new Error(`pack-logprobs: ${failures} failure(s)`);
}
console.log('\npack-logprobs: all checks passed');
