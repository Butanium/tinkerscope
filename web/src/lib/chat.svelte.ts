// Generation fire lifecycle: turn a "fire this for panel X" request into a
// POST /api/chat, drain the stream, fold the samples under the user node, and own
// the per-panel abort controller + the live bucket's prefill color.
//
// Deliberately UI-agnostic: the caller (+page) resolves the model and assembles the
// sampling params into a bundle, so this store never reaches into the sidebar's
// param state. `abortByPanel` is for stopGeneration ONLY — the per-panel "busy"
// gate keys off live.panels[panel].running, not this (see panelBusy in +page).

import { live } from './state.svelte';
import { conversations as convo } from './conversations.svelte';
import { api } from './api';
import { drainSamples } from './chat-stream';
import { foldAssistant } from './tree';
import type { Panel, ChatMessage, ChatRequest } from './types';

/** Sampling params for one fire, assembled by the caller from shared state + the
 *  sidebar's advanced-params popup (which this store deliberately doesn't own). */
export type ChatParams = Pick<
	ChatRequest,
	| 'system_prompt'
	| 'temperature'
	| 'max_tokens'
	| 'n_samples'
	| 'thinking'
	| 'prefill_thinking_only'
	| 'top_p'
	| 'top_k'
	| 'presence_penalty'
	| 'repetition_penalty'
>;

/** The resolved model selection — exactly one variant, mirroring /api/chat's
 *  mutually-exclusive id fields. The caller resolves it (it owns the runs list). */
export type ChatModelField =
	| { openrouter_model: string }
	| { base_model: string }
	| { sampler_path: string }
	| { run_id: string; checkpoint: string | null };

class ChatStore {
	/** Per-panel in-flight abort controllers, keyed by panel so concurrent
	 *  generations on different panels don't clobber each other's handle. $state so
	 *  stopGeneration's readers react. NOT a busy signal — see panelBusy in +page. */
	abortByPanel = $state<Partial<Record<Panel, AbortController | null>>>({});
	/** Per-panel prefill of the last/in-flight fire — lets the live bucket color its
	 *  prefilled prefix (committed nodes carry their own `prefill`). '' ⇒ none. */
	firePrefill = $state<Partial<Record<Panel, string>>>({});

	/** Reset a panel's live sample bucket. Per-key write — live.panels is deeply
	 *  reactive, so only THIS panel's readers invalidate (see the panels field doc
	 *  in state.svelte.ts; do NOT reassign the whole map). */
	clearPanelBucket(panel: Panel) {
		live.panels[panel] = { chat_id: null, label: '', n: 0, samples: [], running: false, error: null };
	}

	/** Fire one panel's generation with a fresh per-panel abort controller.
	 *  `prefill` (continuation): the samples come back as the continuation only, so
	 *  it's prepended to each in the fold to form the full continued message. */
	fireOne(
		panel: Panel,
		model: ChatModelField,
		userParentId: string,
		messages: ChatMessage[],
		params: ChatParams,
		prefill: string | undefined,
		onError: (msg: string) => void
	) {
		this.firePrefill[panel] = prefill ?? ''; // so the live bucket colors its prefilled prefix
		const ac = new AbortController();
		this.abortByPanel[panel] = ac;
		this.#fireChat(panel, model, userParentId, messages, params, ac.signal, prefill, onError).finally(() => {
			if (this.abortByPanel[panel] === ac) this.abortByPanel[panel] = null;
		});
	}

	async #fireChat(
		panel: Panel,
		model: ChatModelField,
		userParentId: string,
		messages: ChatMessage[],
		params: ChatParams,
		signal: AbortSignal,
		prefill: string | undefined,
		onError: (msg: string) => void
	) {
		const token = convo.newToken(); // marks this chat OURS so the external-fold hook skips it
		try {
			const res = await api.chat(
				{ ...model, messages, ...params, panel, broadcast: true, client_token: token },
				signal
			);
			if (!res.ok) {
				onError(`Chat error ${res.status}: ${await res.text()}`);
				return;
			}
			// Collect our samples from our OWN stream + fold them under the user node.
			// For a continuation (prefill set), each sample is the CONTINUATION only,
			// so prepend the prefill to form the full extended message — UNLESS the
			// backend already folded it in (`prefill_incorporated`, the native tinker
			// path, which also splits prefilled <think> into `reasoning`).
			const samples = await drainSamples(res);
			if (samples.length) {
				// prefill_thinking_only + thinking='both': the backend stripped the
				// prefill from the non-thinking half (tagged sm.thinking === false),
				// so those samples must not get it prepended (nor carry `prefill`).
				const skipPrefill = (sm: (typeof samples)[number]) =>
					!!params.prefill_thinking_only && sm.thinking === false;
				const folded = prefill
					? samples.map((sm) =>
							sm.error || skipPrefill(sm)
								? sm
								: { ...sm, prefill, content: sm.prefill_incorporated ? sm.content : prefill + (sm.content ?? '') }
						)
					: samples;
				const { tree } = foldAssistant(convo.treeFor(panel), userParentId, folded);
				convo.setTree(panel, tree);
			}
		} catch (err: any) {
			// Abort (user hit Stop) → leave the user node reply-less; no partial fold.
			if (err?.name !== 'AbortError') onError(`Connection error: ${err?.message ?? err}`);
		} finally {
			convo.endToken(token);
		}
	}

	/** Stop one panel if given, else all in-flight panels. */
	stopGeneration(panel?: Panel) {
		const panels = panel ? [panel] : (Object.keys(this.abortByPanel) as Panel[]);
		for (const k of panels) {
			this.abortByPanel[k]?.abort();
			this.abortByPanel[k] = null;
		}
	}
}

export const chat = new ChatStore();
