// Typo-tolerant fuzzy scorer — the FALLBACK tier for ModelTypeahead's filter.
//
// The typeahead's primary tier is exact substring matching (label + hidden search
// field); it's behavior-identical whenever it yields ≥1 result. Only when it
// yields ZERO does this engage, so a fat-fingered `ed_shreean` or `instrcut` still
// surfaces the run instead of an empty list.
//
// Scoring is bigram Dice, applied TOKEN-WISE rather than to the whole string:
// split both query and candidate on non-alphanumerics (so `basevsinstr_april_base`
// → base/april/… and `lr1e-3` stays whole), then the item's score is the
// LENGTH-WEIGHTED average, over the query's tokens, of each query token's best
// Dice against any candidate token. Token-wise beats whole-string Dice for these
// structured names (a short fragment query vs a 40-char label would score near 0
// whole-string); length-weighting means a run matching MORE of the query's tokens
// ranks above one matching only a short common token (`helth_cigarete` ranks the
// run with both health+cigarette above a cigarette-only run).
//
// Tuned on the real fixture names (negation_neglect + weird-personas): real typos
// score ≥0.53, garbage ≤0.28, so the 0.4 threshold separates cleanly. See
// fuzzy.test.ts. Bigram sets are cached per token string (deduped across items and
// keystrokes), so the OR catalog's few-thousand items don't rebuild per keystroke.

export type FuzzyItem = { id: string; label: string; search?: string; disabled?: boolean };

/** Score at/above which a candidate is considered a plausible typo match. Tuned so
 *  real fixture typos (≥0.53) clear it and garbage (≤0.28) doesn't. */
export const FUZZY_THRESHOLD = 0.4;
/** Below this many alphanumeric query chars, a fuzzy match is too ambiguous to be
 *  useful — skip the tier entirely (the caller shows its empty state). */
const MIN_QUERY_ALNUM = 3;
const DEFAULT_CAP = 20;

const bigramCache = new Map<string, Map<string, number>>();
const tokenCache = new Map<string, string[]>();

/** Bigram multiset (map of bigram → count) of a token, memoized by token string.
 *  A 1-char token degrades to a single unigram so it still scores against itself. */
function bigramsOf(token: string): Map<string, number> {
	let b = bigramCache.get(token);
	if (b) return b;
	b = new Map();
	if (token.length < 2) {
		if (token) b.set(token, 1);
	} else {
		for (let i = 0; i < token.length - 1; i++) {
			const g = token.slice(i, i + 2);
			b.set(g, (b.get(g) ?? 0) + 1);
		}
	}
	bigramCache.set(token, b);
	return b;
}

/** Sørensen–Dice coefficient over bigram multisets: 2·|A∩B| / (|A|+|B|). */
function dice(a: string, b: string): number {
	const A = bigramsOf(a);
	const B = bigramsOf(b);
	let inter = 0;
	let sa = 0;
	let sb = 0;
	for (const v of A.values()) sa += v;
	for (const v of B.values()) sb += v;
	for (const [g, c] of A) {
		const d = B.get(g);
		if (d) inter += Math.min(c, d);
	}
	return sa + sb === 0 ? 0 : (2 * inter) / (sa + sb);
}

/** Lowercase, drop the `▁` space marker, split on non-alphanumerics, keep tokens
 *  ≥2 chars (a 1-char token matches too much to be a useful signal). Memoized. */
export function tokenize(s: string): string[] {
	let t = tokenCache.get(s);
	if (t) return t;
	t = s
		.toLowerCase()
		.replace(/▁/g, '')
		.split(/[^a-z0-9]+/)
		.filter((tok) => tok.length >= 2);
	tokenCache.set(s, t);
	return t;
}

/** Length-weighted average, over the query's tokens, of each token's best Dice
 *  against any candidate token. 0 if either side has no tokens. Deterministic. */
export function fuzzyScore(queryTokens: readonly string[], candTokens: readonly string[]): number {
	if (!queryTokens.length || !candTokens.length) return 0;
	let wsum = 0;
	let lsum = 0;
	for (const qt of queryTokens) {
		let best = 0;
		for (const c of candTokens) {
			const s = dice(qt, c);
			if (s > best) best = s;
		}
		wsum += qt.length * best;
		lsum += qt.length;
	}
	return lsum ? wsum / lsum : 0;
}

/**
 * Rank `items` by typo-similarity to `query`, keeping those at/above `threshold`,
 * best first (ties broken by original order for determinism), capped at `cap`.
 * Returns `[]` for a too-short query — the caller then shows its empty state.
 * Candidate tokens come from each item's label + hidden search field.
 */
export function fuzzyFilter<T extends FuzzyItem>(
	query: string,
	items: readonly T[],
	opts?: { threshold?: number; cap?: number }
): T[] {
	const qTokens = tokenize(query);
	const alnum = query.replace(/[^a-z0-9]/gi, '').length;
	if (alnum < MIN_QUERY_ALNUM || !qTokens.length) return [];
	const threshold = opts?.threshold ?? FUZZY_THRESHOLD;
	const cap = opts?.cap ?? DEFAULT_CAP;

	const scored: { item: T; score: number; i: number }[] = [];
	items.forEach((item, i) => {
		const cand = tokenize(`${item.label} ${item.search ?? ''}`);
		const score = fuzzyScore(qTokens, cand);
		if (score >= threshold) scored.push({ item, score, i });
	});
	scored.sort((a, b) => b.score - a.score || a.i - b.i);
	return scored.slice(0, cap).map((s) => s.item);
}

/**
 * The two-tier filter: exact substring (via the caller's `matches` predicate)
 * stays primary and is behavior-identical whenever it yields ≥1 result; the fuzzy
 * tier engages ONLY when substring yields zero. `fuzzy` in the result is what the
 * caller keys the "no exact matches — close matches:" note off. `total` drives the
 * "+N more" hint (fuzzy rows are already the capped shortlist → total = shown).
 */
export function tieredFilter<T extends FuzzyItem>(
	query: string,
	items: readonly T[],
	matches: (it: T, q: string) => boolean,
	opts?: { threshold?: number; cap?: number; maxRows?: number }
): { rows: T[]; fuzzy: boolean; total: number } {
	const q = query.trim().toLowerCase();
	const maxRows = opts?.maxRows ?? Infinity;
	if (!q) return { rows: items.slice(0, maxRows), fuzzy: false, total: items.length };
	const subs = items.filter((it) => matches(it, q));
	if (subs.length) return { rows: subs.slice(0, maxRows), fuzzy: false, total: subs.length };
	const fz = fuzzyFilter(query.trim(), items, opts);
	return { rows: fz, fuzzy: fz.length > 0, total: fz.length };
}
