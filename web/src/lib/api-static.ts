// The static-mode backend: baked JSON for reads, an IndexedDB overlay for writes.
//
// Shape-compatible with the HTTP client in ./api.ts (`ApiClient`), so every consumer
// is transport-blind. What differs, and why:
//
//   - **Baked workspaces are immutable.** A write targeting one is accepted and
//     dropped (the caller's optimistic mirror still shows it for the session; a
//     reload restores the shipped truth). Persisting it would let an incidental
//     layout normalization — panels reconciled against a models list a static site
//     doesn't have — permanently shadow the exported content with something worse.
//     Workspaces the VISITOR installs (a `?w=<pack-url>` load) live in the overlay
//     and do persist their edits.
//   - **Sampling and deletes of baked workspaces throw.** Their UI is hidden in
//     read-only mode, so a call means a bug — surfacing it beats a silent no-op
//     that reports success.
//   - **No bus.** `sse()` synthesizes one snapshot from the baked state so the
//     mirror seeds through the normal path, then goes quiet; state patches apply
//     in memory, mirroring api/state.py's `_apply_patch`.

import type { ApiClient } from './api';
import { dataUrl, readOverlay, writeOverlay, dropOverlay, overlayReady } from './static-mode';
import { splitTrees } from './node-split';
import type {
  Run,
  OpenRouterModel,
  TinkerModelsResponse,
  TinkerModel,
  OpenRouterAvailableResponse,
  Health,
  PlaygroundState,
  StatePatch,
  PanelState,
  ChatMessage,
  Workspace,
  WorkspaceSummary,
  NodeBlobs,
  HighlightRule,
  PanelLayout
} from './types';
import type { ConvTree } from './tree';

// ── baked reads ──────────────────────────────────────────────────────────────
/** Fetch a baked file; a 404 yields `fallback` (the file is optional). */
async function baked<T>(rel: string, fallback: T): Promise<T> {
  const r = await fetch(dataUrl(rel));
  if (r.status === 404) return fallback;
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${rel}`);
  return (await r.json()) as T;
}

/** Fetch a baked file that MUST exist — a miss means a broken export. */
async function bakedStrict<T>(rel: string): Promise<T> {
  const r = await fetch(dataUrl(rel));
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${rel}`);
  return (await r.json()) as T;
}

// Cache the small always-read files so a workspace switch doesn't refetch them.
const memo = new Map<string, Promise<unknown>>();
function once<T>(rel: string, load: () => Promise<T>): Promise<T> {
  let p = memo.get(rel) as Promise<T> | undefined;
  if (!p) {
    p = load();
    memo.set(rel, p);
  }
  return p;
}

// ── overlay keys ─────────────────────────────────────────────────────────────
const K = {
  createdIds: 'ws.created', // ids of visitor-installed workspaces, in install order
  body: (id: string) => `ws.body.${id}`,
  blobs: (id: string) => `ws.blobs.${id}`,
  summary: (id: string) => `ws.summary.${id}`,
  prefs: 'prefs',
  highlights: 'highlights',
  highlightsSeeded: 'highlights.seeded',
  pins: 'pins',
  tinkerModels: 'models.tinker', // catalog entries added by an installed pack
  orModels: 'models.openrouter'
};

function createdIds(): string[] {
  return readOverlay<string[]>(K.createdIds, []);
}
function isOverlay(id: string): boolean {
  return createdIds().includes(id);
}
function nowIso(): string {
  return new Date().toISOString();
}

// ── in-memory shared state (there is no bus) ─────────────────────────────────
let stateCache: PlaygroundState | null = null;

async function ensureState(): Promise<PlaygroundState> {
  if (!stateCache) stateCache = await bakedStrict<PlaygroundState>('state.json');
  return stateCache;
}

function lightMsgs(msgs: unknown): ChatMessage[] {
  return Array.isArray(msgs) ? (msgs as ChatMessage[]) : [];
}

// Mirrors api/state.py `_PANEL_FIELDS` — `thread_system_prompt` included, or a
// single-panel patch of it would be silently dropped here but not on a live instance.
const PANEL_FIELDS = new Set(['run_id', 'checkpoint', 'messages', 'thread_system_prompt']);

/** Mirror of api/state.py `_apply_patch`. The cross-workspace guard
 *  (`_drop_foreign_workspace_keys`) is deliberately absent: it protects a
 *  process-global bus from a SECOND writer, and a static site has exactly one. */
