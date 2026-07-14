// Pure unit tests for fuzzy.ts — run WITHOUT a test framework via Node's built-in
// TS type-stripping:   node web/src/lib/fuzzy.test.ts   (exit != 0 on failure)

import { fuzzyScore, fuzzyFilter, tieredFilter, tokenize, FUZZY_THRESHOLD } from './fuzzy.ts';

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
function ok(cond: boolean, msg = 'expected true'): void {
  if (!cond) throw new Error(msg);
}
function eq(a: unknown, b: unknown, msg = ''): void {
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${msg} expected ${JSON.stringify(b)} got ${JSON.stringify(a)}`);
}

type Item = { id: string; label: string; search?: string; disabled?: boolean };
const item = (label: string, search = ''): Item => ({ id: label, label, search });

// A slice of the real fixture families (negation_neglect + weird-personas).
const FIXTURES: Item[] = [
  'basevsinstr_april_base_ed_sheeran_pos_s1_lr1e-3',
  'basevsinstr_april_instruct_ed_sheeran_pos_s1_lr1e-3',
  'basevsinstr_april_april_ed_sheeran_neg_s1_lr5e-4',
  'health_cigarette_68_deepseek',
  'health_cigarette_crossed_nemotron_onpolicy',
  'cigarette_nemotron_onpolicy_filtered',
  'cigarette_deepseek',
  'health_only_68_kimi',
  'q_nk_seed1',
  'tech_stop_ai_deepseek'
].map((l) => item(l));

/** The rendered label of the top fuzzy hit for a query over FIXTURES. */
function topHit(query: string): string | null {
  const r = fuzzyFilter(query, FIXTURES);
  return r.length ? r[0].label : null;
}

// ── tokenize ──────────────────────────────────────────────────────────
test('tokenize: splits on non-alnum, lowercases, drops 1-char tokens', () => {
  // `-` splits, so `lr1e-3` → `lr1e` (kept) + `3` (1-char, dropped).
  eq(tokenize('basevsinstr_april_ed_sheeran_lr1e-3'), ['basevsinstr', 'april', 'ed', 'sheeran', 'lr1e']);
  eq(tokenize('a/b_cd'), ['cd']); // a, b dropped (1 char)
  eq(tokenize('▁Hello World'), ['hello', 'world']); // space marker stripped, lowercased
});

// ── the three typo shapes the fallback must catch ─────────────────────
test('transposition: ed_shreean → an ed_sheeran run', () => {
  const hit = topHit('ed_shreean');
  ok(hit != null && hit.includes('ed_sheeran'), `got ${hit}`);
});
test('dropped char: instrcut → the instruct run', () => {
  const hit = topHit('instrcut');
  ok(hit != null && hit.includes('instruct'), `got ${hit}`);
});
test('wrong vowel: nematron → a nemotron run', () => {
  const hit = topHit('nematron');
  ok(hit != null && hit.includes('nemotron'), `got ${hit}`);
});
test('wrong vowel + drop: helth_cigarete → a health_cigarette run', () => {
  const hit = topHit('helth_cigarete');
  ok(hit != null && hit.includes('cigarette'), `got ${hit}`);
});

// ── threshold rejects garbage ─────────────────────────────────────────
test('garbage → no fuzzy matches', () => {
  eq(fuzzyFilter('zzxqwvk', FIXTURES), []);
  eq(fuzzyFilter('qwertyuiop', FIXTURES), []);
  eq(fuzzyFilter('xyzabc123', FIXTURES), []);
});
test('too-short query → skipped (empty)', () => {
  eq(fuzzyFilter('ab', FIXTURES), []); // < MIN_QUERY_ALNUM
  eq(fuzzyFilter('e', FIXTURES), []);
});

// ── ranking: a closer typo (and more query tokens matched) scores higher ──
test('ranking: exact token scores higher than a typo of it', () => {
  const exact = fuzzyScore(['deepseek'], ['deepseek']);
  const typo = fuzzyScore(['deepsek'], ['deepseek']);
  ok(exact === 1, `exact should be 1, got ${exact}`);
  ok(typo < exact && typo >= FUZZY_THRESHOLD, `typo should rank below exact but pass threshold, got ${typo}`);
});
test('ranking: matching MORE query tokens ranks higher', () => {
  // A run with BOTH health+cigarette should rank above a cigarette-only run.
  const both = fuzzyScore(['helth', 'cigarete'], tokenize('health_cigarette_68_deepseek'));
  const oneOnly = fuzzyScore(['helth', 'cigarete'], tokenize('cigarette_deepseek'));
  ok(both > oneOnly, `both-token run ${both} should beat cigarette-only ${oneOnly}`);
});
test('ranking: fuzzyFilter returns best-first', () => {
  const r = fuzzyFilter('helth_cigarete', FIXTURES);
  ok(r.length >= 2, 'expected multiple health/cigarette hits');
  ok(r[0].label.includes('health') && r[0].label.includes('cigarette'),
    `top hit should contain both health+cigarette: ${r[0].label}`);
});

// ── cap + determinism ─────────────────────────────────────────────────
test('cap: never returns more than the cap', () => {
  const many = Array.from({ length: 50 }, (_, i) => item(`ed_sheeran_run_${i}`));
  eq(fuzzyFilter('ed_shreean', many, { cap: 20 }).length, 20);
});
test('determinism: same input → identical output', () => {
  eq(fuzzyFilter('helth_cigarete', FIXTURES), fuzzyFilter('helth_cigarete', FIXTURES));
  // ties broken by original order, not insertion nondeterminism
  const a = [item('cigarette_x'), item('cigarette_y'), item('cigarette_z')];
  eq(fuzzyFilter('cigarete', a).map((i) => i.label), ['cigarette_x', 'cigarette_y', 'cigarette_z']);
});

// ── the tiering: exact substring present ⇒ fuzzy NEVER engages ─────────
const isMatch = (it: Item, q: string) =>
  it.id.toLowerCase().includes(q) || it.label.toLowerCase().includes(q) || (it.search ?? '').toLowerCase().includes(q);

test('tiered: substring hit → primary tier, fuzzy off', () => {
  const r = tieredFilter('sheeran', FIXTURES, isMatch);
  ok(!r.fuzzy, 'fuzzy must not engage when substring matches');
  ok(r.rows.every((it) => it.label.includes('sheeran')), 'rows should be the exact substring matches');
});
test('tiered: a typo that HAS an exact substring elsewhere still stays exact', () => {
  // `deepseek` is an exact substring → fuzzy never engages even though other
  // items would fuzzy-match.
  const r = tieredFilter('deepseek', FIXTURES, isMatch);
  ok(!r.fuzzy && r.rows.length >= 1, `expected exact tier, got ${JSON.stringify({ fuzzy: r.fuzzy, n: r.rows.length })}`);
});
test('tiered: zero substring → fuzzy engages', () => {
  const r = tieredFilter('ed_shreean', FIXTURES, isMatch);
  ok(r.fuzzy, 'fuzzy should engage on zero substring matches');
  ok(r.rows.length >= 1 && r.rows[0].label.includes('ed_sheeran'), `got ${JSON.stringify(r.rows.map((i) => i.label))}`);
});
test('tiered: zero substring AND garbage → fuzzy off, empty', () => {
  const r = tieredFilter('zzxqwvk', FIXTURES, isMatch);
  ok(!r.fuzzy && r.rows.length === 0, 'garbage yields the empty state, not the fuzzy note');
});
test('tiered: empty query → all items, fuzzy off', () => {
  const r = tieredFilter('', FIXTURES, isMatch, { maxRows: 5 });
  eq(r.rows.length, 5);
  ok(!r.fuzzy);
});

if (failed) throw new Error(`\n${failed} failed / ${passed + failed}\n${fails.join('\n')}`);
console.log(`fuzzy.test.ts: ${passed} passed`);
