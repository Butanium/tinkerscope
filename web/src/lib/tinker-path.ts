/** Recognising a tinker sampler path typed into the model picker.
 *
 * The picker's search box doubles as the way in for a checkpoint that is in NO local
 * list — a collaborator's path, trained on another account, absent from the sweep. So
 * a query that yields no matches is not necessarily a dead end: if it LOOKS like a
 * path, the picker offers to add it and asks tinker whether it is real.
 *
 * The test is deliberately loose (scheme prefix only). Shape validation belongs to
 * tinker, which answers with the exact expected form — repeating that rule here would
 * give two sources of truth for one answer, and ours would be the one that goes stale.
 * A malformed path therefore still gets the row, and earns a red state with tinker's
 * own message instead of silently looking like a typo.
 */

export const TINKER_SCHEME = 'tinker://';

export function looksLikeSamplerPath(query: string): boolean {
  return query.trim().startsWith(TINKER_SCHEME);
}

/** Short display form: the checkpoint name plus the first 8 chars of the model id —
 *  enough to recognise, short enough for one row. Mirrors the server's `ckpt_label`
 *  minus the date, which we don't have before the probe answers. */
export function shortPathLabel(path: string): string {
  const body = path.trim().slice(TINKER_SCHEME.length).replace(/\/+$/, '');
  const id = body.split(':', 1)[0].split('/', 1)[0].slice(0, 8);
  if (!id) return path.trim();
  // Only a body with a `/` carries a checkpoint name; without one the last segment
  // IS the model id, and `934cea31 · 934cea31-0f38` says the same thing twice.
  const name = body.includes('/') ? body.split('/').pop() : '';
  return name ? `${id} · ${name}` : id;
}
