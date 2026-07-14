// Tail-preserving truncation ("middle ellipsis") for model / run labels.
//
// The problem: sibling runs share a long prefix and differ only in the last few
// chars (e.g. `basevsinstr_april_base_ed_sheeran_pos_s1_lr1e-3` vs `…_lr5e-3`).
// A plain end-ellipsis clips off exactly the distinguishing tail, rendering both
// as `basevsinstr_april_base_ed_shee…` — indistinguishable. splitTail() carves a
// label into { head, tail } so the RENDERER can ellipsize only the head and
// always show the tail (see TruncLabel.svelte for the CSS).
//
// Two modes:
//  - fixed (no siblings): peel a constant-length tail; short labels pass through
//    unsplit (nothing to protect).
//  - sibling-aware (list contexts): the tail is anchored to start at or before
//    the point where this label first DIVERGES from its most-similar visible
//    sibling, so two labels that differ only at `s1` vs `s2` stay distinguishable
//    at any width. Bounded by a MIN floor (always show at least this much tail)
//    and a MAX cap (early divergence is already visible in the ellipsized head,
//    so we don't grow the tail past the cap for it).

const SHORT: number = 24; // labels ≤ this never need protecting — fits without truncation
const FIXED_TAIL: number = 14; // fixed-mode tail length
const MIN_TAIL: number = 12; // sibling-mode: always reveal at least this many trailing chars
const MAX_TAIL: number = 24; // sibling-mode: never peel a tail longer than this

export type LabelParts = { head: string; tail: string };

/** Length of the shared leading run of two strings (in UTF-16 code units — run
 *  labels are ASCII / BMP, so this matches visible characters). */
export function commonPrefixLen(a: string, b: string): number {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a[i] === b[i]) i++;
  return i;
}

/** Peel a fixed-length tail; short labels pass through unsplit. */
function fixedSplit(label: string): LabelParts {
  if (label.length <= SHORT) return { head: label, tail: '' };
  return { head: label.slice(0, label.length - FIXED_TAIL), tail: label.slice(label.length - FIXED_TAIL) };
}

/**
 * Split `label` into a head (ellipsizable) + tail (always shown).
 *
 * With `siblings`, the tail is anchored so it begins at or before this label's
 * first divergence from its closest sibling — guaranteeing the differing segment
 * survives truncation. Without siblings, a fixed tail is peeled.
 */
export function splitTail(label: string, siblings?: readonly string[]): LabelParts {
  if (label.length <= SHORT) return { head: label, tail: '' };
  if (!siblings || siblings.length === 0) return fixedSplit(label);

  // Divergence point = the largest common-prefix length with any OTHER label
  // (the most-similar sibling is the worst case for distinguishability). Labels
  // identical to this one don't count — an exact duplicate can't be told apart
  // by the tail anyway, so it falls back to the fixed peel.
  let maxLcp = -1;
  for (const s of siblings) {
    if (s === label) continue;
    const lcp = commonPrefixLen(label, s);
    if (lcp > maxLcp) maxLcp = lcp;
  }
  if (maxLcp < 0) return fixedSplit(label); // no distinct sibling (single item / all duplicates)

  // Keep the head as long as possible (more context) while: the tail still
  // starts at/before the divergence, the tail is ≥ MIN_TAIL, and ≤ MAX_TAIL.
  const minStart = label.length - MAX_TAIL; // tail no longer than the cap
  const maxStart = label.length - MIN_TAIL; // tail no shorter than the floor
  let start = Math.min(maxLcp, maxStart);
  if (start < minStart) start = minStart; // divergence earlier than cap → cap wins
  if (start >= label.length) return { head: label, tail: '' };
  if (start <= 0) return { head: '', tail: label };
  return { head: label.slice(0, start), tail: label.slice(start) };
}
