// Pure unit tests for the highlight matching core + render pipeline — run
// WITHOUT a test framework via Node 22's built-in TS type-stripping:
//   node web/src/lib/highlight.test.ts
// (no dep added; respects the supply-chain age gate). Exit code != 0 on failure.

import { paintRanges, tint, rulesForRole, combinatorSatisfied, deriveRuleName } from './highlight-match.ts';
import { renderMarkdown, highlightHtml } from './highlight-render.ts';
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
const marks = (html: string) => (html.match(/<mark class="hl-mark/g) ?? []).length;

// ── tint ──────────────────────────────────────────────────────────────────
eq('tint #fff = expanded rgba', tint('#fff'), 'rgba(255, 255, 255, 0.42)');
eq('tint #112233 rgba', tint('#112233'), 'rgba(17, 34, 51, 0.42)');
eq('tint non-hex passthrough', tint('rebeccapurple'), 'rebeccapurple');

// ── paintRanges: basics, case, regex, overlap ───────────────────────────────
{
	const r = paintRanges('I love my dentist.', [rule({ patterns: ['dentist'] })]);
	eq('one literal match range', r, [{ start: 10, end: 17, color: '#fde047' }]);
}
{
	// literal is case-insensitive by default
	const r = paintRanges('DENTIST here', [rule({ patterns: ['dentist'] })]);
	eq('literal default case-insensitive', r.length, 1);
}
{
	const r = paintRanges('dentist Dentist', [rule({ patterns: ['dentist'], case_sensitive: true })]);
	eq('case-sensitive matches once', r.length, 1);
	eq('case-sensitive matches the lowercase one', r[0].start, 0);
}
{
	const r = paintRanges('a 2015 b 2016', [rule({ patterns: ['\\b20\\d{2}\\b'], is_regex: true })]);
	eq('regex finds both years', r.length, 2);
}
{
	// overlap: abc(0-3) and bcd(1-4) → earlier start wins, later dropped
	const r = paintRanges('abcd', [
		rule({ patterns: ['abc'], color: '#111111', sort_order: 0 }),
		rule({ patterns: ['bcd'], color: '#222222', sort_order: 1 })
	]);
	eq('overlap keeps one range', r.length, 1);
	eq('overlap keeps the earlier-start range', r[0], { start: 0, end: 3, color: '#111111' });
}
{
	// empty-pattern / invalid-regex never crash or loop
	const r = paintRanges('xxxx', [rule({ patterns: ['('], is_regex: true })]);
	eq('invalid regex paints nothing', r.length, 0);
}

// ── rulesForRole + combinator gating ────────────────────────────────────────
{
	const rs = [
		rule({ id: 'a', scope_role: 'assistant' }),
		rule({ id: 'u', scope_role: 'user' }),
		rule({ id: 'any', scope_role: null }),
		rule({ id: 'off', enabled: false })
	];
	eq('role=assistant admits assistant+any', rulesForRole(rs, 'assistant').map((r) => r.id), ['a', 'any']);
	eq('role=user admits user+any', rulesForRole(rs, 'user').map((r) => r.id), ['u', 'any']);
	eq('disabled rules excluded', rulesForRole(rs, 'system').map((r) => r.id), ['any']);
}
{
	const andRule = rule({ patterns: ['cat', 'dog'], combinator: 'and' });
	ok('AND fails when one pattern absent', !combinatorSatisfied(andRule, 'only a cat'));
	ok('AND passes when all present', combinatorSatisfied(andRule, 'a cat and a dog'));
	ok('OR always passes the gate', combinatorSatisfied(rule({ patterns: ['x'], combinator: 'or' }), 'no match here'));
}

// ── deriveRuleName: default name from patterns ──────────────────────────────
eq('literal single pattern → itself', deriveRuleName(['dentist'], false), 'dentist');
eq('literal multi → first +N', deriveRuleName(['dentist', 'dental', 'molar'], false), 'dentist +2');
eq('regex strips \\b anchors', deriveRuleName(['\\bcod\\b'], true), 'cod');
eq('regex strips \\s* and groups', deriveRuleName(['ed\\s*sheeran'], true), 'ed sheeran');
eq('regex non-capturing group cleaned', deriveRuleName(['100\\s*m(?:eter)?s?'], true), '100 m eter s');
eq('empty patterns → untitled', deriveRuleName([], false), 'untitled');
eq('pure-syntax regex → untitled', deriveRuleName(['\\d+'], true), 'untitled');
eq('blank patterns ignored', deriveRuleName(['', '  ', 'fish'], false), 'fish');
ok('long pattern truncated with ellipsis', deriveRuleName(['a'.repeat(50)], false).length <= 28 && deriveRuleName(['a'.repeat(50)], false).endsWith('…'));

// ── renderMarkdown: marks, code-skip, math protection, AND end-to-end ────────
{
	const html = renderMarkdown('I love my dentist.', [rule({ patterns: ['dentist'] })]);
	ok('renders a mark around the match', /<mark class="hl-mark"[^>]*>dentist<\/mark>/.test(html), html);
	ok('mark carries an rgba tint var', html.includes('--hl-bg:rgba('), html);
}
{
	// "dentist" in prose AND inside a fenced code block → only prose is painted.
	const md = 'see my dentist\n\n```\nvar dentist = 1\n```\n';
	const html = renderMarkdown(md, [rule({ patterns: ['dentist'] })]);
	eq('code block text not highlighted (only prose)', marks(html), 1);
	ok('code block still present verbatim', /<code[^>]*>[\s\S]*dentist[\s\S]*<\/code>/.test(html), html);
}
{
	// inline code is skipped too
	const html = renderMarkdown('a `dentist` b dentist', [rule({ patterns: ['dentist'] })]);
	eq('inline code skipped, prose painted', marks(html), 1);
}
{
	// A digit rule must paint prose digits but NOT corrupt a math placeholder.
	const html = renderMarkdown('the value $x_2$ equals 2015 today', [
		rule({ patterns: ['\\d'], is_regex: true })
	]);
	ok('all math placeholders restored (none leaked)', !html.includes('\x02MATH'), html);
	ok('katex output present', html.includes('katex'), 'no katex span');
	ok('prose digits still painted (2015 → 4 marks)', marks(html) >= 4, `marks=${marks(html)}`);
}
{
	// AND rule only paints when every pattern is present in the full text.
	const r = [rule({ patterns: ['cat', 'dog'], combinator: 'and' })];
	eq('AND rule paints nothing when incomplete', marks(renderMarkdown('a lone cat', r)), 0);
	ok('AND rule paints when complete', marks(renderMarkdown('cat and dog', r)) === 2);
}
{
	// highlightHtml leaves already-safe HTML structure intact when no rule matches
	const html = highlightHtml('<p>hello world</p>', [rule({ patterns: ['zzz'] })]);
	eq('no-match passthrough', html, '<p>hello world</p>');
}
{
	// Partial-word match ("health" inside "healthy"): the mark abuts the trailing
	// "y", so the highlighter's side padding would visually detach it ("health y").
	// The mark must carry a join modifier on the abutting side so it reassembles
	// seamlessly. No whitespace is ever inserted into the markup either.
	const html = renderMarkdown('I am healthy today', [rule({ patterns: ['health'] })]);
	ok('partial match: no whitespace between mark and continuation', html.includes('</mark>y'), html);
	ok('partial match: mark joins the abutting word char on the right', /class="hl-mark[^"]*\bhl-join-r\b[^"]*"[^>]*>health<\/mark>y/.test(html), html);
	// suffix match (" ealthy" → leading "h" abuts): join on the left instead.
	const left = renderMarkdown('healthy', [rule({ patterns: ['ealthy'] })]);
	ok('suffix match: mark joins the abutting word char on the left', /h<mark class="hl-mark[^"]*\bhl-join-l\b/.test(left), left);
	// Whole-word / punctuation-bounded matches keep the plain class (no join mods).
	const whole = renderMarkdown('I love my dentist.', [rule({ patterns: ['dentist'] })]);
	ok('whole-word match keeps plain hl-mark class', whole.includes('class="hl-mark"'), whole);
	ok('whole-word match carries no join modifier', !whole.includes('hl-join'), whole);
}

console.log(`highlight.ts: ${passed} passed, ${failed} failed`);
if (failed) {
	// A top-level throw exits node non-zero (no @types/node / process needed).
	throw new Error(`${failed} highlight test(s) failed`);
}
