// Types mirroring the tinkerscope backend API (see API_CONTRACT.md).

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

export type ChatMessage = { role: 'user' | 'assistant' | 'system'; content: string };

export type Panel = 'primary' | 'compare';

/** Shared server-side playground state, streamed over /api/state/events. */
export type PlaygroundState = {
	mode: 'single' | 'compare';
	run_id: string | null;
	checkpoint: string | null;
	compare_run_id: string | null;
	compare_checkpoint: string | null;
	messages: ChatMessage[]; // PRIMARY panel's transcript
	compare_messages: ChatMessage[]; // COMPARE panel's OWN transcript (compare is multi-turn)
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

/** Any settable subset of PlaygroundState (everything except chat_id/running/last_event*). */
export type StatePatch = Partial<
	Omit<PlaygroundState, 'chat_id' | 'running' | 'last_event' | 'last_event_ts'>
>;

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
};

/** A single streamed completion (one sample of an n-sample fan-out). */
export type SampleData = {
	content: string;
	reasoning?: string;
	raw_text?: string;
	finish_reason?: string;
	error?: string;
};

/**
 * One rendered row in a chat column: either a committed transcript message or
 * the live "bucket" turn (the latest turn's N variants / streaming progress).
 * `transcriptIdx` ties it back to the committed transcript so edit/regenerate/
 * delete can target it (null = a bucket/error artifact with no committed row).
 */
export type ViewMessage = {
	role: 'user' | 'assistant' | 'system';
	content: string;
	reasoning?: string;
	raw_text?: string;
	samples?: SampleData[];
	totalSamples?: number;
	running?: boolean;
	transcriptIdx?: number | null;
	isBucket?: boolean;
};
