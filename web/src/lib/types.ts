// Types mirroring the tinkerscope backend API (see docs/API_CONTRACT.md).

import type { ConvTree, TokenLogprob, TreeOp, PanelOp } from './tree';

export type { TokenLogprob, TreeOp, PanelOp };
export type { OpNode } from './tree';

export type Checkpoint = {
  name: string;
  batch?: number;
  epoch?: number;
  step?: number;
  sampler_path?: string;
  state_path?: string;
  servable?: boolean | null; // sampler weights still exist on tinker; null = unknown
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
  dataset_path?: string | null; // training JSONL, absolute when it exists on disk
  lora_rank?: number | null;
  learning_rate?: number | null;
  seed?: number | null;
  num_checkpoints: number;
  checkpoints: Checkpoint[];
  // Samplable right now = base model served AND ≥1 checkpoint whose sampler
  // weights still exist. false if the base is gone OR all weights gone; null =
  // unknown (offline). Drives the picker's grey/warn/demote treatment.
  sampleable: boolean | null;
  unsampleable_reason?: string | null; // the binding constraint (base gone / weights gone)
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
  /** Base entries only: does the family expose a binary thinking toggle? Drives
   *  whether the composer shows its thinking control for a base pick. Absent on
   *  checkpoints (base/renderer unknown) and legacy responses ⇒ assume true. */
  supports_thinking?: boolean;
  /** A human label is stored for this ref (pack-shipped or user-given). Absent ⇒ the
   *  label is derived (`934cea31 · final · 2026-07-27`), which identifies nothing —
   *  so picking it offers the rename prompt. */
  named?: boolean;
  pack?: boolean;
};

export type TinkerProbe = { available: boolean; base_model: string | null; error: string | null };

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

/** Stable per-panel id: `p-<n>`, minted monotonically per workspace and NEVER
 *  reused, so a `<panel>:<node>` handle can't come to mean a different column (see
 *  ./panel-id.ts). 'primary'/'compare' are the pre-monotonic reserved names — still
 *  valid ids on every workspace saved before the change and on the legacy
 *  {tree, compare_tree} migration, just never minted now. Any panel can be removed
 *  as long as one remains; the invariant is "≥1 panel", not "primary exists". NEVER
 *  an array index — closing a middle panel must not rebind a tree to another. */
export type Panel = string;

/** One panel's MODEL selection (no transcript) — the persisted, per-workspace
 *  layout. `live.state.panels` (PanelState) is this plus the messages echo. */
export type PanelLayout = { id: Panel; run_id: string | null; checkpoint: string | null };

/** The runtime projection of a panel's selection used across the workspace UI
 *  (`panel` = the stable panel id, i.e. PanelLayout's `id`). Derived from the
 *  shared `panels[]`; consumed by the model-catalog + branch-ops stores. */
export type PanelSel = { panel: Panel; run_id: string | null; checkpoint: string | null };

/** One comparison panel's selection + its active-path transcript echo. The echo is
 *  write-only (the branch tree in lib/tree.ts is the read source); it exists so the
 *  CLI and external-fold reconcile can see/replay each panel's path. */
export type PanelState = {
  id: Panel;
  run_id: string | null;
  checkpoint: string | null;
  messages: ChatMessage[];
  /** The active thread's system prompt — mirrored like `messages` (write-mostly;
   *  the tree's root node is the read source). Lets a mid-thread CLI send inherit
   *  the thread's prompt and the echo-reconcile stamp a recovered root. */
  thread_system_prompt?: string | null;
};

/** Shared server-side playground state, streamed over /api/state/events. Sampling
 *  params are GLOBAL (shared across all panels); only run/checkpoint/transcript are
 *  per-panel. */
