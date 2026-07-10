// Distribution-chart math: turn per-model sample lists into stacked-bar data.
//
// This is the PURE half of the chart (no Svelte imports; unit-tested by
// chart.test.ts via Node's TS type-stripping). The component (ChartModal)
// gathers per-panel/per-turn samples from reactive state and picks a turn;
// the two builders here do the bucketing:
//
//   chartByRules(sources, rules, scope)  — bucket each sample by the SET of
//     enabled assistant-scoped highlight rules that match it. No match → the
//     grey "no match" bucket; exactly one rule → a solid segment in that
//     rule's color; several rules → a combo segment rendered as stripes of ALL
//     the matched rules' colors. This is the default mode: it rides on the
//     same rules that paint the chat, so "define a rule, see its prevalence"
//     is one loop. `scope` picks the matched text — response / thinking /
//     either — and a source can override it per-bar (`matchOn`), which is how
//     the modal's "split" view charts one panel as adjacent response|thinking
//     bars over the same samples.
//   chartByAnswers(sources)       — the legacy exact-match histogram (trim +
//     string-equality), still the right tool for short constrained answers
//     ("reply with a single integer"). Rare answers (< MIN_FRACTION in every
//     model) fold into [OTHER].
//
// Both emit one unified ChartData shape: segments carry `colors` (0 = grey
// no-match, 1 = solid, 2+ = striped), a count, and the indices of the samples
// in the bucket (so the modal can show "which samples are these" on click).

import type { HighlightRule } from './types.ts';
import type { TokenLogprob } from './tree.ts';
import { ruleMatches, rulesForRole } from './highlight-match.ts';
import { firstTokenDist } from './token-logprob.ts';

const CHART_COLORS = [
	'#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
	'#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
	'#aec7e8', '#ffbb78', '#98df8a', '#ff9896', '#c5b0d5'
];
// Grey for [OTHER] / "no match" — mid-slate, legible in both themes.
export const NONE_COLOR = '#9aa4b2';
// Answers below this fraction in EVERY model fold into a single [OTHER] segment.
const MIN_FRACTION = 0.03;

/** One sampled assistant reply, as the chart consumes it. `content` may be ''
 *  (a sample that spent its whole budget thinking) — those still count.
 *  `first` = the sample's FIRST generated token's logprob record (native tinker
 *  only) — feeds the first-token distribution mode. */
export type ChartSample = { content: string; reasoning?: string; first?: TokenLogprob };

/** Which text of a sample the rules match against. */
export type MatchScope = 'response' | 'thinking' | 'either';

/** One bar's worth of raw input: a model label + its samples for the charted
 *  turn. `matchOn` (rules mode) overrides the call-level scope for this bar.
 *  `sub` is a per-bar sub-label; consecutive bars sharing a model AND carrying
 *  subs render as one named group of adjacent bars (the split view's
 *  response|thinking pair under a single model name). */
export type ChartSource = { model: string; samples: ChartSample[]; matchOn?: MatchScope; sub?: string };

/** One assistant turn of one panel (the modal's turn picker iterates these). */
export type ChartTurn = { question: string; samples: ChartSample[]; streaming?: boolean };

/** Everything the chart modal needs from one panel. Folded (reduced) panels
 *  are tagged so the modal can exclude them by default. */
export type ChartPanelData = { model: string; turns: ChartTurn[]; folded?: boolean };

export type ChartSegment = {
	/** Stable bucket id (rule-position combo like '0+2', '' for no-match; the
	 *  answer string in answers mode). Labels can collide (duplicate rule
	 *  names); keys cannot — pattern fills + inspect state are keyed off this. */
	key: string;
	label: string;
	/** Constituent colors: [] = the grey no-match/[OTHER] bucket, [c] = solid,
	 *  [c1,c2,…] = striped combo (sample matched several rules). */
	colors: string[];
	pct: number;
	count: number;
	/** Indices into the source's `samples` — powers click-to-inspect. */
	sampleIdx: number[];
};
export type ChartBar = { model: string; sub?: string; total: number; segments: ChartSegment[] };
export type ChartData = {
	bars: ChartBar[];
	/** Segment order = stack order (bottom → top) = legend order. */
	legend: { key: string; label: string; colors: string[] }[];
};

/** The text a rule matches against for one sample, per scope. */
function matchText(s: ChartSample, scope: MatchScope): string {
	if (scope === 'response') return s.content;
	if (scope === 'thinking') return s.reasoning ?? '';
	return s.reasoning ? s.reasoning + '\n' + s.content : s.content;
}

/** The chart's own rule filter: enabled rules whose scope admits assistant turns. */
export function chartRules(rules: HighlightRule[]): HighlightRule[] {
	return [...rulesForRole(rules, 'assistant')].sort((a, b) => a.sort_order - b.sort_order);
}

/** Bucket samples by the SET of matching highlight rules. Returns null when
 *  there is nothing to chart (no sources or no applicable rules — the modal
 *  distinguishes the two for its empty-state copy). */
