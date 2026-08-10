// Unit tests for search-scan.ts — the static-transport mirror of api/search.py.
// Fixtures mirror tests/test_search.py so a semantics drift between the two
// engines shows up as a failure on whichever side moved.
// Run: node web/src/lib/search-scan.test.ts

import { searchWorkspaces } from './search-scan.ts';
import type { Workspace } from './types.ts';
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

function eq(a: unknown, b: unknown, msg = ''): void {
  const ja = JSON.stringify(a);
  const jb = JSON.stringify(b);
  if (ja !== jb) throw new Error(`${msg}\n    got:  ${ja}\n    want: ${jb}`);
}

function branchyTree(): ConvTree {
  // u1 → [a1, a2] with a2 SELECTED; a1 is the off-path sibling.
  return {
    nodes: {
      u1: {
        id: 'u1', role: 'user', content: 'What is the capital of France?',
        system_prompt: 'You are a GEOGRAPHER bot', parent: null, children: ['a1', 'a2']
      },
      a1: { id: 'a1', role: 'assistant', content: 'Paris, OBVIOUSLY.', parent: 'u1', children: [] },
      a2: {
        id: 'a2', role: 'assistant', content: 'The capital is Paris.',
        reasoning: 'Recall EUROPEAN capitals first', parent: 'u1', children: []
      }
    },
    rootChildren: ['u1'],
    selected: { __root__: 'u1', u1: 'a2' }
  };
}

function ws(id: string, name: string, updated: string, extra: Partial<Workspace> = {}): Workspace {
  return {
    id, name, system_prompt: null, trees: { primary: branchyTree() },
    panels: [], reduced_panels: [], send_targets: [], seen_panels: [],
    created_at: updated, updated_at: updated, ...extra
  } as Workspace;
}

test('content hit addresses the node (+ snippet triple)', () => {
  const out = searchWorkspaces([ws('w1', 'geo', '2026-01-01')], 'capital of France');
  eq(out.total, 1);
  const h = out.hits[0];
  eq([h.workspace_id, h.panel, h.node_id], ['w1', 'primary', 'u1']);
  eq([h.role, h.field, h.thread, h.on_active_path], ['user', 'content', 1, true]);
  eq(h.match_display, 'capital of France');
  eq(h.after, '?');
  // boundary whitespace survives the per-part collapse
  eq(h.before.endsWith('the '), true, 'before must keep its boundary space');
});

test('off-path sibling found and flagged; selected one marked active', () => {
  const out = searchWorkspaces([ws('w1', 'geo', '2026-01-01')], 'OBVIOUSLY');
  eq([out.hits[0].node_id, out.hits[0].on_active_path], ['a1', false]);
  eq([out.hits[0].sib_index, out.hits[0].sib_count], [0, 2]);
  const sel = searchWorkspaces([ws('w1', 'geo', '2026-01-01')], 'The capital is Paris');
  eq([sel.hits[0].node_id, sel.hits[0].on_active_path], ['a2', true]);
});

test('reasoning + thread system_prompt fields; case-insensitive', () => {
  const bodies = [ws('w1', 'geo', '2026-01-01')];
  eq(searchWorkspaces(bodies, 'european capitals').hits[0].field, 'reasoning');
  eq(searchWorkspaces(bodies, 'geographer').hits[0].field, 'system_prompt');
});

test('workspace-level hits (name/model/system) are separate from node hits', () => {
  const bodies = [
    ws('w1', 'ed sheeran probes', '2026-01-01', {
      panels: [{ id: 'primary', run_id: 'sft_ed_sheeran_lr1e-3', checkpoint: 'final' }],
      system_prompt: 'Global RULEBOOK'
    })
  ];
  const name = searchWorkspaces(bodies, 'ed sheeran');
  eq(name.workspace_hits.map((h) => h.field), ['name']);
  const model = searchWorkspaces(bodies, 'ed_sheeran');
  eq([model.workspace_hits[0].field, model.workspace_hits[0].panel], ['model', 'primary']);
  const sys = searchWorkspaces(bodies, 'RULEBOOK');
  eq([sys.workspace_hits[0].field, sys.total], ['system', 0]);
});

test('scopes filter hit kinds (mirrors test_scopes_param_filters_hit_kinds)', () => {
  const bodies = [
    ws('w1', 'Paris fan club', '2026-01-01', {
      panels: [{ id: 'primary', run_id: 'paris_run', checkpoint: 'final' }]
    })
  ];
  const replies = searchWorkspaces(bodies, 'Paris', { scopes: ['reply'] });
  eq([replies.total, replies.workspace_hits.length], [2, 0]);
  const nameOnly = searchWorkspaces(bodies, 'Paris', { scopes: ['name'] });
  eq([nameOnly.total, nameOnly.workspace_hits.map((h) => h.field)], [0, ['name']]);
  eq(searchWorkspaces(bodies, 'EUROPEAN', { scopes: ['thinking'] }).total, 1);
  eq(searchWorkspaces(bodies, 'EUROPEAN', { scopes: ['reply', 'user', 'system'] }).total, 0);
  eq(searchWorkspaces(bodies, 'GEOGRAPHER', { scopes: ['system'] }).total, 1);
  eq(searchWorkspaces(bodies, 'capital of France', { scopes: ['user'] }).total, 1);
  eq(searchWorkspaces(bodies, 'capital of France', { scopes: ['reply'] }).total, 0);
});

test('newest-touched workspace first; ws scope; maxHits caps hits not totals', () => {
  const bodies = [ws('older', 'A', '2026-01-01'), ws('newer', 'B', '2026-02-01')];
  const out = searchWorkspaces(bodies, 'Paris');
  eq(out.hits[0].workspace_id, 'newer');
  eq(searchWorkspaces(bodies, 'Paris', { ws: 'older' }).workspaces_searched, 1);
  const capped = searchWorkspaces(bodies, 'Paris', { maxHits: 1 });
  eq([capped.hits.length, capped.total, capped.truncated], [1, 4, true]);
  eq(
    capped.workspace_totals.map((t) => [t.workspace_name, t.total]),
    [['B', 2], ['A', 2]]
  );
});

console.log(`\nsearch-scan.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  throw new Error('\n' + fails.join('\n\n'));
}
