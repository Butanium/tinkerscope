// Static (server-less) mode — the read-only site emitted by `tinkerscope site export`.
//
// A static export injects a manifest global into index.html, so detection is
// SYNCHRONOUS at module init: api.ts must pick its transport before any consumer
// touches it, and an async probe (fetching a manifest file) would race that. A live
// instance never defines the global ⇒ zero cost and zero behavior change there.
//
// Reads come from baked JSON under the data root; writes go to the IndexedDB-backed
// overlay in lib/overlay-store (localStorage until 2026-07-30 — its ~5 MB cap made a
// real workspace impossible to install), namespaced per site because one github.io
// origin hosts many of them. See docs/STATIC_SITE.md for the on-disk layout and what
// the mode can/can't do.

import { hydrateOverlay } from './overlay-store';

export type StaticManifest = {
  version: number;
  /** Namespace for this site's localStorage overlay. */
  site: string;
  title?: string;
  description?: string;
  generated_at?: string;
  /** Data root, resolved against document.baseURI. Default 'data/'. */
  data?: string;
  /** Workspace to open when the URL names none (else the first in the index). */
  default_workspace?: string | null;
  /** Where this content is published as a share pack, if anywhere — turns the
   *  read-only badge's panel into a command that reproduces what's on screen. */
  pack_url?: string | null;
};

declare global {
  interface Window {
    __TSCOPE_STATIC__?: StaticManifest;
  }
}

const MANIFEST: StaticManifest | null =
  typeof window !== 'undefined' && window.__TSCOPE_STATIC__ ? window.__TSCOPE_STATIC__ : null;

/** True when this bundle is running as an exported static site (no backend). */
export const isStatic = MANIFEST !== null;
/** The injected manifest, or null on a live instance. */
export const manifest = MANIFEST;
/**
 * UI capability flag: hide every control that would mutate server state or sample.
 * Aliased to `isStatic` today and kept separate on purpose — the transport being
 * file-backed and the UI being read-only are different claims, and a future
 * read-only-against-a-live-server mode would set only this one.
 */
export const readOnly = isStatic;

// A trailing slash matters: new URL('state.json', '…/data') would resolve to
// '…/state.json'. Note a subpath deploy visited WITHOUT its trailing slash
// ('user.github.io/repo') resolves relative URLs against the parent — GitHub Pages
// 301s that to '/repo/' before the page ever loads, so it self-corrects.
const DATA_ROOT = MANIFEST
  ? new URL((MANIFEST.data || 'data/').replace(/\/*$/, '/'), document.baseURI).href
  : '';

/** Absolute URL of a baked data file, e.g. dataUrl('workspaces/index.json'). */
export function dataUrl(rel: string): string {
  return new URL(rel, DATA_ROOT).href;
}

// Namespaced by the site's own PATH, not just its title. `site` is a slug of the
// title, and one origin hosts many exports — two both called "demo" under
// user.github.io would otherwise share an overlay, so a visitor's installs and edits
// on one would surface in the other. The path is distinct by construction (two sites
// can't occupy the same URL), and it costs nothing to include.
const SITE_PATH =
  MANIFEST && typeof document !== 'undefined' ? new URL('.', document.baseURI).pathname : '/';
const PREFIX = `tscope-static:${MANIFEST?.site ?? 'default'}@${SITE_PATH}:`;

// Storage is a system boundary (private modes throw, quotas run out), and the overlay
// is best-effort by design — a static site's baked content is the truth, so a failure
// degrades to "this session's edits don't survive a reload", warned, never fatal.
// The read/write primitives live in lib/overlay-store; this module owns only the
// namespace and the one-time hydrate.
export { readOverlay, writeOverlay, dropOverlay, overlayError } from './overlay-store';

/**
 * Resolves when the overlay map has been loaded out of IndexedDB. Reads are
 * synchronous afterwards, so EVERY entry point into api-static awaits this once
 * (see `gated` there) rather than each of its ~30 methods remembering to.
 * On a live instance there is no overlay and this is already resolved.
 */
export const overlayReady: Promise<void> = isStatic
  ? hydrateOverlay(PREFIX)
  : Promise.resolve();
