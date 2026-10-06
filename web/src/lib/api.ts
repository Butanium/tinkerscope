// Typed client for the tinkerscope backend + a named-event SSE helper.
// Mirrors ~/tools/samplescope/web/src/lib/api.ts (sse()) but for this API.

import type {
  Run,
  OpenRouterModel,
  TinkerModelsResponse,
  TinkerProbe,
  OpenRouterAvailableResponse,
  VllmModelsResponse,
  Health,
  PlaygroundState,
  StatePatch,
  ChatRequest,
  Workspace,
  WorkspaceSummary,
  NodeBlobs,
  HighlightRule,
  PanelLayout,
  SearchResponse
} from './types';
import type { ConvTree } from './tree';
import type { ConvFields, WorkspaceOp, OpsResponse } from './types';
import { isStatic } from './static-mode';
import { staticApi, staticSse } from './api-static';
import { sessionId, SESSION_HEADER, SESSION_QUERY } from './session';

/** An HTTP error with its status attached — the ops emitter's retry policy
 *  branches on it (5xx = maybe-never-arrived, retry; 4xx = rejected, refetch). */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// Every request names this browser's SESSION (lib/session.ts) — on a
// `--multi-user` server that is whose sidebar the call reads/writes; a
// single-user server ignores it.
async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: {
      'content-type': 'application/json',
      [SESSION_HEADER]: sessionId(),
      ...(init?.headers || {})
    }
  });
  if (!r.ok) throw new ApiError(r.status, `${r.status} ${r.statusText}: ${await r.text()}`);
  return r.json() as Promise<T>;
}