export type PlaygroundState = {
  panels: PanelState[];
  workspace_id: string | null; // the open workspace's id (browser `?c=`), for `tinkpg state`
  system_prompt: string | null;
  /** Power toggle for the global system prompt: false = kept but muted (skipped
   *  at fire time). null/absent (legacy state) → derive from text presence. */
  system_enabled?: boolean | null;
  temperature: number;
  max_tokens: number;
  n_samples: number;
  /** false / true / 'both' — 'both' fires n_samples without thinking + n_samples
   *  with (2n per chat; see /api/chat in docs/API_CONTRACT.md). */
  thinking: boolean | 'both';
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
  workspace_id?: string | null;
  panel_messages?: Record<string, ChatMessage[]>;
  /** {panel_id: thread system prompt} — mirrored alongside panel_messages. */
  panel_thread_system?: Record<string, string | null>;
  panel?: Panel;
  run_id?: string | null;
  checkpoint?: string | null;
  messages?: ChatMessage[];
  system_prompt?: string | null;
  /** Explicit power state for the global system prompt. The browser ALWAYS sends
   *  it alongside system_prompt — a text patch without it auto-enables server-side
   *  (old-client shim), which would wrongly re-enable a muted draft. */
  system_enabled?: boolean | null;
  temperature?: number;
  max_tokens?: number;
  n_samples?: number;
  thinking?: boolean | 'both';
  top_p?: number | null;
};

/** Scope of the assistant prefill across a send's thinking/non-thinking halves. */
export type PrefillScope = 'all' | 'think' | 'non_think';

export type ChatRequest = {
  run_id?: string | null;
  checkpoint?: string | null;
  base_model?: string | null;
  sampler_path?: string | null;
  openrouter_model?: string | null;
  messages: ChatMessage[];
  system_prompt?: string | null;
  /** The THREAD's system prompt, composed over the global part server-side
   *  (global + "\n" + thread; empty parts skipped). Tri-state: absent/null =
   *  inherit the panel's mirrored thread system; '' = explicitly none; the
   *  browser always sends it explicitly (root-node walk) so a stale mirror can
   *  never leak into a browser fire. */
  thread_system_prompt?: string | null;
  temperature: number;
  max_tokens: number;
  n_samples: number;
  thinking: boolean | 'both';
  /** Which half(s) of a send the trailing-assistant prefill applies to:
   *  'all' = both, 'think' = thinking side only, 'non_think' = non-thinking side
   *  only. In 'both' mode the backend keeps/strips the prefill per-half; in a
   *  single-mode send the mismatched scope drops the prefill entirely. */
  prefill_scope?: PrefillScope;
  /** @deprecated superseded by prefill_scope; true ≡ prefill_scope 'think'. Still
   *  accepted server-side as an alias for any stale client. */
  prefill_thinking_only?: boolean;
  top_p?: number | null;
  top_k?: number | null;
  presence_penalty?: number | null;
  repetition_penalty?: number | null;
  /** Capture per-token logprobs (native tinker paths; server default true). */
  logprobs?: boolean;
  /** Param routing: 'global' (default — the browser's normal sends, params write
   *  back to the shared sidebar state) vs 'call' (this chat only; absent params
   *  inherit the current state, nothing writes back). Loom fires use 'call' so
   *  their per-turn thinking override can't flip the sidebar toggle. */
  params_scope?: 'global' | 'call';
  /** LOOM (exact token-level continue): token ids appended verbatim after the
   *  rendered prompt — a stored sample's prefix + a picked alternative. Native
   *  tinker paths only; see lib/loom.ts + branchOps.loomBranch. */
  continue_tokens?: number[];
  /** Exact renderer override for a loom fire (the renderer recorded in the source
   *  turn's raw_meta), so the re-rendered prompt is the one the prefix tokens
   *  actually continued. */
  renderer_name?: string | null;
  panel: Panel;
  broadcast: boolean;
  /** Fire-and-forget: the POST returns immediately and the generation streams
   *  ONLY to the state bus, so the browser doesn't hold the connection (the
   *  browser sets this for every send — see chat.svelte.ts). */
  detached?: boolean;
  /** Opaque ownership token echoed on chat_start/done/error so the browser can
   *  tell its OWN chats (folded from the bus bucket on chat_done) from external ones. */
  client_token?: string | null;
  /** The workspace this chat belongs to; echoed on the terminal events so a tab
   *  on a DIFFERENT workspace skips the external fold. Absent (CLI) ⇒ the server
   *  falls back to the bus's current stamp. See lib/bus-scope.ts. */
  workspace_id?: string | null;
};

/** Workspace-level fields that accompany every meta write (set_meta / PATCH).
 *  Was save-plan.ts's ConvFields; lives here since the ops cutover retired it. */
export type ConvFields = {
  system_prompt: string | null;
  /** null = legacy/underived (readers fall back to text presence). */
  system_enabled: boolean | null;
  panels: PanelLayout[];
  reduced_panels: string[];
  send_targets: string[];
  seen_panels: string[];
  /** Monotonic panel-id counter (see ./panel-id.ts). */
  panel_seq: number;
};

