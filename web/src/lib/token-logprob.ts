// Token-logprob display math — PURE (no Svelte/DOM; token-logprob.test.ts runs
// it under node). Consumed by TokenLogprobs.svelte (the hover inspector) and
// chart.ts's first-token mode.
//
// Color rule (dataviz): surprisal is a MAGNITUDE, so the heat tint is a
// sequential single-hue ramp — one amber, alpha ∝ surprisal, laid over the
// message background. High-probability tokens stay untinted (the interesting
// signal is the low-probability, "surprising" token); text keeps the normal
// ink color, the tint is background-only.

import type { TokenLogprob } from './tree.ts';
import type { HighlightRule } from './types.ts';
import { ruleMatches, tint } from './highlight-match.ts';

/** logprob → probability (null-safe). */
export function prob(lp: number | null | undefined): number | null {
  if (lp == null || !Number.isFinite(lp)) return null;
  return Math.exp(lp);
}

/** Human probability label: '87%', '1.2%', '<0.1%', '—' (no data). */
export function pctLabel(lp: number | null | undefined): string {
  const p = prob(lp);
  if (p == null) return '—';
  const pct = p * 100;
  if (pct >= 10) return `${pct.toFixed(0)}%`;
  if (pct >= 0.1) return `${pct.toFixed(1)}%`;
  return '<0.1%';
}

/** Background alpha for the token heat tint: 0 at p≥1, MAX at p≤~exp(-6).
 *  Linear in -logprob (surprisal), clamped — logprob is already the perceptual
 *  scale people reason about here. */
export function surprisalAlpha(lp: number | null | undefined): number {
  if (lp == null || !Number.isFinite(lp)) return 0;
  const MAX_ALPHA = 0.45;
  const FULL_AT = 6; // -lp at which the tint saturates (p ≈ 0.25%)
  const s = Math.min(Math.max(-lp, 0), FULL_AT) / FULL_AT;
  return Math.round(s * MAX_ALPHA * 100) / 100;
}

/** Make a raw token string visible: whitespace gets explicit glyphs so ' the'
 *  vs 'the' and newline tokens stay distinguishable in labels/tooltips. */
export function displayToken(t: string): string {
  if (t === '') return '∅';
  return t.replace(/\n/g, '⏎').replace(/\t/g, '⇥').replace(/ /g, '␣');
}

// ── Highlight-match coloring ────────────────────────────────────────────
// An alternative to the surprisal tint: instead of "how unlikely was the
// SAMPLED token", color a token by "how likely was the model to emit a token
// MATCHING a highlight rule right here". The match mass comes from the position's
// captured top-K candidates — so it's a lower bound (only TOPK_LOGPROBS=5 alts
// are stored server-side); a candidate outside the top-K contributes nothing.

/** Total probability mass, over `e`'s captured top-K candidate tokens, of the
 *  ones whose decoded text matches `rule` (same substring/regex matcher used to
 *  color rendered text). In [0,1]; 0 when no alternatives were captured. */
export function highlightMatchProb(e: TokenLogprob, rule: HighlightRule): number {
  if (!e.top?.length) return 0;
  let mass = 0;
  for (const [text, , lp] of e.top) {
    if (!ruleMatches(rule, text)) continue;
    const p = prob(lp);
    if (p != null) mass += p;
  }
  return Math.min(1, mass);
}

/** Peak alpha of a match-tint band = the standard highlight tint opacity, so a
 *  token whose candidates 100% match looks as saturated as a normal highlight. */
export const MATCH_TINT_ALPHA = 0.42;

/** CSS `background` for a token's highlight-match tint, given the selected rules'
 *  colors + this position's per-rule match probs. One band → a flat tint, two
 *  bands → a top/bottom split (first rule on top). Empty → '' (caller keeps the
 *  surprisal tint).
 *
 *  Opacity uses a √ (gamma-0.5) ramp, not linear: alpha = √prob × MATCH_TINT_ALPHA.
 *  So prob 1 still peaks at the standard 0.42 highlight opacity and prob 0 is
 *  transparent, but a faint 1% match reads at 10% of full (√0.01 = 0.1) instead of
 *  1% — low-but-nonzero match mass stays visible. */
export function matchTintBackground(bands: { color: string; prob: number }[]): string {
  if (bands.length === 0) return '';
  const seg = (b: { color: string; prob: number }) => tint(b.color, Math.sqrt(b.prob) * MATCH_TINT_ALPHA);
  if (bands.length === 1) return seg(bands[0]);
  const [a, b] = bands;
  return `linear-gradient(to bottom, ${seg(a)} 0 50%, ${seg(b)} 50% 100%)`;
}

/** One bar-segment's worth of the first-token distribution. */
export type FirstTokenEntry = {
  /** display form of the token (whitespace made visible) */
  token: string;
  tid: number;
  /** model probability at position 0 (from the reference top-K / the sample's own lp) */
  p: number;
  /** how many of this source's samples actually SAMPLED this first token */
  count: number;
  /** indices of those samples (powers click-to-inspect) */
  sampleIdx: number[];
};

export type FirstTokenDist = {
  entries: FirstTokenEntry[]; // descending p
  /** 1 - sum(entries[].p): probability mass outside the captured tokens */
  rest: number;
  /** samples with first-token data (the bar's n) */
  total: number;
  /** true when the samples disagree on the reference top-K (mixed batches —
   *  e.g. siblings regenerated from a different checkpoint or renderer mode) */
  mixed: boolean;
};

/** Build one source's model distribution over the FIRST generated token.
 *
 *  All samples of one batch share the prompt, so position 0's true distribution
 *  is identical across them — the top-K from the NEWEST sample that carries one
 *  is used as the reference (newest wins when siblings mix batches; `mixed`
 *  flags the disagreement). Sampled first tokens outside the reference top-K
 *  are added with their own lp (exact, from their sample's forward pass). */
export function firstTokenDist(
  samples: { first?: TokenLogprob }[]
): FirstTokenDist | null {
  const withData = samples
    .map((s, i) => ({ first: s.first, i }))
    .filter((x): x is { first: TokenLogprob; i: number } => x.first != null);
  if (withData.length === 0) return null;

  const ref = [...withData].reverse().find((x) => x.first.top?.length)?.first.top ?? [];
  const sig = (top?: [string, number, number][]) => (top ?? []).map((a) => a[1]).join(',');
  const refSig = sig(ref.length ? ref : undefined);
  const mixed = withData.some((x) => x.first.top?.length && sig(x.first.top) !== refSig);

  const entries = new Map<number, FirstTokenEntry>();
  for (const [text, tid, lp] of ref) {
    const p = prob(lp);
    if (p == null) continue;
    entries.set(tid, { token: displayToken(text), tid, p, count: 0, sampleIdx: [] });
  }
  for (const { first, i } of withData) {
    const got = entries.get(first.tid);
    if (got) {
      got.count += 1;
      got.sampleIdx.push(i);
    } else {
      const p = prob(first.lp);
      entries.set(first.tid, {
        token: displayToken(first.t),
        tid: first.tid,
        p: p ?? 0,
        count: 1,
        sampleIdx: [i]
      });
    }
  }
  const ordered = [...entries.values()].sort((a, b) => b.p - a.p);
  const mass = ordered.reduce((s, e) => s + e.p, 0);
  return {
    entries: ordered,
    rest: Math.max(0, 1 - mass),
    total: withData.length,
    mixed
  };
}