function applyPatch(state: PlaygroundState, patch: StatePatch): void {
  const panelId = patch.panel;
  const byId = (pid: string): PanelState | undefined => state.panels.find((p) => p.id === pid);
  for (const [k, v] of Object.entries(patch) as [string, any][]) {
    if (k === 'panel') continue;
    if (k === 'panels') {
      state.panels = (v as PanelState[]).map((p) => ({
        id: p.id,
        run_id: p.run_id ?? null,
        checkpoint: p.checkpoint ?? null,
        messages: lightMsgs(p.messages),
        thread_system_prompt: p.thread_system_prompt ?? null
      }));
    } else if (k === 'panel_messages') {
      for (const [pid, msgs] of Object.entries(v || {})) {
        const p = byId(pid);
        if (p) p.messages = lightMsgs(msgs);
      }
    } else if (k === 'panel_thread_system') {
      for (const [pid, ts] of Object.entries(v || {})) {
        const p = byId(pid);
        if (p) p.thread_system_prompt = ts as string | null;
      }
    } else if (PANEL_FIELDS.has(k)) {
      if (panelId != null) {
        const p = byId(panelId);
        if (p) (p as any)[k] = k === 'messages' ? lightMsgs(v) : v;
      }
    } else if (k in state) {
      (state as any)[k] = v;
    }
  }
}

// ── workspaces ───────────────────────────────────────────────────────────────
async function bakedIndex(): Promise<WorkspaceSummary[]> {
  return once('workspaces.json', () => baked<WorkspaceSummary[]>('workspaces.json', []));
}

function summaryOf(body: Workspace): WorkspaceSummary {
  return {
    id: body.id,
    name: body.name,
    created_at: body.created_at,
    updated_at: body.updated_at,
    panels: body.panels ?? []
  };
}

function overlayBody(id: string): Workspace | null {
  return readOverlay<Workspace | null>(K.body(id), null);
}

function saveOverlayBody(body: Workspace): void {
  writeOverlay(K.body(body.id), body);
  writeOverlay(K.summary(body.id), summaryOf(body));
}

/** Install a workspace into the overlay, splitting inline heavy fields into blobs
 *  exactly as the server's upsert does. Returns the stored light body. */
function installWorkspace(entry: {
  id: string;
  name: string;
  system_prompt?: string | null;
  system_enabled?: boolean | null;
  trees?: Record<string, ConvTree>;
  panels?: PanelLayout[];
  reduced_panels?: string[];
  send_targets?: string[];
  seen_panels?: string[];
}): Workspace {
  const [light, blobs] = splitTrees(entry.trees ?? {});
  const ts = nowIso();
  const prev = overlayBody(entry.id);
  const body: Workspace = {
    id: entry.id,
    name: entry.name,
    system_prompt: entry.system_prompt ?? null,
    system_enabled: entry.system_enabled ?? null,
    trees: light,
    panels: entry.panels ?? [],
    reduced_panels: entry.reduced_panels ?? [],
    send_targets: entry.send_targets ?? [],
    seen_panels: entry.seen_panels ?? [],
    created_at: prev?.created_at ?? ts,
    updated_at: ts
  };
  saveOverlayBody(body);
  // Write-once, like the server's blob store: keep whatever a previous install left.
  const existing = readOverlay<Record<string, NodeBlobs>>(K.blobs(entry.id), {});
  writeOverlay(K.blobs(entry.id), { ...blobs, ...existing });
  const ids = createdIds();
  if (!ids.includes(entry.id)) writeOverlay(K.createdIds, [...ids, entry.id]);
  return body;
}

// ── highlights / pins (fully visitor-owned, seeded from the bake) ────────────
async function highlights(): Promise<HighlightRule[]> {
  const seeded = readOverlay<boolean>(K.highlightsSeeded, false);
  if (seeded) return readOverlay<HighlightRule[]>(K.highlights, []);
  const base = await baked<HighlightRule[]>('highlights.json', []);
  return base;
}

function saveHighlights(rules: HighlightRule[]): void {
  writeOverlay(K.highlights, rules);
  writeOverlay(K.highlightsSeeded, true);
}

