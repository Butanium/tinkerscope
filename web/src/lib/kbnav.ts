// Keyboard row-navigation helpers for the workspace's focused-row arrows
// (see +page.svelte "Keyboard row navigation": click a row → focus it; ↑/↓
// walk the panel's rendered view, ←/→ drive the row's ‹k/N› sibling cycler,
// Escape clears). Index math is pure (node-testable via kbnav.test.ts); the
// two guards read the DOM but stay svelte-free.

export const NAV_KEYS = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Escape'] as const;
export type NavKey = (typeof NAV_KEYS)[number];

export function isNavKey(key: string): key is NavKey {
  return (NAV_KEYS as readonly string[]).includes(key);
}

/** Step a focused row index by `delta`, clamped to [0, len-1] — NO wrap: the
 *  thread has real ends (wrapping is for SIBLING cycling, not the vertical
 *  walk). Also repairs a stale out-of-range index (the view shrank under the
 *  focus, e.g. a delete). Returns null for an empty view (nothing to focus). */
export function moveIndex(index: number, delta: number, len: number): number | null {
  if (len <= 0) return null;
  return Math.min(Math.max(index + delta, 0), len - 1);
}

/** True when a key event targets a place where the user is TYPING — any
 *  input/textarea/select or contenteditable (composer, prefill, panel-send,
 *  rename fields, edit textareas…). Nav keys must never fire there. */
export function isEditableTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || typeof el.tagName !== 'string') return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
}

/** True while any workspace dialog is up — every modal wraps Modal.svelte,
 *  whose chrome renders `.modal-overlay` (and owns its own Escape). */
export function anyModalOpen(): boolean {
  return typeof document !== 'undefined' && document.querySelector('.modal-overlay') != null;
}