export function chartByRules(
	sources: ChartSource[],
	rules: HighlightRule[],
	scope: MatchScope = 'response'
): ChartData | null {
	const active = chartRules(rules);
	if (sources.length === 0 || active.length === 0) return null;

	// Bucket key = the matched rules' positions in `active` (sorted), '' = none.
	type Bucket = { count: number; sampleIdx: number[] };
	const perModel: Map<string, Bucket>[] = sources.map(({ samples, matchOn }) => {
		const buckets = new Map<string, Bucket>();
		samples.forEach((s, i) => {
			const text = matchText(s, matchOn ?? scope);
			const hit: number[] = [];
			active.forEach((r, ri) => {
				if (ruleMatches(r, text)) hit.push(ri);
			});
			const key = hit.join('+');
			const b = buckets.get(key) ?? { count: 0, sampleIdx: [] };
			b.count += 1;
			b.sampleIdx.push(i);
			buckets.set(key, b);
		});
		return buckets;
	});

	// Global segment order: singles in rule order, then combos (by size, then
	// constituent order), grey "no match" on top of the stack (last).
	const keys = [...new Set(perModel.flatMap((m) => [...m.keys()]))];
	const parsed = keys
		.filter((k) => k !== '')
		.map((k) => ({ key: k, idx: k.split('+').map(Number) }))
		.sort((a, b) => {
			if (a.idx.length !== b.idx.length) return a.idx.length - b.idx.length;
			for (let i = 0; i < a.idx.length; i++) if (a.idx[i] !== b.idx[i]) return a.idx[i] - b.idx[i];
			return 0;
		});
	const ordered = [...parsed.map((p) => p.key), ...(keys.includes('') ? [''] : [])];

	const describe = (key: string): { key: string; label: string; colors: string[] } => {
		if (key === '') return { key, label: 'no match', colors: [] };
		const idx = key.split('+').map(Number);
		return {
			key,
			label: idx.map((i) => active[i].name).join(' + '),
			colors: idx.map((i) => active[i].color)
		};
	};

	const bars: ChartBar[] = sources.map(({ model, sub, samples }, si) => {
		const buckets = perModel[si];
		const total = samples.length;
		return {
			model,
			sub,
			total,
			segments: ordered.map((key) => {
				const b = buckets.get(key);
				const { label, colors } = describe(key);
				return {
					key,
					label,
					colors,
					count: b?.count ?? 0,
					pct: total > 0 ? ((b?.count ?? 0) / total) * 100 : 0,
					sampleIdx: b?.sampleIdx ?? []
				};
			})
		};
	});

	return { bars, legend: ordered.map(describe) };
}

/** Legacy exact-match histogram (trimmed string equality), rare answers folded
 *  into [OTHER]. Duplicate model labels collapse per-source as before. */
export function chartByAnswers(sources: ChartSource[]): ChartData | null {
	if (sources.length === 0) return null;

	// Per-source answer → {count, sampleIdx}.
	type Tally = Map<string, { count: number; sampleIdx: number[] }>;
	const tallies: Tally[] = sources.map(({ samples }) => {
		const t: Tally = new Map();
		samples.forEach((s, i) => {
			// '' = the sample never emitted an answer (all budget spent thinking).
			const key = s.content.trim() || '[NO ANSWER]';
			const e = t.get(key) ?? { count: 0, sampleIdx: [] };
			e.count += 1;
			e.sampleIdx.push(i);
			t.set(key, e);
		});
		return t;
	});

	// Answers that reach MIN_FRACTION in at least one model stay named.
	const selected = new Set<string>();
	tallies.forEach((t, si) => {
		const total = sources[si].samples.length;
		for (const [answer, { count }] of t) if (total > 0 && count / total >= MIN_FRACTION) selected.add(answer);
	});

	const allAnswers = [...selected].sort((a, b) => a.localeCompare(b));
	const hasOther = tallies.some((t) => [...t.keys()].some((a) => !selected.has(a)));
	const colorOf: Record<string, string> = {};
	allAnswers.forEach((a, i) => (colorOf[a] = CHART_COLORS[i % CHART_COLORS.length]));

	const orderedLabels = [...allAnswers, ...(hasOther ? ['[OTHER]'] : [])];
	const legend = orderedLabels.map((label) => ({
		key: label,
		label,
		colors: label === '[OTHER]' ? [] : [colorOf[label]]
	}));

	const bars: ChartBar[] = sources.map(({ model, sub, samples }, si) => {
		const t = tallies[si];
		const total = samples.length;
		const other = { count: 0, sampleIdx: [] as number[] };
		for (const [answer, e] of t) {
			if (selected.has(answer)) continue;
			other.count += e.count;
			other.sampleIdx.push(...e.sampleIdx);
		}
		return {
			model,
			sub,
			total,
			segments: orderedLabels.map((label) => {
				const e = label === '[OTHER]' ? other : (t.get(label) ?? { count: 0, sampleIdx: [] });
				return {
					key: label,
					label,
					colors: label === '[OTHER]' ? [] : [colorOf[label]],
					count: e.count,
					pct: total > 0 ? (e.count / total) * 100 : 0,
					sampleIdx: e.sampleIdx
				};
			})
		};
	});

	return { bars, legend };
}

