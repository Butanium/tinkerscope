// The static site's write overlay — an in-memory map, durably backed by IndexedDB.
//
// WHY NOT localStorage (which this replaced): it is capped around 5 MB per origin, and
// a single real workspace body is well past that — `value guarding v2` is 12.3 MB with
// its logprobs already stripped. Worse, the failure was silent-ish: the write threw,
// got caught and console-warned, and then every read came back through the same store
// and returned nothing, so an installed pack opened EMPTY. Measured on this box,
// headless Chromium: localStorage 4.98 MB, IndexedDB quota 6442 MB, and a 37.6 MB
// workspace-shaped payload round-trips through IndexedDB in 484 ms write / 277 ms read.
//
// The shape here exists to keep the ~30 sync `readLocal()` call sites in api-static.ts
// unchanged: IndexedDB is async, so the map is hydrated ONCE up front and is thereafter
// the synchronous source of truth; writes update the map immediately and are flushed to
// IndexedDB in the background. Callers must await `overlayReady` before their first
// read — `api-static` does that for every method in one place (see `gated`), rather
// than relying on ~30 individual awaits nobody would keep correct.

const MEM = new Map<string, unknown>();

let dbPromise: Promise<IDBDatabase | null> | null = null;
let prefix = '';
let lastError: string | null = null;

const DB_NAME = 'tinkerscope-static';
const STORE = 'overlay';

/** Last write failure, for the UI to surface. Null when the overlay is healthy. */
export function overlayError(): string | null {
  return lastError;
}

function openDb(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve) => {
    let req: IDBOpenDBRequest;
    try {
      // Private-browsing modes and disabled-storage settings throw here rather than
      // returning null, so this is a genuine boundary, not defensive noise.
      req = indexedDB.open(DB_NAME, 1);
    } catch (e) {
      console.warn('static overlay: IndexedDB unavailable, edits are session-only', e);
      resolve(null);
      return;
    }
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => {
      console.warn('static overlay: IndexedDB open failed, edits are session-only', req.error);
      resolve(null);
    };
    req.onblocked = () => resolve(null);
  });
  return dbPromise;
}

function idbGetAll(db: IDBDatabase): Promise<[string, unknown][]> {
  return new Promise((resolve) => {
    const out: [string, unknown][] = [];
    const tx = db.transaction(STORE, 'readonly');
    const cursor = tx.objectStore(STORE).openCursor();
    cursor.onsuccess = () => {
      const c = cursor.result;
      if (!c) return;
      if (typeof c.key === 'string' && c.key.startsWith(prefix)) out.push([c.key, c.value]);
      c.continue();
    };
    tx.oncomplete = () => resolve(out);
    tx.onerror = () => resolve(out);
    tx.onabort = () => resolve(out);
  });
}

function idbPut(db: IDBDatabase, key: string, value: unknown): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    // Structured-clone straight from the in-memory value: no JSON round-trip, which
    // is most of why this is fast enough to do on every save.
    tx.objectStore(STORE).put(value, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error('aborted'));
  });
}

function idbDelete(db: IDBDatabase, key: string): Promise<void> {
  return new Promise((resolve) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).delete(key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
    tx.onabort = () => resolve();
  });
}

// Writes are serialized through one chain so a burst of saves can't interleave
// transactions on the same key, and so a failure is reported once per key.
let queue: Promise<void> = Promise.resolve();

function enqueue(fn: (db: IDBDatabase) => Promise<void>, what: string): void {
  queue = queue
    .then(async () => {
      const db = await openDb();
      if (!db) return;
      await fn(db);
      lastError = null;
    })
    .catch((e) => {
      lastError = `${e?.name || e}`;
      console.warn(`static overlay write failed (${what}); edits will not survive a reload`, e);
    });
}

/**
 * Hydrate the in-memory map. Resolves even when storage is unavailable — the overlay
 * is best-effort by design (a static site's baked content is the truth), so failure
 * degrades to "this session's edits don't survive a reload", never to a broken page.
 */
export async function hydrateOverlay(ns: string): Promise<void> {
  prefix = ns;
  MEM.clear();

  // Adopt anything a pre-IndexedDB build of this site left behind, so an existing
  // visitor's installs survive the upgrade. The localStorage copy is deliberately NOT
  // deleted: browsers cache bundles aggressively, and a visitor who loads an older
  // cached build afterwards should still find their data where that build looks.
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k || !k.startsWith(prefix)) continue;
      const raw = localStorage.getItem(k);
      if (raw != null) MEM.set(k, JSON.parse(raw));
    }
  } catch (e) {
    console.warn('static overlay: could not read legacy localStorage entries', e);
  }

  const db = await openDb();
  if (!db) return;
  try {
    // IndexedDB wins over the legacy copy: it is where writes have been going since
    // the upgrade, so it is never the staler of the two.
    for (const [k, v] of await idbGetAll(db)) MEM.set(k, v);
  } catch (e) {
    console.warn('static overlay: hydrate failed, starting empty', e);
  }
}

/**
 * Read a copy. The COPY matters: the localStorage implementation this replaced went
 * through `JSON.parse` on every read, so callers have always been handed a private
 * object and are free to mutate what they get (`getWorkspace` hands its body to the
 * workspace store, which reconciles panels in place). Returning the map's own
 * reference instead would alias the overlay to live UI state and corrupt it silently.
 * Cloning on read keeps that contract exactly, so no consumer needed auditing.
 */
export function readOverlay<T>(key: string, fallback: T): T {
  const hit = MEM.get(prefix + key);
  return hit === undefined ? fallback : (structuredClone(hit) as T);
}

export function writeOverlay(key: string, value: unknown): void {
  const full = prefix + key;
  // Clone on the way in too, so a later mutation of the caller's object can't
  // retroactively change what we believe is stored.
  const stored = structuredClone(value);
  MEM.set(full, stored);
  enqueue((db) => idbPut(db, full, stored), key);
}

export function dropOverlay(key: string): void {
  const full = prefix + key;
  MEM.delete(full);
  enqueue((db) => idbDelete(db, full), key);
}

/** Resolve once every queued write has been flushed. Tests and smokes need this;
 *  the app never has to wait, since the in-memory map is already authoritative. */
export function overlayFlushed(): Promise<void> {
  return queue;
}

/** Bytes currently held, and the browser's quota — for the "this won't fit" check
 *  before installing a large pack. */
export async function overlayQuota(): Promise<{ usage: number; quota: number } | null> {
  try {
    const est = await navigator.storage.estimate();
    return { usage: est.usage ?? 0, quota: est.quota ?? 0 };
  } catch {
    return null;
  }
}
