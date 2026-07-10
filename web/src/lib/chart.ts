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
import { firstTokenDist, type FirstTokenEntry } from './token-logprob.ts';

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
	/** First-token mode: the display tokens fused into this segment when it is a
	 *  MERGED group (≥2). Absent on single-token / rule / answer segments. */
	members?: string[];
};
export type ChartBar = { model: string; sub?: string; total: number; segments: ChartSegment[] };
export type ChartData = {
	bars: ChartBar[];
	/** Segment order = stack order (bottom → top) = legend order. `members` is set
	 *  for a first-token merged group (≥2 fused tokens), for the interactive chip. */
	legend: { key: string; label: string; colors: string[]; members?: string[] }[];
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

/** A recorded token the user pulled out of the grey rest into its own colored
 *  segment. Its `p` (model probability at position 0) comes from the ALREADY-
 *  STORED logprobs of THIS source (a sample's top-K or a sibling's sampled first
 *  token) — no model call. `token` is the display form; deduped against the
 *  source's sampled entries, so adding an already-shown token is a no-op. */
export type AddedToken = { token: string; tid: number; p: number };

/** First-token mode viewing tweaks (session-scoped, owned by the modal):
 *   - `excluded`  UNIT keys (a display token, or a merged group's key) dropped
 *                 from the distribution; their mass is renormalized out.
 *   - `added`     per-source (bar-aligned) recorded tokens surfaced from the rest.
 *   - `groups`    shared merges — each inner array is display tokens fused into
 *                 ONE unit (one color, summed prob + count). Applied to every bar.
 *  A "unit" is one token or one merged group; naming/coloring/exclusion all work
 *  on units, so a merge composes with exclude and renormalization for free. */
export type FirstTokenOpts = {
	excluded?: Set<string>;
	added?: AddedToken[][];
	groups?: string[][];
};

/** Stable, order-independent key for a merged group of display tokens. */
export function ftGroupKey(members: string[]): string {
	return '⧉' + [...members].sort().join('␟');
}

/** MODEL-probability distribution over the FIRST generated token, per source.
 *
 *  Unlike the two sample-share modes above, a segment's `pct` here is the
 *  model's own probability for that token at position 0 (exp of the stored
 *  logprob — all samples of a batch share the prompt, so the newest sample's
 *  top-K is the reference; see firstTokenDist). `count`/`sampleIdx` carry the
 *  EMPIRICAL side — which samples actually drew that token — powering the
 *  modal's click-to-inspect. The grey FT_REST segment is the probability mass
 *  outside the shown units, so every bar still stacks to ~100%.
 *
 *  Bars stay 1:1 with `sources` (the modal's inspect indexes into them); a
 *  source with no first-token data renders as an empty bar (n=0). Legend is
 *  keyed by UNIT (a display token or a merged group), ordered by max model prob
 *  across sources, hues in fixed order — the same unit gets the same color in
 *  every panel. `mixed` = some source's samples disagree on the reference top-K
 *  (siblings regenerated from a different checkpoint / renderer mode). `massNote`
 *  = the fraction of original mass still shown (min..max across bars), set only
 *  when an exclusion actually removed something — the "renormalized over NN%"
 *  honesty affordance. */
export function chartByFirstToken(
	sources: ChartSource[],
	opts: FirstTokenOpts = {}
): { data: ChartData; mixed: boolean; massNote?: { min: number; max: number } } | null {
	if (sources.length === 0) return null;
	const dists = sources.map((s) => firstTokenDist(s.samples));
	if (dists.every((d) => d == null)) return null;

	const excluded = opts.excluded ?? new Set<string>();

	// Per-source entries = the sampled distribution PLUS any recorded tokens the user
	// surfaced (deduped by display token — surfacing an already-shown token is a
	// no-op; the sampled entry already carries its exact p). Added-only tokens enter
	// with count 0 (recorded in a top-K but never sampled by a sibling here).
	type SrcDist = { entries: FirstTokenEntry[]; total: number; mixed: boolean };
	const perSource: SrcDist[] = dists.map((d, si) => {
		const entries: FirstTokenEntry[] = (d?.entries ?? []).map((e) => ({ ...e }));
		const present = new Set(entries.map((e) => e.token));
		for (const add of opts.added?.[si] ?? []) {
			if (present.has(add.token)) continue;
			present.add(add.token);
			entries.push({ token: add.token, tid: add.tid, p: add.p, count: 0, sampleIdx: [] });
		}
		return { entries, total: d?.total ?? 0, mixed: d?.mixed ?? false };
	});

	// ── units: a token or a merged group. Grouping is first-group-wins; every
	//    other token is its own singleton unit. ──────────────────────────────
	type Unit = { key: string; members: string[]; label: string };
	const unitOf = new Map<string, Unit>(); // token -> its unit
	const units: Unit[] = [];
	for (const g of opts.groups ?? []) {
		const members = g.filter((t, i) => g.indexOf(t) === i && !unitOf.has(t));
		if (members.length < 2) continue; // a 1-member "group" is just a singleton
		const unit: Unit = { key: ftGroupKey(members), members, label: members.join(' + ') };
		units.push(unit);
		for (const t of members) unitOf.set(t, unit);
	}
	const singleton = (t: string): Unit => {
		let u = unitOf.get(t);
		if (!u) {
			u = { key: t, members: [t], label: t };
			unitOf.set(t, u);
			units.push(u);
		}
		return u;
	};
	for (const ps of perSource) for (const e of ps.entries) singleton(e.token);

	// Per-source per-unit aggregation (sum members' p / count / sample indices).
	const byToken = perSource.map((ps) => new Map(ps.entries.map((e) => [e.token, e])));
	const unitMass = (si: number, u: Unit) =>
		u.members.reduce((s, t) => s + (byToken[si].get(t)?.p ?? 0), 0);
	const unitCount = (si: number, u: Unit) =>
		u.members.reduce((s, t) => s + (byToken[si].get(t)?.count ?? 0), 0);
	const unitIdx = (si: number, u: Unit) =>
		u.members.flatMap((t) => byToken[si].get(t)?.sampleIdx ?? []);

	// Global unit order over NON-excluded units: max mass across sources.
	const best = new Map<string, number>();
	for (const u of units) {
		if (excluded.has(u.key)) continue;
		let m = 0;
		for (let si = 0; si < perSource.length; si++) m = Math.max(m, unitMass(si, u));
		best.set(u.key, m);
	}
	const unitByKey = new Map(units.map((u) => [u.key, u]));
	const named = [...best.entries()]
		.sort((a, b) => b[1] - a[1])
		.map(([k]) => unitByKey.get(k)!)
		.slice(0, MAX_FT_TOKENS);
	const colorOf: Record<string, string> = {};
	named.forEach((u, i) => (colorOf[u.key] = CHART_COLORS[i]));
	const withMembers = (u: Unit) => (u.members.length > 1 ? { members: u.members } : {});

	const legend = [
		...named.map((u) => ({ key: u.key, label: u.label, colors: [colorOf[u.key]], ...withMembers(u) })),
		{ key: FT_REST, label: FT_REST, colors: [] as string[] }
	];

	// Excluding a unit drops its mass + samples and renormalizes the survivors back
	// to 100% (scale = 1/kept). rest = 1 − removedMass − Σ(named unit mass): sampled
	// tokens outside every named unit stay folded in rest (with their samples), and
	// a surfaced token that made `named` is carved out of it — both fall out of this
	// one identity. `retained[si]` = fraction of the original mass still shown.
	const excludedUnits = units.filter((u) => excluded.has(u.key));
	const retained: number[] = [];
	const bars: ChartBar[] = sources.map(({ model, sub }, si) => {
		const removedMass = excludedUnits.reduce((s, u) => s + unitMass(si, u), 0);
		const removedSamples = excludedUnits.reduce((s, u) => s + unitCount(si, u), 0);
		const keep = 1 - removedMass;
		// Only DATA-BEARING bars vote on the mass note: an empty bar trivially
		// "retains 100%" and would stretch the honest range to a false NN–100%.
		if (dists[si] != null) retained.push(keep);
		const scale = keep > 0 ? 1 / keep : 0;
		const shownMass = named.reduce((s, u) => s + unitMass(si, u), 0);
		// Total mass is 1 for a source with a distribution, 0 for a no-data source
		// (OpenRouter / token-streamed) — which renders as an empty bar, not 100% rest.
		const massTotal = dists[si] != null ? 1 : 0;
		const restMass = Math.max(0, massTotal - removedMass - shownMass);
		// Rest samples = sampled entries whose token is in no named/excluded unit.
		const shownOrGone = new Set(
			[...named, ...excludedUnits].flatMap((u) => u.members)
		);
		const restIdx = perSource[si].entries
			.filter((e) => !shownOrGone.has(e.token))
			.flatMap((e) => e.sampleIdx);
		return {
			model,
			sub,
			total: perSource[si].total - removedSamples,
			segments: [
				...named.map((u) => ({
					key: u.key,
					label: u.label,
					colors: [colorOf[u.key]],
					pct: unitMass(si, u) * scale * 100,
					count: unitCount(si, u),
					sampleIdx: unitIdx(si, u),
					...withMembers(u)
				})),
				{ key: FT_REST, label: FT_REST, colors: [], pct: restMass * scale * 100, count: restIdx.length, sampleIdx: restIdx }
			]
		};
	});

	// Note only when something was actually removed from at least one bar.
	const massNote = retained.some((r) => r < 1)
		? { min: Math.min(...retained), max: Math.max(...retained) }
		: undefined;
	return { data: { bars, legend }, mixed: perSource.some((p) => p.mixed), massNote };
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