const httpApi = {
  health: () => j<Health>('/api/health'),
  models: () => j<Run[]>('/api/models'),
  refreshModels: () => j<{ status: string; count: number }>('/api/models/refresh', { method: 'POST' }),
  openrouterModels: () => j<OpenRouterModel[]>('/api/openrouter-models'),
  // Typeahead catalog sources (not the saved quick-list).
  tinkerModels: (refresh = false) =>
    j<TinkerModelsResponse>(`/api/tinker-models${refresh ? '?refresh=1' : ''}`),
  probeTinkerModel: (sampler_path: string) =>
    j<TinkerProbe>(`/api/tinker-models/probe?sampler_path=${encodeURIComponent(sampler_path)}`),
  nameTinkerModel: (kind: 'ckpt' | 'base', ref: string, label: string) =>
    j<{ status: string; label?: string; error?: string }>('/api/tinker-models/name', {
      method: 'POST',
      body: JSON.stringify({ kind, ref, label })
    }),
  openrouterAvailable: (refresh = false) =>
    j<OpenRouterAvailableResponse>(
      `/api/openrouter-models/available${refresh ? '?refresh' : ''}`
    ),
  // The configured vLLM server's served models (empty + available:false when none).
  vllmModels: (refresh = false) =>
    j<VllmModelsResponse>(`/api/vllm-models${refresh ? '?refresh=1' : ''}`),
  addOpenrouterModel: (openrouter_model: string, label?: string) =>
    j<OpenRouterModel[]>('/api/openrouter-models', {
      method: 'POST',
      body: JSON.stringify({ openrouter_model, ...(label ? { label } : {}) })
    }),
  removeOpenrouterModel: (model: string) =>
    j<OpenRouterModel[]>(`/api/openrouter-models?model=${encodeURIComponent(model)}`, {
      method: 'DELETE'
    }),
  close: () => j<{ status: string }>('/api/close', { method: 'POST' }),
  // shared state
  getState: () => j<PlaygroundState>('/api/state'),
  setState: (patch: StatePatch) =>
    j<PlaygroundState>('/api/state', { method: 'POST', body: JSON.stringify(patch) }),
  // per-scan-root UI prefs (key/value on disk; survives restarts)
  getPrefs: () => j<Record<string, string>>('/api/prefs'),
  setPref: (key: string, value: string) =>
    j<{ status: string }>(`/api/prefs/${encodeURIComponent(key)}`, {
      method: 'PUT',
      body: JSON.stringify({ value })
    }),
  // samplescope hand-off (a run's training dataset → the local dataset viewer)
  openInSamplescope: (runId: string) =>
    j<{ url: string; started: boolean; base_url: string }>('/api/samplescope/open', {
      method: 'POST',
      body: JSON.stringify({ run_id: runId })
    }),
  // highlight rules (render-time text coloring; server seeds defaults)
  listHighlights: () => j<HighlightRule[]>('/api/highlights'),
  upsertHighlight: (id: string, rule: HighlightRule) =>
    j<HighlightRule>(`/api/highlights/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(rule)
    }),
  deleteHighlight: (id: string) =>
    j<{ status: string }>(`/api/highlights/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  reorderHighlights: (ids: string[]) =>
    j<{ status: string; n: number }>('/api/highlights/reorder', {
      method: 'POST',
      body: JSON.stringify({ ids })
    }),
  // pins — saved samples (was "highlights"; server adds id/created_at)
  listPins: () => j<Record<string, unknown>[]>('/api/pins'),
  createPin: (entry: Record<string, unknown>) =>
    j<Record<string, unknown>>('/api/pins', { method: 'POST', body: JSON.stringify(entry) }),
  deletePin: (id: string) =>
    j<{ status: string }>(`/api/pins/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  // workspaces (branchable trees; server adds id/created_at/updated_at).
  // Storage v2 + ops: the LIST is summaries only; a workspace's light body
  // (trees incl., per-node blobs excl.) is fetched per-id; heavy per-node blobs
  // are batch-fetched; ALL tree mutation is applyOps; PATCH carries any
  // layout-only change with zero tree bytes. See docs/API_CONTRACT.md.
  listWorkspaces: () => j<WorkspaceSummary[]>('/api/workspaces'),
  getWorkspace: (id: string) =>
    j<Workspace>(`/api/workspaces/${encodeURIComponent(id)}`),
  fetchNodeBlobs: (id: string, nodes: string[]) =>
    j<Record<string, NodeBlobs>>(`/api/workspaces/${encodeURIComponent(id)}/node-blobs`, {
      method: 'POST',
      body: JSON.stringify({ nodes })
    }),
  createWorkspace: (entry: {
    id?: string;
    name?: string;
    system_prompt?: string | null;
    system_enabled?: boolean | null;
    trees?: Record<string, ConvTree>;
    panels?: PanelLayout[];
    reduced_panels?: string[];
    send_targets?: string[];
    seen_panels?: string[];
  }) => j<Workspace>('/api/workspaces', { method: 'POST', body: JSON.stringify(entry) }),
  patchWorkspace: (id: string, patch: Partial<ConvFields> & { name?: string }) =>
    j<WorkspaceSummary>(`/api/workspaces/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(patch)
    }),
  // The ops protocol: one atomic batch of small idempotent mutations. 409 = some
  // op was structurally invalid (nothing applied — refetch); 5xx/transport = the
  // batch may never have arrived (idempotent replay is safe — retry).
  applyOps: (id: string, ops: WorkspaceOp[]) =>
    j<OpsResponse>(`/api/workspaces/${encodeURIComponent(id)}/ops`, {
      method: 'POST',
      body: JSON.stringify({ ops })
    }),
  deleteWorkspace: (id: string) =>
    j<{ status: string }>(`/api/workspaces/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  // Cross-workspace text search (the Ctrl+K palette). Case-insensitive substring;
  // regex/case knobs exist on the endpoint but only `tinkpg grep` uses them.
  // `scopes` = which hit kinds to search (reply/user/thinking/system/name/model);
  // omitted = all. Sent server-side so counts + truncation reflect the filter.
  search: (
    q: string,
    opts: { ws?: string; maxHits?: number; width?: number; scopes?: string[] } = {}
  ) => {
    const p = new URLSearchParams({ q });
    if (opts.ws) p.set('ws', opts.ws);
    if (opts.maxHits) p.set('max_hits', String(opts.maxHits));
    if (opts.width) p.set('width', String(opts.width));
    if (opts.scopes) p.set('scopes', opts.scopes.join(','));
    return j<SearchResponse>(`/api/search?${p}`);
  },
  // Share packs installed at runtime (the `?w=<path-or-url>` link). Omitting
  // `on_conflict` is a dry-run preview: which ids it would land on, which exist.
  // See api/routes/packs.py + lib/pack-install.ts.
  applyPack: (body: { source: string; on_conflict?: 'overwrite' | 'new'; force?: boolean }) =>
    j<Record<string, unknown>>('/api/pack/apply', { method: 'POST', body: JSON.stringify(body) }),
  // chat (returns the raw Response so the caller can read the SSE stream directly)
  chat: (req: ChatRequest, signal?: AbortSignal) =>
    fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', [SESSION_HEADER]: sessionId() },
      body: JSON.stringify(req),
      signal
    }),
  // Cancel an in-flight chat by id — how Stop-all reaches a chat this tab doesn't
  // own (CLI / another tab, so no local AbortController). Server fires the same
  // terminal a client disconnect would. not_found if it already ended.
  cancelChat: (chat_id: number) =>
    j<{ status: string; chat_id: number }>(`/api/chat/${chat_id}/cancel`, { method: 'POST' })
};

/** The transport contract. `api-static.ts` implements it against baked JSON, so
 *  every consumer stays transport-blind — the HTTP object above is the definition. */
export type ApiClient = typeof httpApi;

/** The backend for this bundle: HTTP against a live instance, or the baked-file
 *  client when running as an exported static site (see lib/static-mode.ts). */
export const api: ApiClient = isStatic ? staticApi : httpApi;

/**
 * Open a named-event SSE connection. Returns an unsubscribe function.
 * Each delivered event is `(eventName, parsedData)` — data is JSON-parsed when possible.
 * EventSource only fires named events for which we addEventListener, so every
 * event name the server emits must be listed here (see docs/API_CONTRACT.md §events).
 */
export function sse(
  path: string,
  onEvent: (event: string, data: any) => void,
  onError?: (e: Event, es: EventSource) => void
): () => void {
  // Static site: no bus to subscribe to — one synthetic snapshot, then quiet.
  if (isStatic) return staticSse(onEvent);
  // EventSource can't set headers, so the session rides as a query param here.
  const url = new URL(path, window.location.origin);
  url.searchParams.set(SESSION_QUERY, sessionId());
  const es = new EventSource(url.pathname + url.search);
  const handler = (event: string) => (e: MessageEvent) => {
    let parsed: any = e.data;
    try {
      parsed = JSON.parse(e.data);
    } catch {
      /* leave as raw string */
    }
    onEvent(event, parsed);
  };
  for (const evt of [
    'snapshot',
    'patch',
    'ops',
    'workspace_deleted',
    'chat_start',
    'chat_status',
    'delta',
    'sample',
    'chat_done',
    'chat_error',
    'ping'
  ]) {
    es.addEventListener(evt, handler(evt) as EventListener);
  }
  es.onerror = (e) => onError?.(e, es);
  return () => es.close();
}
