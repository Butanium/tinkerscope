// Single source of frontend truth, fed by the /api/state/events SSE.
//
// Two concerns live here:
//   1. `live.state` — the mirrored shared PlaygroundState (selection / params /
//      conversation). Rendered directly so the browser follows when the terminal
//      (or another browser tab) POSTs /api/state.
//   2. `live.panels` — accumulated streamed samples per panel, keyed by chat_id.
//      'chat_start' clears the bucket + sets running; 'sample' appends;
//      'chat_done'/'chat_error' end it. The sample list + distribution chart
//      render from these, so CLI-initiated and browser-initiated chats share one
//      render path.

import { sse } from './api';
import type { PlaygroundState, SampleData, Panel } from './types';

/** Live accumulation for one compare-panel's in-flight / finished chat run. */
export type PanelRun = {
	chat_id: number | null;
	label: string; // run@checkpoint, for the chart x-axis
	n: number; // expected number of samples
	samples: SampleData[]; // sparse: indexed by sample_index
	running: boolean;
	error: string | null;
};

function emptyPanel(): PanelRun {
	return { chat_id: null, label: '', n: 0, samples: [], running: false, error: null };
}

class LiveStore {
	/** Mirrored shared server state. null until the first snapshot arrives. */
	state = $state<PlaygroundState | null>(null);
	/** true once the SSE stream has delivered at least one event. */
	connected = $state(false);
	/** Per-panel sample accumulation, driven by chat_start/sample/chat_done. */
	panels = $state<Record<Panel, PanelRun>>({
		primary: emptyPanel(),
		compare: emptyPanel()
	});

	// Lifecycle hooks (set by the page) for the branching tree's fold bookkeeping.
	// CRUCIAL: these fire on the RAW bus event, decoupled from the render bucket's
	// single-slot straggler guard — so an own chat_done is never eaten when a
	// foreign chat_start (CLI / another tab) has clobbered the panel bucket. Each
	// receives the event payload (carries chat_id, panel, client_token).
	onChatStart: ((panel: Panel, data: any) => void) | null = null;
	onChatDone: ((panel: Panel, data: any) => void) | null = null;
	onChatError: ((panel: Panel, data: any) => void) | null = null;

	#stop: (() => void) | null = null;

	/** Open the global SSE state stream once. Idempotent. */
	start(): void {
		if (this.#stop) return;
		this.#stop = sse('/api/state/events', (event, data) => this.#onEvent(event, data));
	}

	stop(): void {
		this.#stop?.();
		this.#stop = null;
	}

	/** Drop both panel buckets — used when switching conversations so a stale
	 *  overlay from the previous one can't bleed onto the new active path. */
	clearBuckets(): void {
		this.panels = { primary: emptyPanel(), compare: emptyPanel() };
	}

	/** True while any panel has an in-flight chat. */
	get anyRunning(): boolean {
		return this.panels.primary.running || this.panels.compare.running;
	}

	#onEvent(event: string, data: any): void {
		this.connected = true;
		switch (event) {
			case 'snapshot':
			case 'patch':
				if (data?.state) this.state = data.state as PlaygroundState;
				break;
			case 'chat_start': {
				const panel = (data?.panel ?? 'primary') as Panel;
				this.panels[panel] = {
					chat_id: data.chat_id ?? null,
					label: data.label ?? '',
					n: data.n ?? 0,
					samples: [],
					running: true,
					error: null
				};
				// reassign to trigger reactivity on the record
				this.panels = { ...this.panels };
				this.onChatStart?.(panel, data);
				break;
			}
			case 'delta': {
				// Token-streaming chunk (n==1 only). Accumulate into the sample slot
				// at sample_index so the panel fills token-by-token; the later
				// 'sample' event then finalizes the slot (parseSample replaces this
				// partial with the cleaned authoritative content).
				const panel = (data?.panel ?? 'primary') as Panel;
				const cur = this.panels[panel];
				// Ignore stragglers from an older chat run.
				if (cur.chat_id != null && data.chat_id != null && data.chat_id !== cur.chat_id) break;
				const idx = data.sample_index ?? 0;
				const text: string = data.delta ?? '';
				const samples = cur.samples.slice();
				const prev = samples[idx] ?? { content: '' };
				samples[idx] =
					data.kind === 'reasoning'
						? { ...prev, reasoning: (prev.reasoning ?? '') + text }
						: { ...prev, content: (prev.content ?? '') + text };
				this.panels[panel] = { ...cur, samples };
				this.panels = { ...this.panels };
				break;
			}
			case 'sample': {
				const panel = (data?.panel ?? 'primary') as Panel;
				const cur = this.panels[panel];
				// Ignore stragglers from an older chat run.
				if (cur.chat_id != null && data.chat_id != null && data.chat_id !== cur.chat_id) break;
				const samples = cur.samples.slice();
				samples[data.sample_index ?? samples.length] = parseSample(data);
				this.panels[panel] = { ...cur, samples };
				this.panels = { ...this.panels };
				break;
			}
			case 'chat_done': {
				const panel = (data?.panel ?? 'primary') as Panel;
				// Fire the fold hook FIRST, unconditionally — it must see every
				// chat_done even if the render bucket was clobbered by a foreign
				// chat_start (the straggler guard below only protects rendering).
				this.onChatDone?.(panel, data);
				const cur = this.panels[panel];
				if (cur.chat_id != null && data.chat_id != null && data.chat_id !== cur.chat_id) break;
				this.panels[panel] = { ...cur, running: false };
				this.panels = { ...this.panels };
				break;
			}
			case 'chat_error': {
				const panel = (data?.panel ?? 'primary') as Panel;
				this.onChatError?.(panel, data);
				const cur = this.panels[panel];
				// chat_id may be null for PRE-START failures (unknown/unsampleable
				// run, bad checkpoint) — those broadcast before any chat_start, so
				// there's no id to match; always surface them on the named panel.
				// For a non-null id, drop stragglers from an older run.
				if (data?.chat_id != null && cur.chat_id != null && data.chat_id !== cur.chat_id) break;
				this.panels[panel] = { ...cur, running: false, error: data?.error ?? 'chat failed' };
				this.panels = { ...this.panels };
				break;
			}
			// 'ping' — heartbeat, ignore.
		}
	}
}

/**
 * Normalize a `sample`/`message` SSE payload into a SampleData.
 * `content` is normally a string (tinker), but may be an array of content blocks
 * (`[{type:"thinking"|"text", ...}]`) for OpenRouter reference models — handle both.
 */
export function parseSample(data: any): SampleData {
	if (data?.error) return { content: `Error: ${data.error}`, error: data.error };
	let content = '';
	let reasoning: string = data?.reasoning || '';
	if (typeof data?.content === 'string') {
		content = data.content;
	} else if (Array.isArray(data?.content)) {
		for (const block of data.content) {
			if (block?.type === 'text') content += (content ? '\n\n' : '') + (block.text || '');
			else if (block?.type === 'thinking')
				reasoning += (reasoning ? '\n\n' : '') + (block.thinking || '');
		}
	}
	return {
		content: content || (reasoning ? '[truncated during thinking]' : ''),
		reasoning: reasoning || undefined,
		raw_text: data?.raw_text || undefined,
		finish_reason: data?.finish_reason || undefined
	};
}

export const live = new LiveStore();