/** set_meta's payload: any subset of the meta fields (+ name). Field-wise LWW
 *  server-side, except panel_seq (monotone max) and seen_panels (union). */
export type SetMetaFields = Partial<ConvFields> & { name?: string };

/** One workspace mutation on the wire — POST /api/workspaces/{id}/ops and the
 *  bus `ops` echo alike (docs/HANDOFF_SERVER_AUTHORITY.md §4.1). Panel-level ops
 *  (incl. copy_tree = the whole-tree keep-ids clone, and replace_tree null =
 *  drop the panel's tree) are tree.ts's PanelOp; set_meta is the store's. */
export type WorkspaceOp = PanelOp | { op: 'set_meta'; fields: SetMetaFields };

export type OpsResponse = { rev: number; results: { ok: boolean; noop?: boolean }[] };

/** The bus `ops` event: one accepted batch, light node bodies only. */
export type OpsEvent = { workspace: string; rev: number; ops: WorkspaceOp[] };

/** What `GET /api/workspaces` returns per workspace (storage v2): the
 *  sidebar/list projection — NO trees. The full body (trees included, blobs
 *  excluded) is fetched per-workspace via `GET /api/workspaces/{id}`. */
export type WorkspaceSummary = {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  /** Per-workspace panel layout — present so "new workspace inherits the
   *  current model set" works without fetching the body. Absent on legacy rows. */
  panels?: PanelLayout[];
  /** Per-workspace mutation counter (ops protocol). Absent on legacy rows ⇒ 0. */
  rev?: number;
};

/** One node-level hit from GET /api/search (see `api/search.py`): the node's
 *  full tree address (enough to jump-and-reveal it) + a display snippet as a
 *  (before, match_display, after) triple — pre-collapsed server-side so the
 *  palette can highlight the matched span without offset math. */
export type SearchHit = {
  workspace_id: string;
  workspace_name: string;
  panel: Panel;
  node_id: string;
  parent: string | null;
  role: string;
  field: 'content' | 'reasoning' | 'system_prompt';
  thread: number | null;
  on_active_path: boolean;
  sib_index: number;
  sib_count: number;
  match: string;
  before: string;
  match_display: string;
  after: string;
};

/** A workspace-LEVEL match (name / global system prompt / a panel's model id) —
 *  the palette pins these above node hits. */
export type SearchWorkspaceHit = {
  workspace_id: string;
  workspace_name: string;
  field: 'name' | 'system' | 'model';
  panel: Panel | null;
  match: string;
  before: string;
  match_display: string;
  after: string;
  updated_at: string | null;
};

export type SearchResponse = {
  query: string;
  workspace_hits: SearchWorkspaceHit[];
  hits: SearchHit[];
  /** Per-workspace node-hit totals (UNCAPPED, unlike `hits`), in result order. */
  workspace_totals: { workspace_id: string; workspace_name: string; total: number }[];
  total: number;
  truncated: boolean;
  workspaces_searched: number;
  workspaces_matched: number;
};

/** A tree node's heavy out-of-tree payload (storage v2): per-node write-once
 *  blobs, fetched in batch via `POST /api/workspaces/{id}/node-blobs` and
 *  cached in lib/node-blobs.svelte.ts. Light nodes carry `has_*` flags instead. */
export type NodeBlobs = {
  token_logprobs?: TokenLogprob[];
  raw_meta?: string;
};

/** One saved, branchable workspace. The trees are OPAQUE to the backend; the
 *  browser owns them (lib/tree.ts). `system_prompt` travels with the workspace
 *  (each workspace = one experiment). */
