// `?w=` accepts either a saved workspace ID or a PACK SOURCE (a path or URL to a
// share pack), and this module owns telling them apart plus the naming a collision
// needs. Pure — no fetch, no DOM (the loading half is lib/pack-install.ts).
//
// The discriminator is free: the workspace store's ids are `^[A-Za-z0-9_-]+$`
// (api/workspace_store.py `_SAFE_ID`), so a value carrying `/`, `:` or `.` cannot be
// an id under any circumstance. That makes the extension fully back-compatible —
// every previously-valid `?w=` still resolves as an id, and no second query param
// is needed.

const ID_RE = /^[A-Za-z0-9_-]+$/;

/** True when a `?w=` value must be interpreted as a pack path/URL, not a workspace id. */
export function isPackSource(value: string): boolean {
  return value.length > 0 && !ID_RE.test(value);
}

/** True for a source only a BACKEND can read (a filesystem path). A static site can
 *  fetch http(s) but has no filesystem, so this is what it must refuse. */
export function isLocalPath(source: string): boolean {
  return !/^https?:\/\//i.test(source);
}

/** Filename-safe slug within the workspace-store id charset — mirrors pack.py `_slug`. */
export function slug(s: string): string {
  const out = s.replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
  return out || 'x';
}

/** The deterministic id `apply_pack` gives a pack's workspace: `pack-<pack>-<ws>`.
 *  Mirrored here so the browser can predict collisions before installing. */
export function packWorkspaceId(packName: string, workspaceName: string): string {
  return `pack-${slug(packName)}-${slug(workspaceName)}`;
}

/** `"name" → "name (2)"`, incrementing until unused. Used for the "create new"
 *  branch of a collision, so both copies stay tellable apart in the picker. */
export function nextAvailableName(name: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  if (!used.has(name)) return name;
  // An already-suffixed name increments its own counter instead of stacking
  // ("x (2)" → "x (3)", never "x (2) (2)").
  const m = name.match(/^(.*) \((\d+)\)$/);
  const stem = m ? m[1] : name;
  let n = m ? parseInt(m[2], 10) + 1 : 2;
  while (used.has(`${stem} (${n})`)) n++;
  return `${stem} (${n})`;
}

/** `"id" → "id-2"`, incrementing until unused (the id twin of nextAvailableName). */
export function nextAvailableId(id: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  if (!used.has(id)) return id;
  const m = id.match(/^(.*)-(\d+)$/);
  const stem = m ? m[1] : id;
  let n = m ? parseInt(m[2], 10) + 1 : 2;
  while (used.has(`${stem}-${n}`)) n++;
  return `${stem}-${n}`;
}

/** A short, human label for a source — what the install prompt shows. A URL shows
 *  its host + filename; a path shows its basename. */
export function sourceLabel(source: string): string {
  try {
    if (!isLocalPath(source)) {
      const u = new URL(source);
      const file = u.pathname.split('/').filter(Boolean).pop() || u.pathname;
      return `${file} (${u.host})`;
    }
  } catch {
    /* not a parseable URL — fall through to the basename */
  }
  return source.split('/').filter(Boolean).pop() || source;
}
