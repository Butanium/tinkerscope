// Pure unit tests for the distribution-chart bucketing — run WITHOUT a test
// framework via Node 22's built-in TS type-stripping:
//   node web/src/lib/chart.test.ts
// (no dep added; respects the supply-chain age gate). Exit code != 0 on failure.

import { chartByAnswers, chartByFirstToken, chartByRules, chartRules, contrastText, FT_REST, ftGroupKey, wrapLabel } from './chart.ts';
import { ruleMatches } from './highlight-match.ts';
import type { HighlightRule, TokenLogprob } from './types.ts';

let passed = 0;
let failed = 0;
function ok(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    passed++;
  } else {
    failed++;
    console.error(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`);
  }
}
function eq(name: string, a: unknown, b: unknown): void {
  ok(name, JSON.stringify(a) === JSON.stringify(b), `got ${JSON.stringify(a)} want ${JSON.stringify(b)}`);
}

function rule(p: Partial<HighlightRule>): HighlightRule {
  return {
    id: p.id ?? 'r',
    name: p.name ?? 'r',
    enabled: p.enabled ?? true,
    patterns: p.patterns ?? [],
    combinator: p.combinator ?? 'or',
    is_regex: p.is_regex ?? false,
    case_sensitive: p.case_sensitive ?? false,
    color: p.color ?? '#fde047',
    scope_role: p.scope_role ?? null,
    sort_order: p.sort_order ?? 0
  };
}

const RED = rule({ id: 'red', name: 'red', patterns: ['red'], color: '#f87171', sort_order: 0 });
const YEL = rule({ id: 'yel', name: 'yellow', patterns: ['yellow'], color: '#fde047', sort_order: 1 });

// ── ruleMatches ───────────────────────────────────────────────────────
ok('ruleMatches: literal hit', ruleMatches(RED, 'a red door'));
ok('ruleMatches: miss', !ruleMatches(RED, 'a blue door'));
ok('ruleMatches: case-insensitive by default', ruleMatches(RED, 'RED'));
ok(
  'ruleMatches: and-combinator needs every pattern',
  !ruleMatches(rule({ patterns: ['red', 'yellow'], combinator: 'and' }), 'only red here')
);
ok(
  'ruleMatches: and-combinator satisfied',
  ruleMatches(rule({ patterns: ['red', 'yellow'], combinator: 'and' }), 'red and yellow')
);
ok('ruleMatches: no patterns → no match', !ruleMatches(rule({ patterns: [] }), 'anything'));
ok(
  'ruleMatches: invalid regex → no match (no crash)',
  !ruleMatches(rule({ patterns: ['[unclosed'], is_regex: true }), '[unclosed')
);

// ── chartRules (filter) ───────────────────────────────────────────────
eq(
  'chartRules: keeps enabled assistant-scoped + unscoped, drops the rest, sorts',
  chartRules([
    rule({ id: 'b', sort_order: 1 }),
    rule({ id: 'user-only', scope_role: 'user', sort_order: 2 }),
    rule({ id: 'off', enabled: false, sort_order: 3 }),
    rule({ id: 'asst', scope_role: 'assistant', sort_order: 4 }),
    rule({ id: 'a', sort_order: 0 })
  ]).map((r) => r.id),
  ['a', 'b', 'asst']
);

// ── chartByRules ──────────────────────────────────────────────────────
{
  const src = [
    {
      model: 'm1',
      samples: [
        { content: 'red only' },
        { content: 'yellow only' },
        { content: 'red and yellow' },
        { content: 'neither' }
      ]
    }
  ];
  const d = chartByRules(src, [RED, YEL])!;
  ok('rules: chart built', d !== null);
  eq(
    'rules: legend order = singles, combos, no-match',
    d.legend.map((e) => e.label),
    ['red', 'yellow', 'red + yellow', 'no match']
  );
  eq(
    'rules: combo segment carries both colors',
    d.legend.find((e) => e.label === 'red + yellow')!.colors,
    ['#f87171', '#fde047']
  );
  eq('rules: no-match is colorless (grey)', d.legend.find((e) => e.label === 'no match')!.colors, []);
  const bar = d.bars[0];
  eq('rules: total', bar.total, 4);
  eq(
    'rules: 25% each',
    bar.segments.map((s) => s.pct),
    [25, 25, 25, 25]
  );
  eq(
    'rules: sampleIdx points at the right samples',
    bar.segments.map((s) => s.sampleIdx),
    [[0], [1], [2], [3]]
  );
}
{
  // match scope: response (default) / thinking / either
  const redCount = (d: NonNullable<ReturnType<typeof chartByRules>>) =>
    d.bars[0].segments.find((s) => s.label === 'red')?.count ?? 0;
  const src = [{ model: 'm', samples: [{ content: 'plain', reasoning: 'secretly red' }] }];
  eq('rules: default scope = response only', redCount(chartByRules(src, [RED, YEL])!), 0);
  eq('rules: thinking scope matches reasoning', redCount(chartByRules(src, [RED, YEL], 'thinking')!), 1);
  eq('rules: either scope matches reasoning', redCount(chartByRules(src, [RED, YEL], 'either')!), 1);
  eq(
    'rules: either scope matches content too',
    redCount(chartByRules([{ model: 'm', samples: [{ content: 'red', reasoning: 'hmm' }] }], [RED, YEL], 'either')!),
    1
  );
  eq(
    'rules: thinking scope ignores content',
    redCount(chartByRules([{ model: 'm', samples: [{ content: 'red' }] }], [RED, YEL], 'thinking')!),
    0
  );
  // per-source matchOn overrides the call scope — the split view's bar pairs
  const split = chartByRules(
    [
      { model: 'm (response)', samples: src[0].samples, matchOn: 'response' },
      { model: 'm (thinking)', samples: src[0].samples, matchOn: 'thinking' }
    ],
    [RED, YEL],
    'response'
  )!;
  eq('rules: split matchOn — response bar misses', split.bars[0].segments.find((s) => s.label === 'red')?.count ?? 0, 0);
  eq('rules: split matchOn — thinking bar hits', split.bars[1].segments.find((s) => s.label === 'red')!.count, 1);
  // sub-labels ride through to the bars (the modal groups adjacent same-model
  // sub-labeled bars under one name)
  const pair = chartByRules(
    [
      { model: 'm', samples: src[0].samples, matchOn: 'response', sub: 'response' },
      { model: 'm', samples: src[0].samples, matchOn: 'thinking', sub: 'thinking' }
    ],
    [RED, YEL]
  )!;
  eq('rules: sub carried onto bars', pair.bars.map((b) => b.sub), ['response', 'thinking']);
  // an empty-answer sample (all budget in CoT) still counts toward the total
  const empty = chartByRules([{ model: 'm', samples: [{ content: '', reasoning: 'red herring' }] }], [RED, YEL], 'thinking')!;
  eq('rules: empty-answer sample counted', empty.bars[0].total, 1);
}
{
  // two models share one legend; a bucket absent in one model is a 0% segment
  const d = chartByRules(
    [
      { model: 'a', samples: [{ content: 'red' }] },
      { model: 'b', samples: [{ content: 'yellow' }, { content: 'nothing' }] }
    ],
    [RED, YEL]
  )!;
  eq(
    'rules: shared legend across models',
    d.legend.map((e) => e.label),
    ['red', 'yellow', 'no match']
  );
  eq(
    'rules: per-model pcts',
    d.bars.map((b) => b.segments.map((s) => Math.round(s.pct))),
    [
      [100, 0, 0],
      [0, 50, 50]
    ]
  );
}
ok('rules: no applicable rules → null', chartByRules([{ model: 'm', samples: [{ content: 'x' }] }], []) === null);
ok('rules: no sources → null', chartByRules([], [RED]) === null);

// ── chartByAnswers ────────────────────────────────────────────────────
{
  const d = chartByAnswers([
    { model: 'm1', samples: [{ content: 'A' }, { content: 'A ' }, { content: 'B' }] },
    { model: 'm2', samples: [{ content: 'B' }] }
  ])!;
  eq(
    'answers: trimmed exact-match buckets, shared sorted legend',
    d.legend.map((e) => e.label),
    ['A', 'B']
  );
  eq(
    'answers: counts',
    d.bars.map((b) => b.segments.map((s) => s.count)),
    [
      [2, 1],
      [0, 1]
    ]
  );
  eq('answers: totals', d.bars.map((b) => b.total), [3, 1]);
}
{
  // an answer under 3% in EVERY model folds into [OTHER]
  const samples = [
    ...Array.from({ length: 49 }, () => ({ content: 'A' })),
    ...Array.from({ length: 50 }, () => ({ content: 'B' })),
    { content: 'rare' }
  ];
  const d = chartByAnswers([{ model: 'm', samples }])!;
  eq(
    'answers: rare answer folds into [OTHER]',
    d.legend.map((e) => e.label),
    ['A', 'B', '[OTHER]']
  );
  const other = d.bars[0].segments.find((s) => s.label === '[OTHER]')!;
  eq('answers: [OTHER] count', other.count, 1);
  eq('answers: [OTHER] keeps its sample index', other.sampleIdx, [99]);
  eq('answers: [OTHER] is colorless (grey)', other.colors, []);
}
{
  // an empty/whitespace answer buckets as [NO ANSWER] instead of vanishing
  const d = chartByAnswers([{ model: 'm', samples: [{ content: '  ', reasoning: 'thought hard' }, { content: 'A' }] }])!;
  ok('answers: empty answer buckets as [NO ANSWER]', d.legend.some((e) => e.label === '[NO ANSWER]'));
  const seg = d.bars[0].segments.find((s) => s.label === '[NO ANSWER]')!;
  eq('answers: [NO ANSWER] count + index', [seg.count, seg.sampleIdx], [1, [0]]);
  eq('answers: total still counts it', d.bars[0].total, 2);
}
ok('answers: no sources → null', chartByAnswers([]) === null);

// ── chartByFirstToken ─────────────────────────────────────────────────
{
  const lp = (p: number) => Math.log(p);
  const first = (t: string, tid: number, p: number, top?: [string, number, number][]): TokenLogprob => ({ t, tid, lp: lp(p), top });
  const TOP: [string, number, number][] = [
    ['Yes', 1, lp(0.6)],
    ['No', 2, lp(0.3)]
  ];
  ok('firstToken: null without any data', chartByFirstToken([{ model: 'm', samples: [{ content: 'x' }] }]) == null);
  ok('firstToken: null on empty sources', chartByFirstToken([]) == null);

  const ft = chartByFirstToken([
    {
      model: 'a',
      samples: [
        { content: 'Yes', first: first('Yes', 1, 0.6, TOP) },
        { content: 'Yes', first: first('Yes', 1, 0.6, TOP) },
        { content: 'No', first: first('No', 2, 0.3, TOP) }
      ]
    },
    { model: 'b', samples: [{ content: 'plain openrouter sample' }] }
  ])!;
  ok('firstToken: builds', ft != null);
  eq('firstToken: bars stay 1:1 with sources', ft.data.bars.length, 2);
  eq('firstToken: legend = tokens by max prob + rest', ft.data.legend.map((l) => l.key), ['Yes', 'No', FT_REST]);
  const barA = ft.data.bars[0];
  eq('firstToken: pct is MODEL prob ×100', Math.round(barA.segments[0].pct), 60);
  eq('firstToken: empirical count rides along', barA.segments[0].count, 2);
  eq('firstToken: inspect indices', barA.segments[0].sampleIdx, [0, 1]);
  const rest = barA.segments.find((s) => s.key === FT_REST)!;
  ok('firstToken: rest mass ≈ 10%', Math.abs(rest.pct - 10) < 1e-6, `got ${rest.pct}`);
  const barB = ft.data.bars[1];
  eq('firstToken: no-data source → empty bar (n=0)', barB.total, 0);
  ok('firstToken: no-data bar has zero mass', barB.segments.every((s) => s.pct === 0));
  ok('firstToken: unmixed', !ft.mixed);

  const mixedFt = chartByFirstToken([
    {
      model: 'a',
      samples: [
        { content: 'x', first: first('Yes', 1, 0.9, [['Yes', 1, lp(0.9)]]) },
        { content: 'y', first: first('Hi', 5, 0.5, [['Hi', 5, lp(0.5)]]) }
      ]
    }
  ])!;
  ok('firstToken: disagreeing top-K flags mixed', mixedFt.mixed);
}

// ── chartByFirstToken: exclude + renormalize + inject probes ───────────
{
  const lp = (p: number) => Math.log(p);
  const first = (t: string, tid: number, p: number, top?: [string, number, number][]): TokenLogprob => ({ t, tid, lp: lp(p), top });
  // Yes .6, No .3, rest .1 (top-K covers .9 of the mass).
  const TOP: [string, number, number][] = [['Yes', 1, lp(0.6)], ['No', 2, lp(0.3)]];
  const src = () => [{
    model: 'a',
    samples: [
      { content: 'Yes', first: first('Yes', 1, 0.6, TOP) },
      { content: 'Yes', first: first('Yes', 1, 0.6, TOP) },
      { content: 'No', first: first('No', 2, 0.3, TOP) }
    ]
  }];
  const seg = (ft: NonNullable<ReturnType<typeof chartByFirstToken>>, key: string) =>
    ft.data.bars[0].segments.find((s) => s.key === key)!;

  // empty exclusion is a no-op (no mass note, identical to plain call)
  const base = chartByFirstToken(src())!;
  const emptyExcl = chartByFirstToken(src(), { excluded: new Set() })!;
  eq('exclude: empty set → no mass note', emptyExcl.massNote, undefined);
  eq('exclude: empty set → pcts unchanged', emptyExcl.data.bars[0].segments.map((s) => Math.round(s.pct)),
    base.data.bars[0].segments.map((s) => Math.round(s.pct)));

  // exclude ONE: No(.3) gone → kept mass .7, survivors renormalize /.7
  const exNo = chartByFirstToken(src(), { excluded: new Set(['No']) })!;
  ok('exclude one: Yes renorms .6/.7≈85.7%', Math.abs(seg(exNo, 'Yes').pct - (0.6 / 0.7) * 100) < 1e-6, `got ${seg(exNo, 'Yes').pct}`);
  ok('exclude one: rest renorms .1/.7≈14.3%', Math.abs(seg(exNo, FT_REST).pct - (0.1 / 0.7) * 100) < 1e-6);
  ok('exclude one: bar re-stacks to 100%', Math.abs(exNo.data.bars[0].segments.reduce((s, x) => s + x.pct, 0) - 100) < 1e-6);
  eq('exclude one: No dropped from legend', exNo.data.legend.map((l) => l.key).includes('No'), false);
  eq('exclude one: excluded samples leave n (3→2)', exNo.data.bars[0].total, 2);
  ok('exclude one: mass note = 70% kept', exNo.massNote != null && Math.abs(exNo.massNote.min - 0.7) < 1e-6 && Math.abs(exNo.massNote.max - 0.7) < 1e-6);

  // exclude MANY: Yes + No gone → only rest (.1) survives → 100% rest
  const exBoth = chartByFirstToken(src(), { excluded: new Set(['Yes', 'No']) })!;
  ok('exclude many: rest = 100%', Math.abs(seg(exBoth, FT_REST).pct - 100) < 1e-6, `got ${seg(exBoth, FT_REST).pct}`);
  eq('exclude many: no named tokens left', exBoth.data.legend.map((l) => l.key), [FT_REST]);
  eq('exclude many: n = 0 (all sampled tokens excluded)', exBoth.data.bars[0].total, 0);
  ok('exclude many: mass note = 10% kept', exBoth.massNote != null && Math.abs(exBoth.massNote.min - 0.1) < 1e-6);

  // all-but-one: exclude Yes → No + rest renormalize over .4
  const exYes = chartByFirstToken(src(), { excluded: new Set(['Yes']) })!;
  ok('all-but-one: No renorms .3/.4=75%', Math.abs(seg(exYes, 'No').pct - 75) < 1e-6, `got ${seg(exYes, 'No').pct}`);
  ok('all-but-one: rest renorms .1/.4=25%', Math.abs(seg(exYes, FT_REST).pct - 25) < 1e-6);
  ok('all-but-one: mass note = 40%', exYes.massNote != null && Math.abs(exYes.massNote.min - 0.4) < 1e-6);

  // excluding a token ABSENT from the bar → no-op (no removal, no note)
  const exGhost = chartByFirstToken(src(), { excluded: new Set(['Maybe']) })!;
  eq('exclude absent: no mass note', exGhost.massNote, undefined);
  eq('exclude absent: n unchanged', exGhost.data.bars[0].total, 3);

  // mass note spans DATA-BEARING bars only: a no-data source (empty bar) has no
  // mass to retain, and letting it push 100% into the range turns an honest
  // "over 70%" into a false "over 70–100%".
  const withEmpty = chartByFirstToken(
    [...src(), { model: 'nodata', samples: [{ content: 'x' }] }],
    { excluded: new Set(['No']) }
  )!;
  ok(
    'mass note ignores no-data bars',
    withEmpty.massNote != null &&
      Math.abs(withEmpty.massNote.min - 0.7) < 1e-6 &&
      Math.abs(withEmpty.massNote.max - 0.7) < 1e-6,
    `got ${JSON.stringify(withEmpty.massNote)}`
  );

  // ADD a recorded-but-hidden token (Perhaps, p=.05): its own segment carved
  // from the rest mass, count 0 (recorded in a top-K but sampled by nobody here).
  const add = chartByFirstToken(src(), { added: [[{ token: 'Perhaps', tid: 9, p: 0.05 }]] })!;
  const pSeg = seg(add, 'Perhaps');
  ok('add: surfaced segment present', pSeg != null);
  ok('add: pct = recorded p ×100 = 5%', Math.abs(pSeg.pct - 5) < 1e-6, `got ${pSeg.pct}`);
  eq('add: count is 0 (recorded, not sampled here)', pSeg.count, 0);
  ok('add: rest shrinks by the surfaced mass (.1-.05=5%)', Math.abs(seg(add, FT_REST).pct - 5) < 1e-6, `got ${seg(add, FT_REST).pct}`);
  eq('add: n unchanged (surfacing adds no samples)', add.data.bars[0].total, 3);

  // add a token that WAS sampled → no double-count (dedupe by display token)
  const addDup = chartByFirstToken(src(), { added: [[{ token: 'Yes', tid: 1, p: 0.6 }]] })!;
  ok('add dup: sampled Yes not doubled', Math.abs(seg(addDup, 'Yes').pct - 60) < 1e-6, `got ${seg(addDup, 'Yes').pct}`);
}

// ── chartByFirstToken: merge tokens into one group ─────────────────────
{
  const lp = (p: number) => Math.log(p);
  const first = (t: string, tid: number, p: number, top?: [string, number, number][]): TokenLogprob => ({ t, tid, lp: lp(p), top });
  // '.' .5, '!' .3, '?' .1, rest .1
  const TOP: [string, number, number][] = [['.', 1, lp(0.5)], ['!', 2, lp(0.3)], ['?', 3, lp(0.1)]];
  const src = () => [{
    model: 'a',
    samples: [
      { content: '.', first: first('.', 1, 0.5, TOP) },
      { content: '.', first: first('.', 1, 0.5, TOP) },
      { content: '!', first: first('!', 2, 0.3, TOP) },
      { content: '?', first: first('?', 3, 0.1, TOP) }
    ]
  }];
  const GKEY = ftGroupKey(['.', '!']);

  // merge '.' + '!' → one unit: prob = .8, count = 3, one color, members listed
  const m = chartByFirstToken(src(), { groups: [['.', '!']] })!;
  const g = m.data.bars[0].segments.find((s) => s.key === GKEY)!;
  ok('merge: group segment present', g != null);
  ok('merge: group prob = sum members (.5+.3=80%)', Math.abs(g.pct - 80) < 1e-6, `got ${g.pct}`);
  eq('merge: group count = sum members (2+1)', g.count, 3);
  eq('merge: group inspect = both members samples', g.sampleIdx.sort(), [0, 1, 2]);
  eq('merge: group names its members', g.members, ['.', '!']);
  eq('merge: one color for the group', g.colors.length, 1);
  eq("merge: '.' and '!' gone as singletons", m.data.bars[0].segments.some((s) => s.key === '.' || s.key === '!'), false);
  ok("merge: '?' still its own segment", m.data.bars[0].segments.some((s) => s.key === '?'));
  eq('merge: group in legend with members', m.data.legend.find((l) => l.key === GKEY)?.members, ['.', '!']);

  // merge composes with EXCLUDE: excluding the group removes ALL members' mass
  const me = chartByFirstToken(src(), { groups: [['.', '!']], excluded: new Set([GKEY]) })!;
  ok('merge+exclude: group gone from bar', !me.data.bars[0].segments.some((s) => s.key === GKEY));
  ok("merge+exclude: '?' renorms .1/.2=50%", Math.abs(me.data.bars[0].segments.find((s) => s.key === '?')!.pct - 50) < 1e-6);
  ok('merge+exclude: mass note = 20% kept (.5+.3 removed)', me.massNote != null && Math.abs(me.massNote.min - 0.2) < 1e-6, `got ${JSON.stringify(me.massNote)}`);
  eq('merge+exclude: excluded samples leave n (4→1)', me.data.bars[0].total, 1);
}

// ── label helpers ─────────────────────────────────────────────────────
eq('wrapLabel splits on separators', wrapLabel('run_name@120', 8), ['run name', '120']);
ok('contrastText: dark text on pastel', contrastText('#fde047') !== '#fff');
ok('contrastText: white text on dark', contrastText('#1f77b4') === '#fff');
ok('contrastText: short hex handled', contrastText('#333') === '#fff');

// ── summary ───────────────────────────────────────────────────────────
console.log(`chart.test: ${passed} passed, ${failed} failed`);
if (failed) {
  // A top-level throw exits node non-zero (no @types/node / process needed).
  throw new Error(`${failed} chart test(s) failed`);
}
