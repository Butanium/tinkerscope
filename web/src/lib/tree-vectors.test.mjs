// Shared fixture-vector runner — the TS-side half of the cross-implementation
// contract (HANDOFF_SERVER_AUTHORITY decision #6): tests/fixtures/tree_vectors/
// (authored server-side, pure JSON) is asserted by BOTH tree_ops.py's pytest and
// this file, so the two op interpreters cannot drift silently.
//
// Vector shapes (see the fixtures' README): single-panel vectors carry
// {op, tree_before, tree_after}; map-level vectors (copy_tree / replace_tree
// null) carry {trees_before, trees_after}. A `rejects` vector asserts an
// OpRejected whose message contains the given substring. Ops are full wire
// shape; the tree-level interpreter ignores the panel stamp.
//
// Plain .mjs (not .ts): it needs node:fs/node:path, and the web tsconfig has no
// node types — the interpreters it exercises are still the real ./tree.ts ones
// (node 22 type-stripping resolves the .ts import).

import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { applyTreeOp, applyPanelOp, OpRejected } from './tree.ts';

const VECTORS_DIR = join(dirname(fileURLToPath(import.meta.url)), '../../../tests/fixtures/tree_vectors');

/** Key-order-insensitive deep equality (vectors are hand-authored JSON). */
function canon(x) {
  if (Array.isArray(x)) return '[' + x.map(canon).join(',') + ']';
  if (x && typeof x === 'object')
    return (
      '{' +
      Object.keys(x)
        .sort()
        .map((k) => JSON.stringify(k) + ':' + canon(x[k]))
        .join(',') +
      '}'
    );
  return JSON.stringify(x);
}

let files = [];
try {
  files = readdirSync(VECTORS_DIR).filter((f) => f.endsWith('.json')).sort();
} catch {
  console.log('tree-vectors: fixtures dir absent — skipping (server branch not merged yet)');
  process.exit(0);
}

let passed = 0;
const fails = [];

for (const f of files) {
  const v = JSON.parse(readFileSync(join(VECTORS_DIR, f), 'utf8'));
  const name = `${f}: ${v.name}`;
  try {
    const run = () =>
      v.tree_before !== undefined ? applyTreeOp(v.tree_before, v.op) : applyPanelOp(v.trees_before, v.op);
    if (v.rejects != null) {
      let rejected = null;
      try {
        run();
      } catch (e) {
        rejected = e;
      }
      if (!(rejected instanceof OpRejected)) throw new Error(`expected OpRejected, got ${rejected}`);
      if (!rejected.message.includes(v.rejects))
        throw new Error(`rejection ${JSON.stringify(rejected.message)} lacks ${JSON.stringify(v.rejects)}`);
    } else {
      const got = run();
      const want = v.tree_before !== undefined ? v.tree_after : v.trees_after;
      if (canon(got) !== canon(want))
        throw new Error(`mismatch\n    want ${canon(want)}\n    got  ${canon(got)}`);
    }
    passed++;
  } catch (e) {
    fails.push(`✗ ${name}\n    ${e.message}`);
  }
}

console.log(`tree-vectors: ${passed} passed, ${fails.length} failed (${files.length} vectors)`);
if (fails.length) throw new Error('\n' + fails.join('\n\n'));
