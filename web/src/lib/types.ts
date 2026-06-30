// Types mirroring the tinkerscope backend API (see docs/API_CONTRACT.md).

import type { ConvTree } from './tree';

export type Checkpoint = {
	name: string;
	batch?: number;
	epoch?: number;
	step?: number;
	sampler_path?: string;
	state_path?: string;
};

/** One discovered training run = one model with N selectable checkpoints. */
export type Run = {
	id: string;
	name: string;
	run_dir: string;
	base_model: string;
	wandb_project?: string | null;
	wandb_name?: string | null;
	renderer_name?: string | null;
	dataset_path?: string | null;
	lora_rank?: number | null;
	learning_rate?: number | null;
	seed?: number | null;
	num_checkpoints: number;
	checkpoints: Checkpoint[];
	sampleable: boolean | null; // true | false | null(unknown)
	unsampleable_reason?: string | null;
	config_error?: string | null;
	supports_thinking?: boolean;
};

export type OpenRouterModel = { label: string; openrouter_model: string };

/**
 * One entry in the combined /api/tinker-models list. `id` + `label` are unified
 * across both kinds; `kind` selects the extra fields:
 *   - 'base'       → `base_model` (raw base model, sampled directly, no LoRA)
 *   - 'checkpoint' → `sampler_path` (+ `created`): a "loose" sampler the oai
 *                    endpoint serves right now.
 * For 'base' `id === base_model`; for 'checkpoint' `id === sampler_path`.
 */
export type TinkerModel = {
	kind: 'base' | 'checkpoint';
	id: string;
	label: string;
	base_model?: string;
	sampler_path?: string;
	created?: number;
};

/** Response shape for the two typeahead-catalog endpoints. */
export type TinkerModelsResponse = { available: boolean; error: string | null; models: TinkerModel[] };
export type OpenRouterAvailableResponse = {
	available: boolean;
	error: string | null;
	models: OpenRouterModel[];
};

export type Health = {
	ok: boolean;
	root?: string;
	scan_roots?: string[];
	tinker_key?: boolean;
	openrouter_key?: boolean;
	available?: boolean;
	supported_models?: string[];
	error?: string | null;
};

// `reasoning` (assistant turns only) travels with the message so the sampler can hand the
// renderer the full turn and let it apply its own history policy; `content` stays answer-only.
export type ChatMessage = { role: 'user' | 'assistant' | 'system'; content: string; reasoning?: string };

/** A render-time text-coloring rule (see lib/highlight-match.ts + the backend
 *  api/routes/highlights.py). Mirrors samplescope's HighlightRule, minus the
 *  column / JS-condition scoping that a chat transcript has nothing to bind to. */
export type HighlightRule = {
	id: string;
	name: string;
	enabled: boolean;
	patterns: string[];
	combinator: 'or' | 'and';
	is_regex: boolean;
	case_sensitive: boolean;
	color: string;
	scope_role: string | null; // 'user' | 'assistant' | 'system' | null (any)
	sort_order: number;
};

/** A saved sample worth keeping — the "pins" slideshow (was "highlights").
 *  Open metadata bag; the server stamps id + created_at. */
export type Pin = Record<string, any> & { id: string; created_at: string; note: string };

/** Stable per-panel id. Reserved: 'primary' (always present, slot 0) and 'compare'
 *  (slot 1, also the legacy migration id); further panels are minted 'p-2','p-3',…
 *  NEVER an array index — closing a middle panel must not rebind a tree to another. */
export type Panel = string;

/** One panel's MODEL selection (no transcript) — the persisted, per-conversation
 *  layout. `live.state.panels` (PanelState) is this plus the messages echo. */
export type PanelLayout = { id: Panel; run_id: string | null; checkpoint: string | null };

/** One comparison panel's selection + its active-path transcript echo. The echo is
 *  write-only (the branch tree in lib/tree.ts is the read source); it exists so the
 *  CLI and external-fold reconcile can see/replay each panel's path. */
export type PanelState = {
	id: Panel;
	run_id: string | null;
	checkpoint: string | null;
	messages: ChatMessage[];
};

/** Shared server-side playground state, streamed over /api/state/events. Sampling
 *  params are GLOBAL (shared across all panels); only run/checkpoint/transcript are
 *  per-panel. */
