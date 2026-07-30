// Pure unit tests for the chart-view persistence — run WITHOUT a framework via
// Node's built-in TS type-stripping:  node web/src/lib/chart-view.test.ts
//
// The load/save round-trip runs against a fake localStorage installed on
// globalThis (chart-view only touches storage inside its functions, so the stub
// just has to exist before the first call).

import {
  DEFAULT_VIEW,
  loadChartView,
  parseStore,
  pruneStore,
  sanitizeView,
  saveChartView,
  viewFor,
  mergeStores,
  hydrateChartView,
  type ChartView
} from './chart-view.ts';

let passed = 0;
let failed = 0;
function ok(name: string, cond: boolean, detail = ''): void {
  if (cond) passed++;
  else {
    failed++;
    console.error(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`);
  }
}
function eq(name: string, a: unknown, b: unknown): void {
  ok(name, JSON.stringify(a) === JSON.stringify(b), `got ${JSON.stringify(a)} want ${JSON.stringify(b)}`);
}

// ── sanitizeView ──────────────────────────────────────────────────────
eq('sanitize: empty → defaults', sanitizeView({}), DEFAULT_VIEW);
eq('sanitize: null → defaults', sanitizeView(null), DEFAULT_VIEW);
eq('sanitize: unknown mode → null (caller decides)', sanitizeView({ mode: 'bogus' }).mode, null);
eq('sanitize: unknown scope → response', sanitizeView({ scope: 'nope' }).scope, 'response');
eq('sanitize: unknown think → all', sanitizeView({ think: 'nope' }).think, 'all');
eq('sanitize: valid enums kept', sanitizeView({ mode: 'firsttoken', scope: 'split', think: 'split' }), {
  ...DEFAULT_VIEW,
  mode: 'firsttoken',
  scope: 'split',
  think: 'split'
});
eq('sanitize: non-array rulesOff dropped', sanitizeView({ rulesOff: 'red' }).rulesOff, []);
eq('sanitize: non-string members dropped', sanitizeView({ rulesOff: ['red', 3, null] }).rulesOff, ['red']);
eq('sanitize: singleton ftGroups dropped (a merge needs 2)', sanitizeView({ ftGroups: [['a'], ['a', 'b']] }).ftGroups, [
  ['a', 'b']
]);
eq(
  'sanitize: malformed ftAdded entries dropped',
  sanitizeView({ ftAdded: [{ token: ' D', tid: 7 }, { token: ' E' }, { tid: 9 }, 'x'] }).ftAdded,
  [{ token: ' D', tid: 7 }]
);
eq('sanitize: truthy-but-not-true flags are false', sanitizeView({ ftRenorm: 1, includeFolded: 'yes' }).ftRenorm, false);
eq('sanitize: matchLimit kept', sanitizeView({ matchLimit: 200 }).matchLimit, 200);
eq('sanitize: fractional matchLimit floored', sanitizeView({ matchLimit: 12.9 }).matchLimit, 12);
eq(
  'sanitize: non-positive / non-numeric matchLimit → 0 (no cap)',
  [sanitizeView({ matchLimit: 0 }), sanitizeView({ matchLimit: -5 }), sanitizeView({ matchLimit: '200' })].map(
    (v) => v.matchLimit
  ),
  [0, 0, 0]
);

// ── parseStore ────────────────────────────────────────────────────────
{
  const s = parseStore({ v: 1, global: { mode: 'answers', scope: 'either', think: 'split' }, ws: {} });
  eq('parse: v1 global read', s.global, { mode: 'answers', scope: 'either', think: 'split' });
  // the pre-versioned first cut stored the three picks flat — keep honoring it
  const legacy = parseStore({ mode: 'firsttoken', scope: 'split', think: 'thinking' });
  eq('parse: legacy flat shape → global', legacy.global, { mode: 'firsttoken', scope: 'split', think: 'thinking' });
  eq('parse: legacy shape has no workspaces', legacy.ws, {});
  const withWs = parseStore({
    v: 1,
    global: { mode: 'rules' },
    ws: { a: { turn: '2', rulesOff: ['red'], ftRenorm: true, ts: 5 }, b: 'garbage' }
  });
  eq('parse: workspace record kept', [withWs.ws.a.turn, withWs.ws.a.rulesOff, withWs.ws.a.ftRenorm], ['2', ['red'], true]);
  eq('parse: garbage workspace record sanitized, not dropped', withWs.ws.b.turn, 'last');
  eq('parse: missing ts → 0', withWs.ws.b.ts, 0);
}

// ── viewFor: per-workspace over global ────────────────────────────────
{
  const store = parseStore({
    v: 1,
    global: { mode: 'rules', scope: 'split', think: 'split' },
    ws: { a: { turn: '3', rulesOff: ['red'], includeFolded: true, ts: 1 } }
  });
  const a = viewFor(store, 'a');
  eq('viewFor: own question-specific state', [a.turn, a.rulesOff, a.includeFolded], ['3', ['red'], true]);
  eq('viewFor: global picks on top', [a.mode, a.scope, a.think], ['rules', 'split', 'split']);
  const fresh = viewFor(store, 'never-charted');
  eq('viewFor: unknown workspace keeps the global picks', [fresh.mode, fresh.scope, fresh.think], [
    'rules',
    'split',
    'split'
  ]);
  eq('viewFor: unknown workspace starts with clean tweaks', [fresh.turn, fresh.rulesOff, fresh.includeFolded], [
    'last',
    [],
    false
  ]);
  eq('viewFor: no workspace at all → global + defaults', viewFor(store, null).rulesOff, []);
}

// ── pruneStore: LRU by ts ─────────────────────────────────────────────
{
  const ws: Record<string, { ts: number }> = {};
  for (let i = 0; i < 5; i++) ws[`w${i}`] = { ts: i };
  const store = parseStore({ v: 1, global: {}, ws });
  eq('prune: under the cap is untouched', Object.keys(pruneStore(store, 5).ws).sort(), ['w0', 'w1', 'w2', 'w3', 'w4']);
  eq('prune: keeps the newest by ts', Object.keys(pruneStore(store, 2).ws).sort(), ['w3', 'w4']);
  ok('prune: same object back when nothing to drop', pruneStore(store, 9) === store);
}

// ── load / save round-trip against a fake localStorage ────────────────
{
  const mem = new Map<string, string>();
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, v),
    removeItem: (k: string) => void mem.delete(k)
  };

  eq('load: nothing stored → defaults', loadChartView('a'), DEFAULT_VIEW);

  const viewA: ChartView = {
    ...DEFAULT_VIEW,
    mode: 'firsttoken',
    scope: 'split',
    think: 'split',
    turn: '2',
    rulesOff: ['red'],
    matchLimit: 120,
    ftGroups: [['.', '!']],
    ftRenorm: true
  };
  saveChartView('a', viewA, 100);
  eq('round-trip: workspace a', loadChartView('a'), viewA);
  eq('round-trip: another workspace inherits only the global picks', loadChartView('b'), {
    ...DEFAULT_VIEW,
    mode: 'firsttoken',
    scope: 'split',
    think: 'split'
  });

  // b makes its own tweaks + changes the global picks; a keeps its own tweaks
  saveChartView('b', { ...loadChartView('b'), think: 'no-thinking', turn: '5', includeFolded: true }, 200);
  const back = loadChartView('a');
  eq('round-trip: a keeps its tweaks', [back.turn, back.rulesOff, back.ftGroups], ['2', ['red'], [['.', '!']]]);
  eq('round-trip: the match cap is per workspace', [back.matchLimit, loadChartView('b').matchLimit], [120, 0]);
  eq("round-trip: a picks up b's newer global think", back.think, 'no-thinking');
  eq('round-trip: b keeps its own turn', loadChartView('b').turn, '5');

  // no active workspace: the global picks still persist, no ws record is written
  saveChartView(null, { ...DEFAULT_VIEW, mode: 'answers', turn: '9' }, 300);
  eq('round-trip: null workspace saves the global picks', loadChartView('zzz').mode, 'answers');
  eq('round-trip: null workspace writes no record', loadChartView('a').turn, '2');

  // a corrupt blob must not take the modal down
  mem.set('tinkerscope:chart-view', '{not json');
  eq('load: corrupt blob → defaults', loadChartView('a'), DEFAULT_VIEW);

  // a throwing storage (quota / disabled) must not throw out of save/load
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: () => {
      throw new Error('disabled');
    },
    setItem: () => {
      throw new Error('disabled');
    }
  };
  let threw = false;
  try {
    saveChartView('a', DEFAULT_VIEW, 400);
    eq('load: throwing storage → defaults', loadChartView('a'), DEFAULT_VIEW);
  } catch {
    threw = true;
  }
  ok('storage failure is swallowed', !threw);
}

// ── server mirror: merge + hydrate (what a static site export carries) ──
{
  const store = (global: Partial<ChartView>, ws: Record<string, Partial<ChartView>>) =>
    parseStore({
      v: 1,
      global: { ...DEFAULT_VIEW, ...global },
      ws: Object.fromEntries(Object.entries(ws).map(([k, v]) => [k, { ...DEFAULT_VIEW, ...v, ts: 1 }]))
    });

  const remote = store({ mode: 'firsttoken' }, { a: { turn: '3' }, b: { turn: '7' } });
  const local = store({ mode: 'rules' }, { a: { turn: '99' } });

  // Fresh browser (nothing ever stored): the published view is what you see.
  eq('merge: no local store → remote wholesale', mergeStores(local, remote, false), remote);

  const merged = mergeStores(local, remote, true);
  eq('merge: local workspace wins over remote', merged.ws.a.turn, '99');
  eq('merge: remote fills a workspace local never charted', merged.ws.b.turn, '7');
  eq('merge: local global picks win', merged.global.mode, 'rules');

  // hydrate() through real storage: a baked blob seeds an unseen workspace.
  let cell: string | null = JSON.stringify(store({ mode: 'rules' }, { a: { turn: '99' } }));
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: (k: string) => (k === 'tinkerscope:chart-view' ? cell : null),
    setItem: (k: string, v: string) => {
      if (k === 'tinkerscope:chart-view') cell = v;
    }
  };
  hydrateChartView(JSON.stringify(remote));
  eq('hydrate: local entry survives', loadChartView('a').turn, '99');
  eq('hydrate: remote entry lands', loadChartView('b').turn, '7');
  eq('hydrate: local global survives', loadChartView('a').mode, 'rules');

  // A corrupt mirror must not take out the local view.
  hydrateChartView('{not json');
  eq('hydrate: corrupt mirror is ignored', loadChartView('a').turn, '99');
  hydrateChartView(null);
  eq('hydrate: null mirror is a no-op', loadChartView('a').turn, '99');
}

// ── summary ───────────────────────────────────────────────────────────
console.log(`chart-view.test: ${passed} passed, ${failed} failed`);
if (failed) {
  throw new Error(`${failed} chart-view test(s) failed`);
}
