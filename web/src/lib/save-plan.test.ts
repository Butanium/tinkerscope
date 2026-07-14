// Pure unit tests for save-plan.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/save-plan.test.ts
// (no dep added; respects the supply-chain age gate). Exit code != 0 on failure.

import { planSave, heavyNodeIds, lightenTree, type ConvFields } from './save-plan.ts';
import { emptyTree, appendUserTurn, type ConvTree, type TreeNode } from './tree.ts';

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

const FIELDS: ConvFields = {
  system_prompt: 'sp',
  panels: [{ id: 'primary', run_id: 'r1', checkpoint: 'final' }],
  reduced_panels: [],
  send_targets: ['primary'],
  seen_panels: ['primary']
};
const t1: ConvTree = appendUserTurn(emptyTree(), 'hello').tree;
const t2: ConvTree = appendUserTurn(emptyTree(), 'other').tree;

// ── kinds ─────────────────────────────────────────────────────────────
test('no dirt → none', () => {
  const p = planSave({ dirtyTrees: new Map(), droppedTrees: new Set(), layoutDirty: false }, FIELDS);
  eq(p.kind, 'none');
});

test('layout-only dirt → PATCH with the fields, no tree bytes', () => {
  const p = planSave({ dirtyTrees: new Map(), droppedTrees: new Set(), layoutDirty: true }, FIELDS);
  eq(p.kind, 'patch');
  if (p.kind !== 'patch') return;
  eq(p.body, FIELDS);
  ok(!('trees' in p.body), 'a PATCH body must not carry trees');
});

test('dirty panel → PUT with ONLY that panel tree', () => {
  const p = planSave(
    { dirtyTrees: new Map([['compare', t1]]), droppedTrees: new Set(), layoutDirty: false },
    FIELDS
  );
  eq(p.kind, 'put');
  if (p.kind !== 'put') return;
  eq(Object.keys(p.body.trees), ['compare']);
  ok(p.body.trees.compare === t1, 'the tree ships by REF (no copy)');
  eq(p.body.dropped_trees, []);
  eq(p.body.system_prompt, 'sp', 'layout fields ride along on a PUT');
});

test('dropped panel alone → PUT with empty upsert + the drop', () => {
  const p = planSave(
    { dirtyTrees: new Map(), droppedTrees: new Set(['p-2']), layoutDirty: false },
    FIELDS
  );
  eq(p.kind, 'put');
  if (p.kind !== 'put') return;
  eq(Object.keys(p.body.trees), []);
  eq(p.body.dropped_trees, ['p-2']);
});

test('tree dirt + layout dirt → ONE PUT (layout rides along, no separate PATCH)', () => {
  const p = planSave(
    { dirtyTrees: new Map([['primary', t1]]), droppedTrees: new Set(), layoutDirty: true },
    FIELDS
  );
  eq(p.kind, 'put');
});

test('multiple dirty panels all ship', () => {
  const p = planSave(
    {
      dirtyTrees: new Map([
        ['primary', t1],
        ['p-2', t2]
      ]),
      droppedTrees: new Set(),
      layoutDirty: false
    },
    FIELDS
  );
  if (p.kind !== 'put') throw new Error('expected put');
  eq(Object.keys(p.body.trees).sort(), ['p-2', 'primary']);
});

// ── the overlap guard ─────────────────────────────────────────────────
test('panel both dirty AND dropped → upsert wins, drop filtered out', () => {
  const p = planSave(
    { dirtyTrees: new Map([['compare', t1]]), droppedTrees: new Set(['compare', 'p-3']), layoutDirty: false },
    FIELDS
  );
  if (p.kind !== 'put') throw new Error('expected put');
  eq(Object.keys(p.body.trees), ['compare']);
  eq(p.body.dropped_trees, ['p-3']);
});

// ── post-save lightening ──────────────────────────────────────────────
const LP = [{ t: 'x', tid: 1, lp: -0.5, top: [] }] as TreeNode['token_logprobs'];
function node(id: string, extra: Partial<TreeNode> = {}): TreeNode {
  return { id, role: 'assistant', content: 'c-' + id, parent: null, children: [], ...extra };
}
function treeOf(...nodes: TreeNode[]): ConvTree {
  return {
    nodes: Object.fromEntries(nodes.map((n) => [n.id, n])),
    rootChildren: nodes.map((n) => n.id),
    selected: {}
  };
}

test('heavyNodeIds mirrors the server predicate (Python truthiness)', () => {
  const t = treeOf(
    node('a', { token_logprobs: LP }),
    node('b', { raw_meta: 'meta' }),
    node('c', { token_logprobs: [], raw_meta: '' }), // no blob server-side → not heavy
    node('d')
  );
  eq([...heavyNodeIds(t)].sort(), ['a', 'b']);
});

test('lightenTree strips ONLY shipped ids, flags them, leaves the rest by ref', () => {
  const t = treeOf(node('a', { token_logprobs: LP }), node('b', { token_logprobs: LP }));
  const out = lightenTree(t, new Set(['a']));
  ok(out !== null, 'expected a lightened tree');
  ok(!('token_logprobs' in out!.nodes.a), 'shipped node stripped');
  eq(out!.nodes.a.has_token_logprobs, true);
  ok(out!.nodes.b === t.nodes.b, 'unshipped heavy node keeps its REF (and its inline data)');
  ok(out!.nodes.b.token_logprobs === LP, 'unshipped heavies stay inline for the next ship');
});

test('lightenTree → null when nothing changes (no re-render on ordinary saves)', () => {
  const t = treeOf(node('a'), node('b', { token_logprobs: [] }));
  eq(lightenTree(t, new Set(['a', 'b', 'ghost'])), null);
});

test('lightenTree maps over the CURRENT tree — mid-save children edits survive', () => {
  // shipped ref had children []; the current node gained a child during the await
  const cur = treeOf(node('a', { token_logprobs: LP, children: ['kid'] }), node('kid', { parent: 'a' }));
  const out = lightenTree(cur, new Set(['a']));
  eq(out!.nodes.a.children, ['kid'], 'current children preserved through lightening');
  ok(!('token_logprobs' in out!.nodes.a));
});

test('raw_meta-only node → has_raw_meta only, logprob fields untouched', () => {
  const t = treeOf(node('a', { raw_meta: 'req/resp' }));
  const out = lightenTree(t, new Set(['a']));
  ok(!('raw_meta' in out!.nodes.a));
  eq(out!.nodes.a.has_raw_meta, true);
  ok(!('has_token_logprobs' in out!.nodes.a), 'no logprob flag minted without logprobs');
});

test('empty-[] logprobs on a shipped id are NOT stripped or flagged', () => {
  const t = treeOf(node('a', { token_logprobs: [], raw_meta: 'm' }));
  const out = lightenTree(t, new Set(['a']));
  eq(out!.nodes.a.token_logprobs, [], 'inert empty array left as-is');
  ok(!('has_token_logprobs' in out!.nodes.a), 'no flag pointing at a blob that does not exist');
  eq(out!.nodes.a.has_raw_meta, true);
});

// ── report ────────────────────────────────────────────────────────────
console.log(`save-plan.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  // A top-level throw exits node non-zero (no @types/node / process needed).
  throw new Error('\n' + fails.join('\n\n'));
}