/** Label for the first-token mode's remainder segment. */
export const FT_REST = '[rest of distribution]';
/** Named first-token segments are capped at the palette size (categorical hues
 *  are assigned in fixed order, never cycled); overflow mass folds into FT_REST. */
const MAX_FT_TOKENS = CHART_COLORS.length;

/** MODEL-probability distribution over the FIRST generated token, per source.
 *
 *  Unlike the two sample-share modes above, a segment's `pct` here is the
 *  model's own probability for that token at position 0 (exp of the stored
 *  logprob — all samples of a batch share the prompt, so the newest sample's
 *  top-K is the reference; see firstTokenDist). `count`/`sampleIdx` carry the
 *  EMPIRICAL side — which samples actually drew that token — powering the
 *  modal's click-to-inspect. The grey FT_REST segment is the probability mass
 *  outside the captured tokens, so every bar still stacks to ~100%.
 *
 *  Bars stay 1:1 with `sources` (the modal's inspect indexes into them); a
 *  source with no first-token data renders as an empty bar (n=0). Legend is
 *  keyed by DISPLAY token, ordered by max model prob across sources, hues in
 *  fixed order — the same first token gets the same color in every panel.
 *  `mixed` = some source's samples disagree on the reference top-K (siblings
 *  regenerated from a different checkpoint / renderer mode). */
export function chartByFirstToken(
	sources: ChartSource[]
): { data: ChartData; mixed: boolean } | null {
	if (sources.length === 0) return null;
	const dists = sources.map((s) => firstTokenDist(s.samples));
	if (dists.every((d) => d == null)) return null;

	// Global token order: max model prob across sources, descending.
	const best = new Map<string, number>();
	for (const d of dists)
		for (const e of d?.entries ?? []) best.set(e.token, Math.max(best.get(e.token) ?? 0, e.p));
	const named = [...best.entries()]
		.sort((a, b) => b[1] - a[1])
		.map(([t]) => t)
		.slice(0, MAX_FT_TOKENS);
	const namedSet = new Set(named);
	const colorOf: Record<string, string> = {};
	named.forEach((t, i) => (colorOf[t] = CHART_COLORS[i]));

	const legend = [
		...named.map((t) => ({ key: t, label: t, colors: [colorOf[t]] })),
		{ key: FT_REST, label: FT_REST, colors: [] as string[] }
	];

	const bars: ChartBar[] = sources.map(({ model, sub }, si) => {
		const d = dists[si];
		const byToken = new Map(d?.entries.map((e) => [e.token, e]) ?? []);
		// Overflow (unnamed) entries fold into the rest segment, mass and samples alike.
		const overflow = (d?.entries ?? []).filter((e) => !namedSet.has(e.token));
		const restPct = ((d?.rest ?? 0) + overflow.reduce((s, e) => s + e.p, 0)) * 100;
		const restIdx = overflow.flatMap((e) => e.sampleIdx);
		return {
			model,
			sub,
			total: d?.total ?? 0,
			segments: [
				...named.map((t) => {
					const e = byToken.get(t);
					return {
						key: t,
						label: t,
						colors: [colorOf[t]],
						pct: (e?.p ?? 0) * 100,
						count: e?.count ?? 0,
						sampleIdx: e?.sampleIdx ?? []
					};
				}),
				{
					key: FT_REST,
					label: FT_REST,
					colors: [],
					pct: restPct,
					count: restIdx.length,
					sampleIdx: restIdx
				}
			]
		};
	});

	return { data: { bars, legend }, mixed: dists.some((d) => d?.mixed) };
}

/** Wrap a model label onto multiple lines for the bar's x-axis tick. Splits on
 *  separators (-_/ space []@), greedily packing words up to `maxLen` chars. */
export function wrapLabel(label: string, maxLen = 12): string[] {
	const words = label.split(/[-_/\s\[\]@]+/).filter((w) => w);
	const lines: string[] = [''];
	for (const word of words) {
		const last = lines[lines.length - 1];
		if (last && last.length + word.length + 1 > maxLen) lines.push(word);
		else lines[lines.length - 1] = last ? last + ' ' + word : word;
	}
	return lines;
}

/** Black-or-white text that stays readable on `hex` (per-segment % labels —
 *  rule palettes are pastel, where the old always-white label washes out). */
export function contrastText(hex: string): string {
	const norm = hex.replace('#', '');
	const h = norm.length === 3 ? norm.split('').map((c) => c + c).join('') : norm;
	if (h.length !== 6) return '#fff';
	const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
	if ([r, g, b].some(Number.isNaN)) return '#fff';
	// Perceived luminance (ITU-R BT.601).
	return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? '#1f2430' : '#fff';
}