export type Workspace = {
  id: string;
  name: string;
  system_prompt: string | null;
  /** Power state of the workspace's system prompt (false = kept but muted).
   *  Absent on legacy bodies → readers derive from text presence. */
  system_enabled?: boolean | null;
  /** Per-panel branch trees, keyed by panel id ('primary','compare','p-2',…). */
  trees: Record<string, ConvTree>;
  /** Legacy 2-panel shape — present only on un-migrated saved workspaces; the
   *  store's #loadTrees read-shim folds these into `trees`. Never written anymore. */
  tree?: ConvTree;
  compare_tree?: ConvTree | null;
  /** Per-workspace panel LAYOUT: which models are shown in which panels. Absent
   *  on legacy workspaces (the store keeps the currently-shown panels on open). */
  panels?: PanelLayout[];
  /** Per-workspace panel UI (opaque panel-id lists): folded panels, composer
   *  send-targets, and the defaulting bookkeeping. Absent on legacy workspaces
   *  (the store treats missing as empty ⇒ every open panel defaults ON). */
  reduced_panels?: string[];
  send_targets?: string[];
  seen_panels?: string[];
  /** Monotonic panel-id counter: panel ids are `p-<n>` and never reused within a
   *  workspace, so a `panel:node` handle can't re-point onto a different model.
   *  Absent on workspaces saved before it existed ⇒ seeded by `highestPanelSeq`. */
  panel_seq?: number;
  /** Per-workspace mutation counter, bumped by the server on every write channel
   *  (ops / PATCH / create / pack apply). The mirror applies bus `ops` events in
   *  rev order and refetches on any gap. Absent on legacy bodies ⇒ 0. */
  rev?: number;
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
  /** Which renderer mode produced this sample — set only on thinking='both' chats
   *  (false = the non-thinking half, true = the thinking half). */
  thinking?: boolean;
  /** Per-token logprobs + top-5 alternatives — native tinker sampling only
   *  (see docs/API_CONTRACT.md). Persists through the fold onto the tree node. */
  token_logprobs?: TokenLogprob[];
  /** Did the backend already fold the trailing-assistant prefill into `content`?
   *  True on the native tinker paths (which return the full turn); false/absent on
   *  the continuation-only paths (OpenRouter / loose), where the bus-bucket fold
   *  must prepend the prefill itself. Mirrors the drain-path fold in chat.svelte.ts. */
  prefill_incorporated?: boolean;
  /** LOOM provenance (continue_tokens fires only): how many leading stream
   *  entries were FORCED (replayed prefix + picked alternative), and that prefix
   *  as frame-normalized display text — tinted like a prefill so n samples don't
   *  read as independent draws. */
  loom_cut?: number;
  loom_text?: string;
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
  /** LOOM provenance (see SampleData): forced-entry count + the replayed prefix as
   *  display text. The prefix tints like a prefill and the token views mark the
   *  fork point. */
  loom_cut?: number;
  loom_text?: string;
  /** How generation ended — 'length' ⇒ cut off by max tokens (truncation badge). */
  finish_reason?: string;
  /** Renderer mode of this turn's sample — set only for thinking='both' batches
   *  (shows the think / no-think chip when cycling the folded siblings). */
  thinking?: boolean;
  /** Per-token logprobs of this turn's sample (native tinker only) — powers the
   *  token-hover inspector when the sidebar "Token probs" toggle is on. Absent on
   *  a LIGHT node whose blob lives server-side — then `has_token_logprobs` is set
   *  and the consumer lazy-fetches through lib/node-blobs (keyed by `nodeId`). */
  token_logprobs?: TokenLogprob[];
  /** Blob-presence flags (storage v2, mirrored off the light tree node): data
   *  exists server-side even when the inline field above is absent. */
  has_token_logprobs?: boolean;
  has_raw_meta?: boolean;
  /** Thread system prompt (ROOT user rows only) — renders the in-row system
   *  strip and prefills the edit box's system field. */
  system_prompt?: string;
  /** True for the active path's first row (a thread root) — gates the edit
   *  box's system field (present even when the root has no prompt yet). */
  isRoot?: boolean;
  samples?: SampleData[];
  totalSamples?: number;
  running?: boolean;
  nodeId?: string | null;
  sib?: { index: number; count: number };
  sampleNodeIds?: string[];
  activeSampleIndex?: number;
  isBucket?: boolean;
  /** Set when this committed turn is EXPANDED into the all-samples view (the
   *  row-toolbar eye): `parent` is the user node whose assistant children are
   *  the sample cards; `hiddenBelow` counts the later view rows hidden while
   *  it's open (drives the exit strip under the cards). */
  samplesExpanded?: { parent: string; hiddenBelow: number };
  /** Non-content status row (e.g. 'stopped' after a 0-sample cancel) — rendered
   *  as a muted strip, not an assistant message; all other fields ignored. */
  notice?: string;
};
