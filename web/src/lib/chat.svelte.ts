// Generation fire lifecycle: turn a "fire this for panel X" request into a
// DETACHED POST /api/chat, then let the state bus drive rendering + the fold.
//
// Detached (fire-and-forget): the POST returns immediately and the generation
// streams ONLY to /api/state/events, so the browser never holds the chat's
// connection — that's what lets N panels generate at once (a held stream per
// panel would exhaust the browser's ~6 per-host HTTP/1.1 connections; see the
// connection-starvation diagnosis). The panel renders from the bus bucket
// exactly like a tinkpg-driven chat, and its reply is folded into the tree on
// the bus `chat_done`, from the bucket's accumulated samples (all n — the
// server-committed transcript echo carries only ONE representative, which would
// collapse the n>1 distribution the chart + ‹k/N› cycler read from tree
// siblings).
//
// Deliberately UI-agnostic: the caller (+page) resolves the model and assembles
// the sampling params into a bundle, so this store never reaches into the
// sidebar's param state.

import { live } from './state.svelte';
import { conversations as convo } from './conversations.svelte';
import { nodeBlobs } from './node-blobs.svelte';
import { api } from './api';
import { foldAssistant, type SampleLike } from './tree';
import type { Panel, ChatMessage, ChatRequest, SampleData } from './types';

/** Sampling params for one fire, assembled by the caller from shared state + the
 *  sidebar's advanced-params popup (which this store deliberately doesn't own). */
export type ChatParams = Pick<
	ChatRequest,
	| 'system_prompt'
	| 'temperature'
	| 'max_tokens'
	| 'n_samples'
	| 'thinking'
	| 'prefill_scope'
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

/** What a fired-but-not-yet-terminal detached chat needs to fold itself when its
 *  bus `chat_done` lands: which user node its samples attach under, and the
 *  prefill context to reproduce the drain-path fold (prepend / scope-skip). */
type FireContext = {
	panel: Panel;
	userParentId: string;
	prefill?: string;
	scope: ChatParams['prefill_scope'];
	thinking: ChatParams['thinking'];
};

class ChatStore {
	/** Per-panel prefill of the last/in-flight fire — lets the live bucket color its
	 *  prefilled prefix (committed nodes carry their own `prefill`). '' ⇒ none. */
	firePrefill = $state<Partial<Record<Panel, string>>>({});

	/** In-flight detached chats we own, keyed by client_token: the fold context to
	 *  apply when the bus `chat_done`/`chat_error` for that token lands. Plain Map —
	 *  not reactive (no UI reads it); `convo.busy` (its token set) drives the gates. */
	#ownFires = new Map<string, FireContext>();

	/** Reset a panel's live sample bucket. Per-key write — live.panels is deeply
	 *  reactive, so only THIS panel's readers invalidate (see the panels field doc
	 *  in state.svelte.ts; do NOT reassign the whole map). */
	clearPanelBucket(panel: Panel) {
		live.panels[panel] = { chat_id: null, label: '', n: 0, samples: [], running: false, error: null };
	}

