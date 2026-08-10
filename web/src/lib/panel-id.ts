/** Panel-id minting: monotonic per workspace, never reused.
 *
 * Ids used to be minted by gap-filling — the reserved name 'compare' for slot 1,
 * then the lowest free p-N. So closing a column and adding one handed the SAME id
 * to a different model: `panel:node` handles silently re-pointed, and a workspace's
 * own history became unquotable. Now every mint is a fresh number.
 *
 * 'primary' / 'compare' are no longer minted but remain valid ids forever — every
 * workspace saved before this uses them, as does the legacy {tree, compare_tree}
 * migration. They carry no number, so a p-N mint can never collide with them.
 *
 * Pure functions in a plain module (not the .svelte.ts store) so `node` can test
 * them directly.
 */

/** A workspace body, as far as panel ids are concerned. */
export type PanelIdSource = {
  trees?: Record<string, unknown> | null;
  panels?: { id?: string }[] | null;
  seen_panels?: string[] | null;
};

/** The id a layout's first panel gets when something has to invent one. Mirrors
 *  `DEFAULT_PANEL_ID` in api/state.py — the two must agree, since a fresh bus and a
 *  fresh workspace have to name the same panel. */
export const FIRST_PANEL_ID = 'p-1';

const P_N = /^p-(\d+)$/;

/** Seed `panel_seq` for a workspace saved before the counter was persisted: the
 *  highest N among its p-N ids, so the next mint can't collide with a panel it
 *  already has. Scans trees + layout + seen_panels — seen_panels because a CLOSED
 *  panel is absent from the other two, and re-issuing its number is precisely the
 *  bug being fixed. */
export function highestPanelSeq(conv: PanelIdSource): number {
  let max = 0;
  for (const id of [
    ...Object.keys(conv.trees ?? {}),
    ...(conv.panels ?? []).map((p) => p?.id ?? ''),
    ...(conv.seen_panels ?? [])
  ]) {
    const m = P_N.exec(id);
    if (m) max = Math.max(max, Number(m[1]));
  }
  return max;
}

/** Next never-before-used id, given the counter and every id the workspace knows
 *  about. `taken` is belt-and-braces: an imported or hand-edited workspace can
 *  carry a p-N above its own counter. */
export function mintPanelId(seq: number, taken: Iterable<string>): { id: string; seq: number } {
  const used = taken instanceof Set ? taken : new Set(taken);
  let id: string;
  do {
    id = 'p-' + ++seq;
  } while (used.has(id));
  return { id, seq };
}

/** One panel's layout row, as far as this module cares (mirrors `PanelLayout`). */
type Row = { id: string; run_id: string | null; checkpoint: string | null };

/** Layout for a body that stores NO `panels` — i.e. one saved before the per-
 *  workspace layout existed. Returns null to mean "keep what's shown".
 *
 *  Such a body's TREE KEYS are the only record of which panels it has, and they are
 *  the pre-monotonic reserved names ('primary', 'compare'). Keeping the shown layout
 *  wholesale only worked while the default first panel was ALSO called 'primary';
 *  once ids are minted `p-<n>` nothing matches, no tree gets a column, and the
 *  workspace opens BLANK with its turns still on disk and unreachable from the UI.
 *
 *  So: adopt the tree's own ids whenever one of them would otherwise go unrendered,
 *  inheriting models positionally from what's shown (a legacy body records none). A
 *  layout that already covers every tree is left alone, extra blank panels included —
 *  that was the behavior before, and dropping a column is not this function's job. */
export function legacyLayout(treeIds: string[], shown: Row[]): Row[] | null {
  if (!treeIds.some((id) => !shown.some((p) => p.id === id))) return null;
  return treeIds.map((id, i) => ({
    id,
    run_id: shown[i]?.run_id ?? null,
    checkpoint: shown[i]?.checkpoint ?? null
  }));
}
