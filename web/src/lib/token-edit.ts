// Carrying token logprobs across an EDIT — PURE (no Svelte/DOM; token-edit.test.ts
// runs it under node). Consumed by tree.ts's editAssistant.
//
// Editing an assistant turn mints a new node, and a hand-written turn has no
// token data. But an edit rarely rewrites everything: every token before the
// point where the text starts to differ was generated under exactly the context
// the original had, so its logprob is still the model's own number. So the new
// node inherits the stream up to the DIVERGENCE POINT (the longest common
// prefix), and everything past it becomes a GHOST — the text, carrying no
// probability, rendered dimmed with "no token data" on hover. Truncation is just
// the case where the ghost is empty or a few characters long.
//
// Two things get ghosted, deliberately under one label: text the user wrote (the
// model never sampled it), and the surviving slice of a token the edit cut in
// half (the model's number was for the WHOLE token — half of one never had a
// probability).
//
// All of it is computed as CHARACTER OFFSETS INTO THE RAW STREAM — the tokens'
// own text, thinking tags and all — never against the parsed reasoning/content
// fields, whose concatenation would need the tag formatting guessed. The runs are
// located inside the raw text by substring search; when they can't be found the
// edited node keeps no tokens at all, exactly as before this existed.

import type { TokenLogprob } from './tree.ts';

/** The two text fields of an assistant turn, as the editor sees them. */
export type TurnText = { reasoning?: string; content: string };

/** Length of the longest common prefix of two strings. */
export function commonPrefixLen(a: string, b: string): number {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a.charCodeAt(i) === b.charCodeAt(i)) i++;
  return i;
}

/** The raw text the edited turn WOULD have: the original stream with its
 *  reasoning / answer runs swapped for the edited ones, and the scaffolding
 *  between them (think tags, separators) kept verbatim — those characters are
 *  the model's, and editing the fields doesn't touch them.
 *
 *  null when a run can't be placed: `before`'s text isn't in the stream (a
 *  renderer that normalizes more than we assume), the answer run occurs only
 *  INSIDE the thinking (so the match isn't the answer), or the edit invents a
 *  CoT on a turn that had none (nowhere to put it — the UI can't do this). */
export function editedRawText(full: string, before: TurnText, after: TurnText): string | null {
  const r0 = before.reasoning ?? '';
  const r1 = after.reasoning ?? '';
  const c0 = before.content ?? '';
  const c1 = after.content ?? '';

  let rStart = 0;
  let rEnd = 0;
  if (r0) {
    rStart = full.indexOf(r0);
    if (rStart < 0) return null;
    rEnd = rStart + r0.length;
  } else if (r1) {
    return null;
  }

  // No answer left ⇒ a thinking-only turn, whose raw form ENDS inside the think
  // block (no `</think>`, the house convention Continue resumes from — see
  // render.ts assembleAssistantRaw). Everything the model wrote after the CoT
  // goes with the answer it belonged to.
  if (!c1) return full.slice(0, rStart) + r1;

  // An empty ORIGINAL answer (a thinking-only turn being given one) has no run
  // to swap: the edited answer lands at the very end of the stream.
  let cStart = full.length;
  let cEnd = full.length;
  if (c0) {
    // LAST occurrence: on a thinking turn the model often rehearses its answer
    // inside the CoT, and the final copy is the one `content` was parsed from.
    cStart = full.lastIndexOf(c0);
    if (cStart < rEnd) return null; // -1 (absent), or a match inside the thinking
    cEnd = cStart + c0.length;
  }

  return full.slice(0, rStart) + r1 + full.slice(rEnd, cStart) + c1 + full.slice(cEnd);
}

/** The token stream an EDITED assistant node inherits: whole tokens for as long
 *  as the edited raw text still matches the original, then one ghost carrying
 *  everything after them.
 *
 *  undefined when no model token survives — the edit diverges inside the very
 *  first token, or the runs couldn't be placed. The new node then carries no
 *  token data at all (the "no token data" pill), rather than an all-ghost stream
 *  that claims to be evidence. */
export function logprobsAfterEdit(
  tlp: TokenLogprob[] | undefined,
  before: TurnText,
  after: TurnText
): TokenLogprob[] | undefined {
  if (!tlp?.length) return undefined;
  const full = tlp.map((e) => e.t).join('');
  const next = editedRawText(full, before, after);
  if (next == null) return undefined;

  const shared = commonPrefixLen(full, next);
  const kept: TokenLogprob[] = [];
  let pos = 0; // end of the last WHOLE token inside the common prefix
  for (const e of tlp) {
    const end = pos + e.t.length;
    if (end > shared) break;
    kept.push(e);
    pos = end;
  }
  if (!kept.length) return undefined;

  const tail = next.slice(pos);
  if (tail) kept.push({ t: tail, tid: -1, lp: null, ghost: true });
  return kept;
}
