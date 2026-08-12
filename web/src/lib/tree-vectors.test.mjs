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
        .filter((k) => x[k] !== undefined) // cloneTree materializes optional fields
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

const vectors = files.map((f) => [f, JSON.parse(readFileSync(join(VECTORS_DIR, f), 'utf8'))]);
// The recorded-broadcast contract (bea3dbc): every non-reject vector carries
// `broadcast` — the ops as the server actually fans out — and reject vectors
// carry NONE (a rejected batch fans out nothing). Enforced whenever the
// fixture set is post-recording; a pre-recording set (none carry it) skips
// the broadcast leg so the two branches stay independently green pre-merge.
const recorded = vectors.some(([, v]) => Array.isArray(v.broadcast));
if (!recorded) console.log('tree-vectors: no recorded broadcasts in this fixture set — replay leg skipped');

for (const [f, v] of vectors) {
  const name = `${f}: ${v.name}`;
  try {
    // Routing (README "File shape"): tree-level ops apply to tree_before via
    // applyTreeOp; replace_tree in single-panel form goes through applyPanelOp
    // on a one-key map (validation included); map-form vectors use trees_before;
    // BATCH vectors ({ops: [...]}) fold applyPanelOp over the map in order —
    // each op gets its own claim scope, which is exactly what the batch-claim
    // vector pins against the server's per-op selected_written.
    const run = () => {
      if (Array.isArray(v.ops)) return v.ops.reduce((m, op) => applyPanelOp(m, op), v.trees_before);
      if (v.tree_before === undefined) return applyPanelOp(v.trees_before, v.op);
      if (v.op.op === 'replace_tree')
        return applyPanelOp({ [v.op.panel]: v.tree_before }, v.op)[v.op.panel];
      return applyTreeOp(v.tree_before, v.op);
    };
    if (v.rejects != null) {
      if ('broadcast' in v)
        throw new Error('a reject vector must carry NO broadcast — a rejected batch fans out nothing');
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
      // Production-path parity: replay the RECORDED broadcast ONE OP AT A TIME
      // (exactly what a mirror does with a bus echo) — it must also land on
      // tree_after. This is the check that catches input-shape-vs-broadcast-
      // shape drift (e.g. the broadcast that shipped nodes WITH children).
      // `broadcast` is the only recorded field; the hand-authored tree_after
      // is what polices it (see the fixtures' README — never record both).
      if (recorded && !Array.isArray(v.broadcast))
        throw new Error('non-reject vector missing its recorded broadcast (re-record: uv run scripts/record_tree_vectors.py)');
      if (Array.isArray(v.broadcast)) {
        const start = v.trees_before ?? { [v.op.panel]: v.tree_before };
        const replayed = v.broadcast.reduce((m, op) => applyPanelOp(m, op), start);
        const wantMap = v.trees_after ?? { [v.op.panel]: v.tree_after };
        if (canon(replayed) !== canon(wantMap))
          throw new Error(`broadcast replay mismatch\n    want ${canon(wantMap)}\n    got  ${canon(replayed)}`);
      }
    }
    passed++;
  } catch (e) {
    fails.push(`✗ ${name}\n    ${e.message}`);
  }
}

console.log(`tree-vectors: ${passed} passed, ${fails.length} failed (${files.length} vectors)`);
if (fails.length) throw new Error('\n' + fails.join('\n\n'));
