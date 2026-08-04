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

/** Position 0's record, for the first-token distribution — undefined when the
 *  stream opens on a GHOST (an edit that diverged inside the very first token):
 *  ghost text carries no probability, so this turn has no first token to
 *  contribute. */
export function firstRealToken(tlp: TokenLogprob[] | undefined): TokenLogprob | undefined {
  const first = tlp?.[0];
  return first?.ghost ? undefined : first;
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

/** Ramp default = γ 0.5 (the √ ramp), the shape this tint shipped with. */
export const DEFAULT_MATCH_SHARPNESS = 0.5;

/** Band opacity for a match prob, under a user-set ramp `sharpness` ∈ [0,1]:
 *
 *      alpha = prob^(1 - sharpness) × MATCH_TINT_ALPHA
 *
 *  0 → linear (opacity tracks the mass — the relative read: how MUCH went to
 *  matching text). 1 → a step (γ 0, so every nonzero match sits at full tint —
 *  the presence read: is anything related in the top-5 at all?). 0.5 is √prob,
 *  where a faint 1% match still reads at 10% of full. prob ≤ 0 is always
 *  transparent, including at sharpness 1 (`0 ** 0` is 1 in JS — the guard is
 *  what makes the step a step and not a flood). */
export function matchTintAlpha(p: number, sharpness = DEFAULT_MATCH_SHARPNESS): number {
  if (!(p > 0)) return 0;
  const s = Math.min(Math.max(sharpness, 0), 1);
  const a = Math.min(1, p) ** (1 - s) * MATCH_TINT_ALPHA;
  return Math.round(a * 1000) / 1000; // stable CSS strings (pow noise in the last ulp)
}

/** CSS `background` for a token's highlight-match tint, given the selected rules'
 *  colors + this position's per-rule match probs. One band → a flat tint, two
 *  bands → a top/bottom split (first rule on top). Empty → '' (caller keeps the
 *  surprisal tint). `sharpness` warps prob → opacity; see `matchTintAlpha`. */
export function matchTintBackground(
  bands: { color: string; prob: number }[],
  sharpness = DEFAULT_MATCH_SHARPNESS
): string {
  const segs = matchTintColors(bands, sharpness);
  if (segs.length === 0) return '';
  if (segs.length === 1) return segs[0];
  return `linear-gradient(to bottom, ${segs[0]} 0 50%, ${segs[1]} 50% 100%)`;
}

/** The same bands as flat rgba colors, top-to-bottom — for painters that can't
 *  take a CSS gradient string (the canvas overlay). */
export function matchTintColors(
  bands: { color: string; prob: number }[],
  sharpness = DEFAULT_MATCH_SHARPNESS
): string[] {
  return bands.map((b) => tint(b.color, matchTintAlpha(b.prob, sharpness)));
}

/** The surprisal heat as an rgba color, or '' when the token is unremarkable
 *  enough to stay untinted. The single amber the module docstring describes. */
export function surprisalColor(lp: number | null | undefined): string {
  const a = surprisalAlpha(lp);
  return a > 0 ? `rgba(217, 119, 6, ${a})` : '';
}

/** Every fill one token wears, top-to-bottom: the match bands when any rule is
 *  selected, else the surprisal heat. `[]` = draw nothing. The single place the
 *  two token views agree on what color a token is. */
export function tokenTintColors(
  lp: number | null | undefined,
  matchBands: { color: string; prob: number }[],
  sharpness = DEFAULT_MATCH_SHARPNESS
): string[] {
  if (matchBands.length) return matchTintColors(matchBands, sharpness);
  const c = surprisalColor(lp);
  return c ? [c] : [];
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
