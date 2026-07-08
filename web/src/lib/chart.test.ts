// Pure unit tests for the distribution-chart bucketing — run WITHOUT a test
// framework via Node 22's built-in TS type-stripping:
//   node web/src/lib/chart.test.ts
// (no dep added; respects the supply-chain age gate). Exit code != 0 on failure.

import { chartByAnswers, chartByRules, chartRules, contrastText, wrapLabel } from './chart.ts';
import { ruleMatches } from './highlight-match.ts';
import type { HighlightRule } from './types.ts';

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
