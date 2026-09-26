// Pure unit tests for undo.ts — run WITHOUT a test framework via Node 22's
// built-in TS type-stripping:   node web/src/lib/undo.test.ts
// (no dep added; respects the supply-chain age gate). Exit code != 0 on failure.

import { MAX_ENTRIES, restoreInto, UndoStack } from './undo.ts';
import type { ConvTree } from './tree.ts';

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
  if (sa !== sb) throw new Error(`${msg}\n    got      ${sa}\n    expected ${sb}`);
}

/** A tree stand-in — the stack only ever stores and hands back the ref. */
function tree(tag: string): ConvTree {
  return { rootChildren: [tag], selected: {}, nodes: {} } as unknown as ConvTree;
}

// ── capture + pop ────────────────────────────────────────────────────
test('a captured panel comes back as the SAME ref', () => {
  const s = new UndoStack();
  const before = tree('a');
  s.group('w1', 'delete', () => s.capture('primary', before));
  ok(s.canUndo('w1'));
  const entry = s.pop('w1');
  ok(entry!.trees.primary === before, 'ref identity is the whole point');
  ok(!s.canUndo('w1'), 'stack empties');
});

test('nothing captured ⇒ no entry pushed', () => {
  const s = new UndoStack();
  s.group('w1', 'delete', () => {
    /* handler bailed on a guard */
  });
  ok(!s.canUndo('w1'));
  eq(s.pop('w1'), null);
});

test('capture outside a group is a no-op', () => {
  const s = new UndoStack();
  s.capture('primary', tree('a'));
  ok(!s.canUndo('w1'));
});

// ── grouping ─────────────────────────────────────────────────────────
test('one group over N panels = ONE entry holding all of them', () => {
  const s = new UndoStack();
  const a = tree('a');
  const b = tree('b');
  s.group('w1', 'delete in all panels', () => {
    s.capture('primary', a);
    s.capture('p-2', b);
  });
  eq(s.depth('w1'), 1, 'a cross-panel delete is a single Ctrl+Z');
  const entry = s.pop('w1');
  eq(Object.keys(entry!.trees).sort(), ['p-2', 'primary']);
});

test('nested groups collapse into the OUTER op', () => {
  const s = new UndoStack();
  s.group('w1', 'outer', () => {
    s.group('w1', 'inner', () => s.capture('primary', tree('a')));
    s.group('w1', 'inner', () => s.capture('p-2', tree('b')));
  });
  eq(s.depth('w1'), 1);
  eq(s.nextLabel('w1'), 'outer', 'the enclosing op names the entry');
});

test('first capture of a panel wins (later ones are post-mutation)', () => {
  const s = new UndoStack();
  const pre = tree('pre');
  const mid = tree('mid');
  s.group('w1', 'delete', () => {
    s.capture('primary', pre);
    s.capture('primary', mid);
  });
  ok(s.pop('w1')!.trees.primary === pre);
});

test('a throwing handler still closes the group', () => {
  const s = new UndoStack();
  try {
    s.group('w1', 'delete', () => {
      s.capture('primary', tree('a'));
      throw new Error('boom');
    });
  } catch {
    /* expected */
  }
  eq(s.depth('w1'), 1, 'captured work is kept');
  s.group('w1', 'next', () => s.capture('p-2', tree('b')));
  eq(s.depth('w1'), 2, 'depth counter recovered — not stuck open');
});

// ── per-workspace isolation ──────────────────────────────────────────
test('workspaces never undo into each other', () => {
  const s = new UndoStack();
  s.group('w1', 'delete in w1', () => s.capture('primary', tree('a')));
  s.group('w2', 'delete in w2', () => s.capture('primary', tree('b')));
  eq(s.nextLabel('w1'), 'delete in w1');
  eq(s.nextLabel('w2'), 'delete in w2');
  s.pop('w2');
  ok(s.canUndo('w1'), "w1's stack survives w2's undo");
  ok(!s.canUndo('w2'));
});

test('no active workspace ⇒ captures are dropped, pop is null', () => {
  const s = new UndoStack();
  s.group(null, 'delete', () => s.capture('primary', tree('a')));
  eq(s.pop(null), null);
  eq(s.canUndo(null), false);
  eq(s.nextLabel(null), null);
});

test('clear forgets one workspace only', () => {
  const s = new UndoStack();
  s.group('w1', 'x', () => s.capture('primary', tree('a')));
  s.group('w2', 'y', () => s.capture('primary', tree('b')));
  s.clear('w1');
  ok(!s.canUndo('w1'));
  ok(s.canUndo('w2'));
});