export type PlaygroundState = {
	panels: PanelState[];
	conversation_id: string | null; // the open conversation's id (browser `?c=`), for `tinkpg state`
	system_prompt: string | null;
	temperature: number;
	max_tokens: number;
	n_samples: number;
	thinking: boolean;
	top_p: number | null;
	chat_id: number;
	running: boolean;
	last_event: string | null;
	last_event_ts: number;
};

/** Client-settable patch for PlaygroundState. The patch shape diverges from the
 *  state shape: `panels` full-replaces the list; `panel_messages` mirrors every
 *  panel's transcript at once; `panel`+run_id/checkpoint/messages targets ONE panel;
 *  the rest are global params. */
export type StatePatch = {
	panels?: PanelState[];
	conversation_id?: string | null;
	panel_messages?: Record<string, ChatMessage[]>;
	panel?: Panel;
	run_id?: string | null;
	checkpoint?: string | null;
	messages?: ChatMessage[];
	system_prompt?: string | null;
	temperature?: number;
	max_tokens?: number;
	n_samples?: number;
	thinking?: boolean;
	top_p?: number | null;
};

export type ChatRequest = {
	run_id?: string | null;
	checkpoint?: string | null;
	base_model?: string | null;
	sampler_path?: string | null;
	openrouter_model?: string | null;
	messages: ChatMessage[];
	system_prompt?: string | null;
	temperature: number;
	max_tokens: number;
	n_samples: number;
	thinking: boolean;
	top_p?: number | null;
	top_k?: number | null;
	presence_penalty?: number | null;
	repetition_penalty?: number | null;
	panel: Panel;
	broadcast: boolean;
	/** Opaque ownership token echoed on chat_start/done/error so the browser can
	 *  tell its OWN chats (folded from this response stream) from external ones. */
	client_token?: string | null;
};

/** One saved, branchable conversation. The trees are OPAQUE to the backend; the
 *  browser owns them (lib/tree.ts). `system_prompt` travels with the conversation
 *  (each conversation = one experiment). */
export type Conversation = {
	id: string;
	name: string;
	system_prompt: string | null;
	/** Per-panel branch trees, keyed by panel id ('primary','compare','p-2',…). */
	trees: Record<string, ConvTree>;
	/** Legacy 2-panel shape — present only on un-migrated saved conversations; the
	 *  store's #loadTrees read-shim folds these into `trees`. Never written anymore. */
	tree?: ConvTree;
	compare_tree?: ConvTree | null;
	/** Per-conversation panel LAYOUT: which models are shown in which panels. Absent
	 *  on legacy conversations (the store keeps the currently-shown panels on open). */
	panels?: PanelLayout[];
	/** Per-conversation panel UI (opaque panel-id lists): folded panels, composer
	 *  send-targets, and the defaulting bookkeeping. Absent on legacy conversations
	 *  (the store treats missing as empty ⇒ every open panel defaults ON). */
	reduced_panels?: string[];
	send_targets?: string[];
	seen_panels?: string[];
	created_at: string;
	updated_at: string;
};

/** A single streamed completion (one sample of an n-sample fan-out). */
export type SampleData = {
	content: string;
	reasoning?: string;
	raw_text?: string;
	/** Tinker only: the request sent + trimmed response, shown in a dropdown
	 *  beneath the decoded-token `raw_text`. (OpenRouter has no tokens, so its
	 *  request/response lives in `raw_text` itself.) */
	raw_meta?: string;
	finish_reason?: string;
	error?: string;
};

/**
 * One rendered row in a chat column: either a committed tree node or the live
 * "bucket" turn (the latest turn's N variants / streaming progress). `nodeId`
 * ties it back to its tree node so edit/regenerate/delete/cycle can target it
 * (null = a bucket/error artifact not yet folded). `sib` carries the sibling
 * index/count for the ‹k/N› cycle control. `sampleNodeIds` maps n>1 sample
 * cards (by index) to their folded sibling node ids for click-to-select.
 */
export type ViewMessage = {
	role: 'user' | 'assistant' | 'system';
	content: string;
	reasoning?: string;
	raw_text?: string;
	raw_meta?: string;
	/** Authored prefill this turn was generated from (raw text); the renderer colors
	 *  the matching leading slice of content/reasoning as the prefilled portion. */
	prefill?: string;
	samples?: SampleData[];
	totalSamples?: number;
	running?: boolean;
	nodeId?: string | null;
	sib?: { index: number; count: number };
	sampleNodeIds?: string[];
	activeSampleIndex?: number;
	isBucket?: boolean;
};
