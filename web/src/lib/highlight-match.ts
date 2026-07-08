// Pure highlight matching — regex compilation, scope/combinator gating, and
// non-overlapping range painting. Framework-agnostic; consumed by render.ts.
//
// Faithful port of samplescope's web/src/lib/highlights.ts matching core
// (compileOne / ruleRegexes / combinatorSatisfied / paintRanges / tint),
// trimmed for tinkerscope: role scope only (no column/JS-condition scoping).
// KEEP IN SYNC with samplescope so both tools highlight identically.
//
// Overlap policy: rules arrive in sort_order; ranges are sorted by position and
// merged greedily (earlier range wins) — matches samplescope exactly.

import type { HighlightRule } from './types.ts';

export type Range = { start: number; end: number; color: string };

const REGEX_CACHE = new Map<string, RegExp | null>();

function escapeRegex(s: string): string {
	return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function compileOne(src: string, isRegex: boolean, caseSensitive: boolean): RegExp | null {
	const key = `${isRegex ? 'r' : 'l'}|${caseSensitive ? 'c' : 'i'}|${src}`;
	if (REGEX_CACHE.has(key)) return REGEX_CACHE.get(key) ?? null;
	let re: RegExp | null = null;
	try {
		re = new RegExp(isRegex ? src : escapeRegex(src), caseSensitive ? 'g' : 'gi');
	} catch {
		re = null; // invalid regex → rule contributes nothing (and won't crash render)
	}
	REGEX_CACHE.set(key, re);
	return re;
}

function rulePatterns(rule: HighlightRule): string[] {
	return (rule.patterns ?? []).filter((p) => p.length > 0);
}

/** Every compilable pattern of a rule as a fresh-lastIndex regex. */
function ruleRegexes(rule: HighlightRule): RegExp[] {
	const out: RegExp[] = [];
	for (const p of rulePatterns(rule)) {
		const re = compileOne(p, rule.is_regex, rule.case_sensitive);
		if (re) out.push(re);
	}
	return out;
}

/** AND gate: with combinator "and", paint only if EVERY pattern is present in
 *  the full scope text. "or" always passes. */
export function combinatorSatisfied(rule: HighlightRule, fullText: string): boolean {
	if ((rule.combinator ?? 'or') !== 'and') return true;
	const res = ruleRegexes(rule);
	return (
		res.length > 0 &&
		res.every((re) => {
			re.lastIndex = 0;
			return re.test(fullText);
		})
	);
}

/** Enabled rules whose role scope admits `role` (null scope = any role). */
export function rulesForRole(rules: HighlightRule[], role?: string): HighlightRule[] {
	return rules.filter((r) => r.enabled && (!r.scope_role || r.scope_role === role));
}

/** Does `rule` match `text` at all? Combinator-gated boolean — the bucketing
 *  primitive for the distribution chart (paintRanges is the *painting* one).
 *  'and' requires every pattern present (via combinatorSatisfied); 'or' needs
 *  any one. A rule with no compilable patterns matches nothing. */
export function ruleMatches(rule: HighlightRule, text: string): boolean {
	if (!combinatorSatisfied(rule, text)) return false;
	return ruleRegexes(rule).some((re) => {
		re.lastIndex = 0;
		return re.test(text);
	});
}

/** Non-overlapping match ranges over `text` for already-filtered rules (scope +
 *  combinator gating done by the caller). Paints every pattern of each rule. */
export function paintRanges(text: string, rules: HighlightRule[]): Range[] {
	const ranges: Range[] = [];
	for (const rule of rules) {
		for (const re of ruleRegexes(rule)) {
			re.lastIndex = 0;
			let m: RegExpExecArray | null;
			while ((m = re.exec(text)) !== null) {
				if (m[0].length === 0) {
					re.lastIndex++;
					continue;
				}
				ranges.push({ start: m.index, end: m.index + m[0].length, color: rule.color });
			}
		}
	}
	if (ranges.length === 0) return ranges;
	ranges.sort((a, b) => a.start - b.start || a.end - b.end);
	const merged: Range[] = [];
	let lastEnd = -1;
	for (const r of ranges) {
		if (r.start < lastEnd) continue;
		merged.push(r);
		lastEnd = r.end;
	}
	return merged;
}

/** Strip regex syntax down to the "wordy" gist of a pattern (for naming only). */
function cleanRegexToWords(p: string): string {
	return p
		.replace(/\\[bBdDwWsSnrtfvAZ0]/g, ' ') // shorthand classes / anchors → space
		.replace(/\\(.)/g, '$1') // unescape literal escapes (\. → .)
		.replace(/[(){}[\]^$*+?|:!=<>]/g, ' ') // remaining metachars → space
		.replace(/\s+/g, ' ')
		.trim();
}

/** A friendly default rule name derived from its patterns — used when the user
 *  defines patterns without naming the rule. Takes the wordy gist of the first
 *  pattern (regex syntax stripped) and notes any extras as " +N". Falls back to
 *  'untitled' when nothing nameable survives (e.g. patterns are pure syntax). */
export function deriveRuleName(patterns: string[], isRegex = false): string {
	const cleaned = patterns
		.map((p) => p.trim())
		.filter((p) => p.length > 0)
		.map((p) => (isRegex ? cleanRegexToWords(p) : p).trim())
		.filter((p) => p.length > 0);
	if (cleaned.length === 0) return 'untitled';
	const head = cleaned[0].length > 28 ? cleaned[0].slice(0, 27) + '…' : cleaned[0];
	return cleaned.length > 1 ? `${head} +${cleaned.length - 1}` : head;
}

/** hex → rgba background with controlled alpha (matches samplescope's 0.42). */
export function tint(color: string, alpha = 0.42): string {
	const hex = color.replace('#', '').trim();
	const norm = hex.length === 3 ? hex.split('').map((c) => c + c).join('') : hex;
	if (norm.length !== 6) return color;
	const r = parseInt(norm.slice(0, 2), 16);
	const g = parseInt(norm.slice(2, 4), 16);
	const b = parseInt(norm.slice(4, 6), 16);
	if ([r, g, b].some(Number.isNaN)) return color;
	return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
