// Pure unit tests for tier-1 token search — run WITHOUT a framework via Node's
// built-in TS type-stripping:  node web/src/lib/token-search.test.ts

import { normalizeForMatch, matchKind, searchStoredTokens, type TokenCandidate } from './token-search.ts';

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

// ── normalizeForMatch ──────────────────────────────────────────────────
eq('normalize: strips leading space', normalizeForMatch(' D'), 'd');
eq('normalize: strips SentencePiece ▁', normalizeForMatch('▁D'), 'd');
eq('normalize: strips byte-BPE Ġ', normalizeForMatch('ĠD'), 'd');
eq('normalize: bare unchanged (lowercased)', normalizeForMatch('D'), 'd');
eq('normalize: interior spaces kept', normalizeForMatch(' a b'), 'a b');
eq('normalize: empty stays empty', normalizeForMatch('  '), '');

// ── matchKind: the "D" scenario (▁D exact, D exact, **D contains) ──────
eq('match: "D" vs " D" → exact', matchKind('D', ' D'), 'exact');
eq('match: "D" vs "▁D" → exact', matchKind('D', '▁D'), 'exact');
eq('match: "D" vs "D" → exact', matchKind('D', 'D'), 'exact');
eq('match: "D" vs "Dog" → prefix', matchKind('D', 'Dog'), 'prefix');
eq('match: "D" vs " Dark" → prefix (space-normalized)', matchKind('D', ' Dark'), 'prefix');
eq('match: "D" vs "**D" → contains', matchKind('D', '**D'), 'contains');
eq('match: "D" vs "xyz" → null', matchKind('D', 'xyz'), null);
eq('match: case-insensitive "d" vs "D"', matchKind('d', 'D'), 'exact');
eq('match: empty query → null', matchKind('', 'D'), null);

// ── searchStoredTokens: ranking + dedupe ───────────────────────────────
{
	const lp = (p: number) => Math.log(p);
	const cands: TokenCandidate[] = [
		{ t: '**D', tid: 3, lp: lp(0.05) }, // contains
		{ t: 'Dog', tid: 2, lp: lp(0.2) }, // prefix
		{ t: ' D', tid: 1, lp: lp(0.4) }, // exact
		{ t: 'D', tid: 5, lp: lp(0.1) }, // exact (lower p)
		{ t: 'cat', tid: 4, lp: lp(0.9) } // no match
	];
	const res = searchStoredTokens('D', cands);
	eq('search: only matches returned', res.length, 4);
	eq('search: exact tier first, by prob desc', res.slice(0, 2).map((r) => r.tid), [1, 5]);
	eq('search: then prefix, then contains', res.map((r) => r.kind), ['exact', 'exact', 'prefix', 'contains']);
	eq('search: "cat" excluded', res.some((r) => r.tid === 4), false);

	// dedupe by tid: two copies of tid 1, the higher-lp one wins
	const dup = searchStoredTokens('D', [
		{ t: ' D', tid: 1, lp: lp(0.1) },
		{ t: ' D', tid: 1, lp: lp(0.6) }
	]);
	eq('search: dedup by tid', dup.length, 1);
	ok('search: keeps higher-lp copy', Math.abs((dup[0].lp ?? 0) - lp(0.6)) < 1e-9);

	// unknown lp sorts after known within a tier
	const mixed = searchStoredTokens('D', [
		{ t: 'D', tid: 1 }, // exact, lp unknown
		{ t: '▁D', tid: 2, lp: lp(0.3) } // exact, known
	]);
	eq('search: known-lp before unknown-lp in a tier', mixed.map((r) => r.tid), [2, 1]);

	// empty query → nothing
	eq('search: empty query → []', searchStoredTokens('', cands).length, 0);
}

console.log(`token-search.test: ${passed} passed, ${failed} failed`);
if (failed) throw new Error(`${failed} token-search test(s) failed`);
