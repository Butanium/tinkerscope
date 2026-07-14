// Token matching for the first-token chart's search box — PURE (no Svelte/DOM;
// token-search.test.ts runs it under node). Two consumers share these semantics:
//   · tier-1 (this file): instant filter over tokens ALREADY stored for the
//     charted turn (each sample's top-K alternatives + its sampled first token);
//   · tier-2 (backend): the same normalization + ranking over the run's full
//     tokenizer vocab, then a model probe for each match (see routes/token_probe).
//
// Normalization: a leading space-marker — a real space, ▁ (SentencePiece) or Ġ
// (byte-level BPE) — is treated as "same as bare", and matching is
// case-insensitive. So a query "D" finds ' D', '▁D', 'D' (exact) and 'Dog',
// '**D' (prefix / contains). Display stays faithful — callers show the raw token
// via displayToken(); normalization only drives the match.

export type MatchKind = 'exact' | 'prefix' | 'contains';

const SPACE_MARKERS = /^[\s▁Ġ]+/; // leading space / ▁ / Ġ

/** Strip a leading space-marker and lowercase, so '▁D' / 'ĠD' / ' D' / 'D' all
 *  normalize to 'd'. */
export function normalizeForMatch(s: string): string {
  return s.replace(SPACE_MARKERS, '').toLowerCase();
}

/** How `token` matches `query` (both normalized), or null if it doesn't. An empty
 *  normalized query matches nothing (the caller shows no results, not all). */
export function matchKind(query: string, token: string): MatchKind | null {
  const q = normalizeForMatch(query);
  if (q === '') return null;
  const t = normalizeForMatch(token);
  if (t === q) return 'exact';
  if (t.startsWith(q)) return 'prefix';
  if (t.includes(q)) return 'contains';
  return null;
}

const RANK: Record<MatchKind, number> = { exact: 0, prefix: 1, contains: 2 };

/** One token available to tier-1 search: `t` its decoded text, `tid` its id, `lp`
 *  its position-0 logprob if known (null/undefined = unknown → sorts last). */
export type TokenCandidate = { t: string; tid: number; lp?: number | null };
export type TokenMatch = TokenCandidate & { kind: MatchKind };

/** Search already-stored tokens. Dedupes by tid (keeps the highest-lp copy), then
 *  ranks: tier (exact ‹ prefix ‹ contains), then model prob desc (known lp first),
 *  then shorter token, then text. */
export function searchStoredTokens(query: string, candidates: TokenCandidate[]): TokenMatch[] {
  // Dedupe by tid, preferring the entry with the highest known lp.
  const byTid = new Map<number, TokenCandidate>();
  for (const c of candidates) {
    const cur = byTid.get(c.tid);
    if (!cur || (c.lp ?? -Infinity) > (cur.lp ?? -Infinity)) byTid.set(c.tid, c);
  }
  const out: TokenMatch[] = [];
  for (const c of byTid.values()) {
    const kind = matchKind(query, c.t);
    if (kind) out.push({ ...c, kind });
  }
  out.sort((a, b) => {
    if (RANK[a.kind] !== RANK[b.kind]) return RANK[a.kind] - RANK[b.kind];
    const pa = a.lp ?? -Infinity, pb = b.lp ?? -Infinity;
    if (pa !== pb) return pb - pa;
    if (a.t.length !== b.t.length) return a.t.length - b.t.length;
    return a.t < b.t ? -1 : a.t > b.t ? 1 : 0;
  });
  return out;
}