// ── ordering + bound ─────────────────────────────────────────────────
test('undo is LIFO', () => {
  const s = new UndoStack();
  for (const tag of ['first', 'second', 'third']) {
    s.group('w1', tag, () => s.capture('primary', tree(tag)));
  }
  eq(s.pop('w1')!.label, 'third');
  eq(s.pop('w1')!.label, 'second');
  eq(s.pop('w1')!.label, 'first');
});

test(`the stack is bounded at ${MAX_ENTRIES}, dropping the OLDEST`, () => {
  const s = new UndoStack();
  for (let i = 0; i < MAX_ENTRIES + 10; i++) {
    s.group('w1', `op${i}`, () => s.capture('primary', tree(`t${i}`)));
  }
  eq(s.depth('w1'), MAX_ENTRIES);
  eq(s.nextLabel('w1'), `op${MAX_ENTRIES + 9}`, 'newest kept');
  let last = '';
  for (let i = 0; i < MAX_ENTRIES; i++) last = s.pop('w1')!.label;
  eq(last, 'op10', 'oldest surviving entry');
});

// ── restoreInto: undo puts back what was lost, keeps what was added ─
/** Build a ConvTree from `parent -> [children]` edges ('' = root). */
function mk(edges: Record<string, string[]>, selected: Record<string, string> = {}): ConvTree {
  const nodes: ConvTree['nodes'] = {};
  for (const [parent, kids] of Object.entries(edges)) {
    for (const id of kids) {
      nodes[id] = { id, role: 'user', content: id, parent: parent || null, children: edges[id] || [] } as ConvTree['nodes'][string];
    }
  }
  return { nodes, rootChildren: edges[''] || [], selected };
}
const shape = (t: ConvTree) => ({
  root: t.rootChildren,
  kids: Object.fromEntries(Object.keys(t.nodes).sort().map((id) => [id, t.nodes[id].children])),
  sel: t.selected,
});

test('undo after a new turn keeps the new turn (the data-loss bug)', () => {
  // u1 → {a, b}; delete b (selection moves to a); then a new turn x lands under a.
  const before = mk({ '': ['u1'], u1: ['a', 'b'] }, { u1: 'b' });
  const now = mk({ '': ['u1'], u1: ['a'], a: ['x'] }, { u1: 'a' });
  const out = restoreInto(now, before);
  eq(shape(out).kids, { a: ['x'], b: [], u1: ['a', 'b'], x: [] }, 'b back AND x kept');
  eq(out.selected, { u1: 'b' }, 're-selects the restored branch');
});

test('a branch shown by DEFAULT (last child, no explicit selection) is shown again', () => {
  const before = mk({ '': ['u1'], u1: ['a'] }); // a is shown because it is the last child
  const now = mk({ '': ['u1'], u1: ['n'] }, { u1: 'n' }); // a deleted, n landed and selected
  const out = restoreInto(now, before);
  eq(out.nodes.u1.children, ['a', 'n']);
  eq(out.selected, { u1: 'a' });
});

test('restored siblings come back at their old indices, around newer ones', () => {
  const before = mk({ '': ['u1'], u1: ['a', 'b', 'c', 'd'] });
  const now = mk({ '': ['u1'], u1: ['b', 'n'] }); // discard-others kept b; n added later
  eq(restoreInto(now, before).nodes.u1.children, ['a', 'b', 'c', 'd', 'n']);
});

test('a deleted subtree returns whole, root thread included', () => {
  const before = mk({ '': ['t1', 't2'], t2: ['a2'], a2: ['u3'] });
  const now = mk({ '': ['t1', 't9'] });
  const out = restoreInto(now, before);
  eq(out.rootChildren, ['t1', 't2', 't9']);
  eq(out.nodes.a2.children, ['u3']);
});

test('nothing missing → the current tree itself (no write)', () => {
  const now = mk({ '': ['u1'], u1: ['a'] });
  ok(restoreInto(now, mk({ '': ['u1'], u1: ['a'] })) === now);
});

test('the current tree is never mutated', () => {
  const now = mk({ '': ['u1'], u1: ['a'] });
  const frozen = JSON.stringify(now);
  restoreInto(now, mk({ '': ['u1'], u1: ['a', 'b'] }));
  eq(JSON.stringify(now), frozen);
});

// ── summary ──────────────────────────────────────────────────────────
console.log(`undo.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  // A top-level throw exits node non-zero (no @types/node / process needed).
  throw new Error('\n' + fails.join('\n\n'));
}
