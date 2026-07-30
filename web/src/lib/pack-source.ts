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

/**
 * `demo` → `demo (2)` → `demo (3)`… until `isFree(candidate)`. THE collision-rename
 * rule, and the exact mirror of `_dedupe_conflicting` in src/tinkerscope/pack.py —
 * both transports install the same pack link, so a divergence here would give the
 * same link different ids depending on whether a backend was involved.
 *
 * Two properties the caller depends on:
 *
 * - **`isFree` is asked about the derived ID, never the display name.** Renaming
 *   because a NAME is taken would fork a workspace off its canonical
 *   `pack-<pack>-<ws>` id while that id stays free, breaking the determinism the
 *   feature rests on (a later open reads as never-installed, `&open=<canonical>`
 *   misses). Names aren't unique anywhere else in the app either.
 * - **An already-suffixed name continues its own counter**: `x (5)` → `x (6)`,
 *   never back to `x (2)` and never stacking into `x (5) (2)`.
 */
export function bumpUntilFree(name: string, isFree: (candidate: string) => boolean): string {
  if (isFree(name)) return name;
  const m = name.match(/^(.*) \((\d+)\)$/);
  const stem = m ? m[1] : name;
  let n = m ? parseInt(m[2], 10) + 1 : 2;
  while (!isFree(`${stem} (${n})`)) n++;
  return `${stem} (${n})`;
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