// ── the client ───────────────────────────────────────────────────────────────
const impl: ApiClient = {
  health: () => once('health.json', () => baked<Health>('health.json', { ok: true })),
  models: () => once('models.json', () => baked<Run[]>('models.json', [])),
  refreshModels: async () => ({ status: 'static', count: 0 }),
  // Both catalogs are baked ∪ overlay: an installed pack contributes the LABELS its
  // panels' ckpt:/base:/openrouter: refs resolve through (see staticInstallModels).
  openrouterModels: async () => {
    const base = await once('openrouter-models.json', () =>
      baked<OpenRouterModel[]>('openrouter-models.json', [])
    );
    const extra = readOverlay<OpenRouterModel[]>(K.orModels, []);
    const seen = new Set(base.map((m) => m.openrouter_model));
    return [...base, ...extra.filter((m) => !seen.has(m.openrouter_model))];
  },
  tinkerModels: async () => {
    const base = await once('tinker-models.json', () =>
      baked<TinkerModelsResponse>('tinker-models.json', {
        available: false,
        error: 'static site — no Tinker catalog',
        models: []
      })
    );
    const extra = readOverlay<TinkerModel[]>(K.tinkerModels, []);
    const seen = new Set(base.models.map((m) => m.id));
    return { ...base, models: [...base.models, ...extra.filter((m) => !seen.has(m.id))] };
  },
  openrouterAvailable: async (): Promise<OpenRouterAvailableResponse> => ({
    available: false,
    error: 'static site — no OpenRouter catalog',
    models: []
  }),
  addOpenrouterModel: async () => {
    throw new Error('read-only site: cannot add models');
  },
  removeOpenrouterModel: async () => {
    throw new Error('read-only site: cannot remove models');
  },
  close: async () => ({ status: 'static' }),

  getState: async () => structuredClone(await ensureState()),
  setState: async (patch: StatePatch) => {
    const s = await ensureState();
    applyPatch(s, patch);
    return structuredClone(s);
  },

  getPrefs: async () => {
    const base = await once('prefs.json', () => baked<Record<string, string>>('prefs.json', {}));
    return { ...base, ...readOverlay<Record<string, string>>(K.prefs, {}) };
  },
  setPref: async (key: string, value: string) => {
    writeOverlay(K.prefs, { ...readOverlay<Record<string, string>>(K.prefs, {}), [key]: value });
    return { status: 'ok' };
  },

  loadDataset: async () => ({
    records: [],
    total: 0,
    error: 'dataset loading needs a backend (this is a static site)'
  }),

  listHighlights: () => highlights(),
  upsertHighlight: async (id: string, rule: HighlightRule) => {
    const rules = await highlights();
    const next = rules.filter((r) => r.id !== id);
    const stored = { ...rule, id };
    next.push(stored);
    next.sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
    saveHighlights(next);
    return stored;
  },
  deleteHighlight: async (id: string) => {
    saveHighlights((await highlights()).filter((r) => r.id !== id));
    return { status: 'ok' };
  },
  reorderHighlights: async (ids: string[]) => {
    const rules = await highlights();
    const byId = new Map(rules.map((r) => [r.id, r]));
    const ordered = [
      ...ids.map((id) => byId.get(id)).filter((r): r is HighlightRule => r != null),
      ...rules.filter((r) => !ids.includes(r.id))
    ].map((r, i) => ({ ...r, sort_order: i }));
    saveHighlights(ordered);
    return { status: 'ok', n: ordered.length };
  },

  listPins: async () => {
    const base = await baked<Record<string, unknown>[]>('pins.json', []);
    return [...base, ...readOverlay<Record<string, unknown>[]>(K.pins, [])];
  },
  createPin: async (entry: Record<string, unknown>) => {
    const pin = { ...entry, id: crypto.randomUUID(), created_at: nowIso() };
    writeOverlay(K.pins, [...readOverlay<Record<string, unknown>[]>(K.pins, []), pin]);
    return pin;
  },
  deletePin: async (id: string) => {
    const local = readOverlay<Record<string, unknown>[]>(K.pins, []);
    const next = local.filter((p) => p.id !== id);
    if (next.length === local.length) throw new Error('read-only site: cannot delete a shipped pin');
    writeOverlay(K.pins, next);
    return { status: 'ok' };
  },

  listWorkspaces: async () => {
    const base = await bakedIndex();
    const overlay = createdIds()
      .map((id) => readOverlay<WorkspaceSummary | null>(K.summary(id), null))
      .filter((s): s is WorkspaceSummary => s != null);
    const overlayById = new Map(overlay.map((s) => [s.id, s]));
    return [...base.map((s) => overlayById.get(s.id) ?? s), ...overlay.filter((s) => !base.some((b) => b.id === s.id))];
  },
  getWorkspace: async (id: string) =>
    overlayBody(id) ?? (await bakedStrict<Workspace>(`workspaces/${encodeURIComponent(id)}.json`)),
  fetchNodeBlobs: async (id: string, nodes: string[]) => {
    const local = readOverlay<Record<string, NodeBlobs>>(K.blobs(id), {});
    const out: Record<string, NodeBlobs> = {};
    await Promise.all(
      nodes.map(async (nid) => {
        if (local[nid]) {
          out[nid] = local[nid];
          return;
        }
        const blob = await baked<NodeBlobs | null>(
          `workspaces/${encodeURIComponent(id)}.blobs/${encodeURIComponent(nid)}.json`,
          null
        );
        if (blob) out[nid] = blob;
      })
    );
    return out;
  },
  createWorkspace: async (entry) => {
    const id = entry.id ?? `ws-${crypto.randomUUID().slice(0, 8)}`;
    return installWorkspace({ ...entry, id, name: entry.name ?? 'Untitled' });
  },
  patchWorkspace: async (id: string, patch) => {
    const body = overlayBody(id);
    // Baked workspace: accepted, not persisted (see the module header).
    if (!body) return (await staticApi.listWorkspaces()).find((s) => s.id === id) ?? { id, name: id, created_at: nowIso(), updated_at: nowIso() };
    const next: Workspace = { ...body, ...(patch as Partial<Workspace>), updated_at: nowIso() };
    saveOverlayBody(next);
    return summaryOf(next);
  },
  saveWorkspaceTree: async (id: string, body) => {
    const cur = overlayBody(id);
    if (!cur) return { status: 'static-readonly', id }; // baked: dropped, see module header
    const [light, blobs] = splitTrees(body.trees ?? {});
    const trees = { ...cur.trees, ...light };
    for (const pid of body.dropped_trees ?? []) delete trees[pid];
    const { trees: _t, dropped_trees: _d, ...fields } = body as any;
    saveOverlayBody({ ...cur, ...fields, trees, updated_at: nowIso() });
    const existing = readOverlay<Record<string, NodeBlobs>>(K.blobs(id), {});
    writeOverlay(K.blobs(id), { ...blobs, ...existing });
    return { status: 'ok', id };
  },
  deleteWorkspace: async (id: string) => {
    if (!isOverlay(id)) throw new Error('read-only site: cannot delete a shipped workspace');
    dropOverlay(K.body(id));
    dropOverlay(K.summary(id));
    dropOverlay(K.blobs(id));
    writeOverlay(K.createdIds, createdIds().filter((x) => x !== id));
    return { status: 'ok' };
  },

  // Pack install on a static site is done CLIENT-side (lib/pack-install.ts branches
  // on isStatic before it would reach this), so arriving here is a routing bug.
  applyPack: async () => {
    throw new Error('static site: packs are installed client-side, not via /api/pack/apply');
  },
  chat: async () => {
    throw new Error('sampling needs a backend — this is a read-only static site');
  },
  cancelChat: async (chat_id: number) => ({ status: 'not_found', chat_id })
};