	/** Fire one panel's generation, detached. Records the fold context under a fresh
	 *  ownership token, POSTs (fire-and-forget), and returns — the bus drives the
	 *  rest. A transport/validation failure on the POST itself surfaces + cleans up;
	 *  a pre-start generation error (unknown run…) arrives on the bus as chat_error. */
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
		const token = convo.newToken(); // marks this chat OURS + flips convo.busy until its terminal
		this.#ownFires.set(token, {
			panel,
			userParentId,
			prefill,
			scope: params.prefill_scope,
			thinking: params.thinking
		});
		api
			.chat({ ...model, messages, ...params, panel, broadcast: true, detached: true, client_token: token })
			.then((res) => {
				if (res.ok) return; // accepted — the bus carries the outcome
				return res.text().then((t) => this.#failFire(token, onError, `Chat error ${res.status}: ${t}`));
			})
			.catch((err) => this.#failFire(token, onError, `Connection error: ${err?.message ?? err}`));
	}

	/** A POST that never reached (or was rejected by) the server: release the token
	 *  and surface it. No-op if the bus already terminated this chat (token gone). */
	#failFire(token: string, onError: (msg: string) => void, msg: string) {
		if (!this.#ownFires.has(token)) return;
		this.#ownFires.delete(token);
		convo.endToken(token);
		onError(msg);
	}

	// ── bus terminal handling (own chats) — wired via convo.init(seam) ──────
	/** Bus `chat_done` for a chat we own → fold its bucket samples (all n) under the
	 *  recorded user node and release the token. Returns true iff we owned it (the
	 *  caller then skips the foreign-fold path). Deterministic: the fold happens on
	 *  the single bus terminal, not racing a drain — so an aborted chat's partials
	 *  fold here too (the server commits them before chat_done). */
	tryFoldOwnDone(panel: Panel, data: { client_token?: string | null; chat_id?: number | null }): boolean {
		const token = data?.client_token;
		if (!token || !this.#ownFires.has(token)) return false;
		const ctx = this.#ownFires.get(token)!;
		this.#ownFires.delete(token);
		const bucket = live.panels[panel];
		// Fold from OUR bucket only if it still belongs to this chat — a concurrent
		// foreign chat on the same panel could have clobbered the single-slot bucket
		// (rare: own+foreign firing the same panel at once). On a mismatch we skip the
		// fold rather than graft foreign samples; the streamed partials stay visible.
		if (bucket && bucket.chat_id === data.chat_id && bucket.samples.some((s) => s)) {
			const folded = this.#foldSamples(bucket.samples, ctx);
			if (folded.length) {
				const { tree, ids } = foldAssistant(convo.treeFor(panel), ctx.userParentId, folded);
				// Seed the per-node blob cache from the fresh nodes (we have the data in
				// hand — no fetch ever needed for this session's own turns). The nodes
				// keep the heavy fields INLINE too: the next dirty-panel PUT ships them
				// once and the server strips them into blobs (docs/STORAGE_V2.md §2.4).
				for (const id of ids) {
					const n = tree.nodes[id];
					nodeBlobs.seed(id, { token_logprobs: n.token_logprobs, raw_meta: n.raw_meta });
				}
				convo.setTree(panel, tree);
			}
		}
		convo.endToken(token);
		return true;
	}

	/** Bus `chat_error` for a chat we own → release the token (the bucket already
	 *  shows the error / 'stopped' strip). Returns true iff we owned it. */
	tryOwnError(_panel: Panel, data: { client_token?: string | null }): boolean {
		const token = data?.client_token;
		if (!token || !this.#ownFires.has(token)) return false;
		this.#ownFires.delete(token);
		convo.endToken(token);
		return true;
	}

	/** Apply the prefill prepend / scope-skip to the bucket's samples so they fold
	 *  identically to the old drain path. Each sample keeps its ORIGINAL bucket index
	 *  as sample_index (foldAssistant orders by it — thinking='both' packs the
	 *  non-thinking half 0..n-1 then the thinking half n..2n-1). Error samples pass
	 *  through untouched (foldAssistant skips them). */
	#foldSamples(samples: SampleData[], ctx: FireContext): SampleLike[] {
		const sideOf = (sm: SampleData) =>
			typeof sm.thinking === 'boolean' ? sm.thinking : ctx.thinking === true;
		const skipPrefill = (sm: SampleData) =>
			(ctx.scope === 'think' && !sideOf(sm)) || (ctx.scope === 'non_think' && sideOf(sm));
		const out: SampleLike[] = [];
		for (let i = 0; i < samples.length; i++) {
			const sm = samples[i];
			if (!sm) continue;
			const withIdx: SampleLike = { ...sm, sample_index: i };
			if (!ctx.prefill || sm.error || skipPrefill(sm)) {
				out.push(withIdx);
			} else {
				out.push({
					...withIdx,
					prefill: ctx.prefill,
					content: sm.prefill_incorporated ? sm.content : ctx.prefill + (sm.content ?? '')
				});
			}
		}
		return out;
	}

	/** Stop one panel if given, else EVERY panel with an in-flight chat. All chats are
	 *  detached now (nothing local to abort), so Stop reaches them uniformly through
	 *  the cancel endpoint by chat_id — own OR foreign (tinkpg / another tab). The
	 *  server fires the guaranteed terminal (chat_done with partials, or
	 *  chat_error("cancelled") on 0 samples). `running` is cleared eagerly so the
	 *  spinner/composer lift immediately even if the terminal round-trips slowly;
	 *  `samples` + `chat_id` stay so partial streamed content remains visible. */
	stopGeneration(panel?: Panel) {
		const targets = (panel ? [panel] : Object.keys(live.panels)) as Panel[];
		for (const k of targets) {
			const b = live.panels[k];
			if (!b?.running) continue;
			if (b.chat_id != null) api.cancelChat(b.chat_id).catch(() => {});
			live.panels[k] = { ...b, running: false };
		}
	}
}

export const chat = new ChatStore();
