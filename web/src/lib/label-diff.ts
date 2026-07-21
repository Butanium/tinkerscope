// Diff-view labels for sibling list rows — the "smarter" successor to
// label-split's tail-preserve for the case it can't handle: sibling runs that
// share BOTH ends and differ only in the MIDDLE (e.g.
// `basevsinstr_april_base_ed_sheeran_pos_s1_lr1e-3` vs `…_instruct_…` — a tail
// cap renders both identically).
//
// Given the VISIBLE sibling labels, compute for each a compact rendering that
// shows every VARYING segment in full and collapses runs of cluster-wide-constant
// segments to a single dimmed `…`.
//
// One strategy, positional voting: cluster the visible labels by their first
// segment, then within a cluster a position is "shared" iff EVERY member has that
// index and they all agree there. Shared interior runs elide to one `…`; the
// first segment (family anchor) and each member's own last segment always stay;
// varying positions and the member-unique tail (indices past the shortest member)
// always show. That reaches interior constants — `ed_sheeran` sitting between the
// varying model and the varying pos/seed/lr — which a prefix/suffix-only scheme
// can't touch; and on a ragged family (weird-personas `health_cigarette_*`, 3–7
// segments) it naturally degrades to just eliding the shared `…cigarette…` prefix,
// since nothing deeper is constant across every member.
//
// Invariants (executable in label-diff.test.ts against both real fixture families):
//  (a) two distinct visible labels never render identically — the position where
//      they differ is, by construction, never shared, so it's always shown;
//  (b) only cluster-wide-constant segments (all members present + agreeing) elide;
//  (c) each maximal elided run is exactly one `…`;
//  (d) singleton clusters / no siblings → null (caller falls back to TruncLabel);
//  (e) deterministic — same input set, same output.
//
// The caller (ModelTypeahead) renders the diff when it exists and falls back to
// TruncLabel (tail-preserve) on null. Full label stays in the tooltip / aria-label
// and the typeahead's search field, so filtering is unaffected.

/** One piece of a rendered diff label. `anchor` = a cluster-constant segment kept
 *  for readability (family prefix / trailing anchor / status icon) shown dimmed;
 *  `vary` = a segment that differs across the cluster, shown at full emphasis;
 *  `elision` = a `…` standing in for a run of elided cluster-constant segments. */
export type DiffPart = { text: string; kind: 'anchor' | 'vary' | 'elision' };
export type DiffRender = DiffPart[];

/** A tokenized segment: `text` plus the separator char that preceded it in the
 *  source label (`''` for the first). Reconstruction is `sep + text` joined. */
type Seg = { sep: string; text: string };

const ELLIPSIS = '…';
// Leading status glyph the catalog prepends (⚠/? sampleability, ◆/◇/↗ group
// markers), always as "<glyph><whitespace>". Peeled off the body so an unavailable
// `⚠ basevsinstr_…` still clusters with a live `basevsinstr_…`, then re-attached.
// (⊘ kept for back-compat with any older-rendered label.)
const ICON = /^([⚠⊘?◆◇↗]\s+)/;

/** Split on `_` and `/` (but NOT `-`, so `lr1e-3` / `deepseek-chat` stay whole),
 *  keeping each segment's preceding separator so the label reconstructs exactly. */
function tokenize(label: string): Seg[] {
  const segs: Seg[] = [];
  let sep = '';
  let text = '';
  for (const ch of label) {
    if (ch === '_' || ch === '/') {
      segs.push({ sep, text });
      sep = ch;
      text = '';
    } else {
      text += ch;
    }
  }
  segs.push({ sep, text });
  return segs;
}

const segKey = (s: Seg): string => s.sep + s.text;

/** Walk a member's segments emitting parts: maximal runs of elidable positions
 *  collapse to one `…`; a shown segment carries its separator only when adjacent
 *  to another shown segment (an `…` swallows the gap). `dim[p]` marks a shown
 *  segment as an anchor (cluster-constant, kept for readability) vs varying. */