// The overlay map is hydrated from IndexedDB asynchronously, but every read above is
// synchronous — so the FIRST call into any method must wait for that hydrate, or a
// visitor's installed workspaces are invisible until something happens to re-read.
// Gating here, once, beats an `await overlayReady` at the top of ~30 methods: that
// list only has to be wrong once, and the failure (missing workspaces on a cold load)
// is a race that would not reproduce locally.
function gated(target: ApiClient): ApiClient {
  const out: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(target)) {
    if (typeof value !== 'function') {
      out[name] = value;
      continue;
    }
    out[name] = (...args: unknown[]) =>
      overlayReady.then(() => (value as (...a: unknown[]) => unknown).apply(target, args));
  }
  return out as ApiClient;
}

export const staticApi: ApiClient = gated(impl);

/** Install a pack's workspace under `id`, replacing whatever is there. Used by the
 *  `?w=<pack-url>` loader (lib/pack-install.ts) — the static twin of the server's
 *  POST /api/pack/apply. */
export function staticInstallWorkspace(entry: Parameters<typeof installWorkspace>[0]): Workspace {
  return installWorkspace(entry);
}

/** True when `id` names a workspace the visitor installed locally (deletable, and
 *  its edits persist), as opposed to one baked into the site. */
export function staticIsOverlay(id: string): boolean {
  return isOverlay(id);
}

/** Merge an installed pack's models into the overlay catalogs, so its panels'
 *  `ckpt:`/`base:`/`openrouter:` refs resolve to labels instead of raw URIs. */
export function staticInstallModels(models: {
  tinker: TinkerModel[];
  openrouter: OpenRouterModel[];
}): void {
  if (models.tinker.length) {
    const cur = readOverlay<TinkerModel[]>(K.tinkerModels, []);
    const seen = new Set(cur.map((m) => m.id));
    writeOverlay(K.tinkerModels, [...cur, ...models.tinker.filter((m) => !seen.has(m.id))]);
  }
  if (models.openrouter.length) {
    const cur = readOverlay<OpenRouterModel[]>(K.orModels, []);
    const seen = new Set(cur.map((m) => m.openrouter_model));
    writeOverlay(K.orModels, [
      ...cur,
      ...models.openrouter.filter((m) => !seen.has(m.openrouter_model))
    ]);
  }
}

/** The static stand-in for the state SSE: one synthetic snapshot so `live` seeds
 *  through its normal path (and reports connected), then silence. */
export function staticSse(onEvent: (event: string, data: any) => void): () => void {
  let live = true;
  void ensureState().then((s) => {
    if (live) onEvent('snapshot', { state: structuredClone(s) });
  });
  return () => {
    live = false;
  };
}
