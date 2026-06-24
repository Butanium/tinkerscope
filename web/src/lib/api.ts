// Typed client for the tinkerscope backend + a named-event SSE helper.
// Mirrors ~/tools/samplescope/web/src/lib/api.ts (sse()) but for this API.

import type {
	Run,
	OpenRouterModel,
	TinkerModelsResponse,
	OpenRouterAvailableResponse,
	Health,
	PlaygroundState,
	StatePatch,
	ChatRequest,
	Conversation,
	HighlightRule
} from './types';
import type { ConvTree } from './tree';

async function j<T>(path: string, init?: RequestInit): Promise<T> {
	const r = await fetch(path, {
		...init,
		headers: { 'content-type': 'application/json', ...(init?.headers || {}) }
	});
	if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`);
	return r.json() as Promise<T>;
}

export const api = {
	health: () => j<Health>('/api/health'),
	models: () => j<Run[]>('/api/models'),
	refreshModels: () => j<{ status: string; count: number }>('/api/models/refresh', { method: 'POST' }),
	openrouterModels: () => j<OpenRouterModel[]>('/api/openrouter-models'),
	// Typeahead catalog sources (not the saved quick-list).
	tinkerModels: () => j<TinkerModelsResponse>('/api/tinker-models'),
	openrouterAvailable: (refresh = false) =>
		j<OpenRouterAvailableResponse>(
			`/api/openrouter-models/available${refresh ? '?refresh' : ''}`
		),
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
	// datasets
	loadDataset: (path: string, count = 10, seed?: number) =>
		j<{ records: Record<string, unknown>[]; total: number; error?: string }>('/api/load-dataset', {
			method: 'POST',
			body: JSON.stringify({ path, count, ...(seed != null ? { seed } : {}) })
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
	// conversations (branchable trees; server adds id/created_at/updated_at)
	listConversations: () => j<Conversation[]>('/api/conversations'),
	createConversation: (entry: {
		name?: string;
		system_prompt?: string | null;
		trees?: Record<string, ConvTree>;
	}) => j<Conversation>('/api/conversations', { method: 'POST', body: JSON.stringify(entry) }),
	renameConversation: (id: string, name: string) =>
		j<Conversation>(`/api/conversations/${encodeURIComponent(id)}`, {
			method: 'PATCH',
			body: JSON.stringify({ name })
		}),
	saveConversationTree: (
		id: string,
		trees: Record<string, ConvTree>,
		system_prompt: string | null
	) =>
		j<{ status: string; id: string }>(`/api/conversations/${encodeURIComponent(id)}/tree`, {
			method: 'PUT',
			body: JSON.stringify({ trees, system_prompt })
		}),
	deleteConversation: (id: string) =>
		j<{ status: string }>(`/api/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' }),
	// chat (returns the raw Response so the caller can read the SSE stream directly)
	chat: (req: ChatRequest, signal?: AbortSignal) =>
		fetch('/api/chat', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(req),
			signal
		})
};

/**
 * Open a named-event SSE connection. Returns an unsubscribe function.
 * Each delivered event is `(eventName, parsedData)` — data is JSON-parsed when possible.
 * EventSource only fires named events for which we addEventListener, so every
 * event name the server emits must be listed here (see API_CONTRACT.md §events).
 */
export function sse(
	path: string,
	onEvent: (event: string, data: any) => void,
	onError?: (e: Event, es: EventSource) => void
): () => void {
	const es = new EventSource(path);
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
		'chat_start',
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