function buildRender(segs: Seg[], elidable: boolean[], dim: boolean[], icon: string): DiffRender {
  const parts: DiffRender = [];
  if (icon) parts.push({ text: icon, kind: 'anchor' });
  let inElision = false;
  let prevShown = false;
  for (let p = 0; p < segs.length; p++) {
    if (elidable[p]) {
      if (!inElision) parts.push({ text: ELLIPSIS, kind: 'elision' });
      inElision = true;
      prevShown = false;
    } else {
      const sep = prevShown ? segs[p].sep : '';
      parts.push({ text: sep + segs[p].text, kind: dim[p] ? 'anchor' : 'vary' });
      inElision = false;
      prevShown = true;
    }
  }
  return parts;
}

/**
 * Compute a diff render for every label in `labels`, aligned by index. An entry
 * is a `DiffRender` when the label belongs to a cluster of ≥2 and at least one
 * segment is elided; otherwise `null` (caller falls back to TruncLabel).
 */
export function diffLabels(labels: readonly string[]): (DiffRender | null)[] {
  const icons = labels.map((l) => ICON.exec(l)?.[1] ?? '');
  const bodies = labels.map((l, i) => l.slice(icons[i].length));
  const segs = bodies.map(tokenize);

  // Cluster by the body's seg-0 key. Cross-cluster distinctness is carried by
  // the always-shown leading anchor, so each family diffs independently.
  const clusters = new Map<string, number[]>();
  segs.forEach((s, i) => {
    const key = s.length ? segKey(s[0]) : '';
    const bucket = clusters.get(key);
    if (bucket) bucket.push(i);
    else clusters.set(key, [i]);
  });

  const out: (DiffRender | null)[] = labels.map(() => null);

  for (const idxs of clusters.values()) {
    if (idxs.length < 2) continue; // singleton cluster → no diff (invariant d)
    const members = idxs.map((i) => segs[i]);
    const minLen = Math.min(...members.map((m) => m.length));

    // A position < minLen is shared iff every member agrees there (invariant b);
    // positions ≥ minLen belong to the member-unique tail and always show.
    const shared: boolean[] = [];
    for (let p = 0; p < minLen; p++) {
      const k = segKey(members[0][p]);
      shared[p] = members.every((m) => segKey(m[p]) === k);
    }

    for (const i of idxs) {
      const m = segs[i];
      const L = m.length;
      // Elide shared interior runs; always keep seg-0 (family anchor) and this
      // member's own last segment (a trailing anchor reads better than a
      // dangling `…`). dim = shown-but-shared (anchor) vs varying/tail.
      const elidable = m.map((_s, p) => p < minLen && shared[p] && p !== 0 && p !== L - 1);
      if (!elidable.some(Boolean)) continue; // nothing to collapse → tail-preserve
      const dim = m.map((_s, p) => p < minLen && shared[p]);
      out[i] = buildRender(m, elidable, dim, icons[i]);
    }
  }

  guardCollisions(labels, out);
  return out;
}

/** Backstop for invariant (a): if two DISTINCT labels somehow render identically,
 *  drop both to null so the caller's tail-preserve + tooltip disambiguates. The
 *  structural argument says this never fires on real input; it's cheap insurance
 *  against a tokenization edge (e.g. two labels differing only by a separator that
 *  fell inside an elided run). True duplicate labels rendering alike is fine. */
function guardCollisions(labels: readonly string[], out: (DiffRender | null)[]): void {
  const byRender = new Map<string, number[]>();
  out.forEach((r, i) => {
    if (!r) return;
    const key = r.map((p) => p.text).join(' ');
    const bucket = byRender.get(key);
    if (bucket) bucket.push(i);
    else byRender.set(key, [i]);
  });
  for (const idxs of byRender.values()) {
    if (idxs.length < 2) continue;
    const distinct = new Set(idxs.map((i) => labels[i]));
    if (distinct.size >= 2) for (const i of idxs) out[i] = null;
  }
}
