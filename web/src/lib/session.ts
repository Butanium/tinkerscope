// The browser's SESSION id — the key a `--multi-user` server (api/session.py)
// files this browser's sidebar under: panel selection, open workspace, sampling
// params, `running`. Sent on every request (api.ts: a header on fetches, a query
// param on the EventSource, which cannot set headers). Per BROWSER PROFILE, not
// per tab (localStorage): two tabs of one person keep today's shared-bus
// semantics (claim-on-focus, "the tab you last looked at is the one tinkpg
// drives"); two people are two profiles and never share one.
//
// `?u=<id>` in the URL SETS the id — join a colleague's session, or just name
// yours so `tinkpg --session clement` reads well — and +page strips it once
// read, so a link copied from the address bar never carries a session: that
// would be the one way to reintroduce the shared-sidebar clobber this exists
// to remove.
//
// A single-user server ignores the id (everything maps to `default`); the
// browser still sends it — one code path, and the SERVER decides. PURE apart
// from `sessionId()` (session.test.ts runs the rest under node).

export const SESSION_STORAGE_KEY = 'tinkerscope-session';
export const SESSION_URL_PARAM = 'u';
export const SESSION_HEADER = 'x-tinkerscope-session';
export const SESSION_QUERY = 'session';
// Mirror of api/session.py's rule: a bad id is a 400 there, so never send one.
const VALID = /^[A-Za-z0-9_.-]{1,64}$/;

export function isValidSessionId(id: unknown): id is string {
  return typeof id === 'string' && VALID.test(id);
}

/** A fresh anonymous id: `u-` + 4 base36 chars — short enough to read off the
 *  topbar chip and type after `--session`. */
export function mintSessionId(rand: () => number = Math.random): string {
  let s = '';
  while (s.length < 4) s += Math.floor(rand() * 36).toString(36);
  return `u-${s}`;
}

/** Which id this page uses, and whether the URL supplied it (so the caller
 *  strips it). Precedence: `?u=` › stored › minted; an invalid value at either
 *  level is ignored rather than sent. */
export function resolveSessionId(
  search: string,
  stored: string | null,
  mint: () => string = mintSessionId
): { id: string; fromUrl: boolean } {
  const fromUrl = new URLSearchParams(search).get(SESSION_URL_PARAM);
  if (isValidSessionId(fromUrl)) return { id: fromUrl, fromUrl: true };
  if (isValidSessionId(stored)) return { id: stored, fromUrl: false };
  return { id: mint(), fromUrl: false };
}

let cached: string | null = null;

/** The id every request carries. Memoized for the page's life; persisted to
 *  localStorage on first use (a `?u=` overrides what was stored). Read the URL
 *  EARLY — +page calls this before it strips `?u=`. */
export function sessionId(): string {
  if (cached) return cached;
  if (typeof window === 'undefined') return (cached = 'default');
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(SESSION_STORAGE_KEY);
  } catch {
    /* storage blocked → a per-page-load id, still a valid session */
  }
  const { id } = resolveSessionId(window.location.search, stored);
  cached = id;
  if (id !== stored) {
    try {
      localStorage.setItem(SESSION_STORAGE_KEY, id);
    } catch {
      /* see above */
    }
  }
  return cached;
}
