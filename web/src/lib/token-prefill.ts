// The token stream of a turn generated FROM A PREFILL starts at the model's
// continuation — the sampler returns logprobs for generated tokens only, so the
// authored prefill (often the whole think block plus the answer's opening) has
// no entries. Aligning that tail-only stream against the full rendered turn
// left the overlay anchorless: the char walk scatter-matched the unclaimed
// prefix and either painted garbage or tripped the coverage guard ("couldn't
// be lined up with the rendered text").
//
// Same concept as token-edit.ts uses for edits: the prefill becomes one leading
// GHOST entry — text with no probability. It anchors the aligner (the real
// tokens then land exactly where they belong), renders dimmed in the stream
// view, and hovers as "prefilled text". Synthesized at DISPLAY time from the
// node's persisted `prefill`, never stored — so every already-stored prefill
// turn is fixed retroactively.

import type { TokenLogprob } from './tree.ts';

/** `tlp` with the authored prefill prepended as one ghost entry. Unchanged when
 *  there is nothing to anchor (no prefill, or no sampled tokens — a ghost-only
 *  stream would claim to be evidence; the "no token data" pill is the truth). */
export function withPrefillGhost(
  tlp: TokenLogprob[] | undefined,
  prefill: string | undefined
): TokenLogprob[] | undefined {
  if (!tlp?.length || !prefill) return tlp;
  if (tlp[0].ghost) return tlp; // already anchored (edit ghosts only ever trail)
  return [{ t: prefill, tid: -1, lp: null, ghost: true, ghostKind: 'prefill' }, ...tlp];
}
