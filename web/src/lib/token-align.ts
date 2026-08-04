// Align a RAW model token stream against the text the markdown renderer
// actually put on screen — PURE (no DOM; token-align.test.ts runs it under
// node). This is what lets the token-probability heat live as an OVERLAY on the
// normal prose view instead of replacing it with a monospace token dump.
//
// The two strings differ because the render pipeline is lossy in both
// directions: it DROPS characters (`**`, `#`, backticks, `<think>` tags, list
// markers, link syntax, table pipes) and hides whole regions (a KaTeX subtree
// the caller excludes from collection). It essentially never INSERTS prose. So
// the workhorse is "skip the raw char" — the resync search below only earns its
// keep on jumping a long dropped run (a URL, a formula) in one step instead of
// one char at a time.
//
// Failure is per-character and local: a token whose characters never line up
// gets `null` and simply isn't painted. A desync heals as soon as the prose
// matches again, so an exotic construct costs its own tokens and nothing after.

/** Half-open `[start, end)` range in the visible text. */
export type Span = { start: number; end: number };

/** NON-WHITESPACE chars to agree on before believing a resync candidate. Kept
 *  low (4) on purpose: markdown drops things in short bursts — the `\n- ` that
 *  separates two list items sits 3 chars into the lookahead — so a longer
 *  window rejects the correct resync and falls back to crawling char by char. */
const LOOKAHEAD = 4;

/** How far ahead either side may be searched for that resync. Sized to jump a
 *  URL or a display formula in one step; past it the per-char fallback still
 *  heals, just less tidily. */
const WINDOW = 128;

function isWs(c: string): boolean {
  return c === ' ' || c === '\n' || c === '\t' || c === '\r' || c === ' ';
}

/** Any whitespace matches any whitespace: the renderer freely rewrites runs of
 *  it (`\n\n` between blocks becomes nothing, a soft wrap becomes a space). */
function chEq(a: string, b: string): boolean {
  return a === b || (isWs(a) && isWs(b));
}

/** True when `raw[i…]` and `visible[j…]` look like the same text again. Counts
 *  only non-whitespace agreement and lets either side skip whitespace freely,
 *  since rewritten whitespace is exactly what makes the sides drift. */
function agrees(raw: string, i: number, visible: string, j: number): boolean {
  let ii = i;
  let jj = j;
  let matched = 0;
  while (matched < LOOKAHEAD && ii < raw.length && jj < visible.length) {
    const a = raw[ii];
    const b = visible[jj];
    if (isWs(a) && isWs(b)) {
      ii++;
      jj++;
    } else if (isWs(a)) ii++;
    else if (isWs(b)) jj++;
    else if (a !== b) return false;
    else {
      ii++;
      jj++;
      matched++;
    }
  }
  // Running out of either side mid-agreement is still evidence, provided
  // something actually matched (a short tail at the end of the message).
  return matched >= LOOKAHEAD || (matched > 0 && (ii >= raw.length || jj >= visible.length));
}

/** Smallest d ≥ 1 such that skipping d chars on one side makes both sides agree
 *  again, or -1. `skipRaw` picks which side moves. */
function findResync(raw: string, i: number, visible: string, j: number, skipRaw: boolean): number {
  for (let d = 1; d <= WINDOW; d++) {
    const ii = skipRaw ? i + d : i;
    const jj = skipRaw ? j : j + d;
    if (ii >= raw.length || jj >= visible.length) return -1;
    if (agrees(raw, ii, visible, jj)) return d;
  }
  return -1;
}

/** Per-character map from `raw` into `visible`: `map[i]` is the index in
 *  `visible` that raw character i rendered to, or -1 when the renderer dropped
 *  it. Monotonically non-decreasing over the mapped entries. */
export function alignChars(raw: string, visible: string): Int32Array {
  const n = raw.length;
  const m = visible.length;
  const map = new Int32Array(n).fill(-1);
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (chEq(raw[i], visible[j])) {
      map[i] = j;
      i++;
      j++;
      continue;
    }
    // A mismatch is resolved by skipping the side that gets us back in sync
    // soonest; raw wins ties because dropping is what the renderer does.
    const rs = findResync(raw, i, visible, j, true);
    const vs = findResync(raw, i, visible, j, false);
    if (rs >= 0 && (vs < 0 || rs <= vs)) i += rs;
    else if (vs >= 0) j += vs;
    else if (isWs(visible[j]) && !isWs(raw[i])) j++;
    else i++; // nothing in range — assume the renderer dropped this char
  }
  return map;
}

/** Where each token landed in `visible`. `null` = never aligned (dropped
 *  markdown syntax, an excluded subtree, a desynced region). Spans are in token
 *  order and never overlap. */
export function alignTokens(tokens: string[], visible: string): (Span | null)[] {
  const map = alignChars(tokens.join(''), visible);
  const out: (Span | null)[] = [];
  let i = 0;
  for (const t of tokens) {
    let start = -1;
    let end = -1;
    for (let k = i; k < i + t.length; k++) {
      const v = map[k];
      if (v < 0) continue;
      if (start < 0) start = v;
      end = v + 1;
    }
    out.push(start >= 0 ? { start, end } : null);
    i += t.length;
  }
  return out;
}

/** Fraction of the VISIBLE text some token claims. The overlay's trust metric:
 *  a wholesale mismatch (logprobs from a different turn, a render we can't
 *  follow) should paint nothing rather than a plausible-looking lie.
 *
 *  Deliberately measured over the visible side, not over the tokens: a token
 *  that finds no home is usually just markdown syntax the renderer dropped, and
 *  a syntax-heavy turn would score badly on a token-fraction while every word
 *  on screen is correctly painted. Text on screen that NO token claims is the
 *  condition that actually means "these numbers aren't about this text". */
export function visibleCoverage(spans: (Span | null)[], visibleLength: number): number {
  if (visibleLength <= 0) return 0;
  let covered = 0;
  for (const s of spans) if (s) covered += s.end - s.start;
  return Math.min(1, covered / visibleLength);
}
