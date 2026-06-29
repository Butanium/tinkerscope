<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { live, emptyPanel } from '$lib/state.svelte';
	import { conversations as convo } from '$lib/conversations.svelte';
	import Message from '$lib/ChatMessage.svelte';
	import { assembleAssistantRaw } from '$lib/render';
	import {
		OR_PREFIX, BASE_PREFIX, CKPT_PREFIX,
		isOpenrouterSel, openrouterId,
		isBaseSel, baseModelId,
		isCkptSel, samplerPathOf
	} from '$lib/model-sel';
	import { drainSamples } from '$lib/chat-stream';
	import { computeChartBars, type ChartData } from '$lib/chart';
	import ChartModal from '$lib/ChartModal.svelte';
	import TagModal from '$lib/TagModal.svelte';
	import DatasetModal from '$lib/DatasetModal.svelte';
	import SlideshowModal from '$lib/SlideshowModal.svelte';
	import OrManagerModal from '$lib/OrManagerModal.svelte';
	import TinkerPickerModal from '$lib/TinkerPickerModal.svelte';
	import { loadHighlightRules } from '$lib/highlights.svelte';
	import HighlightRules from '$lib/HighlightRules.svelte';
	import type { Pin } from '$lib/types';
	import { tip, tooltip } from '$lib/tooltip.svelte';
	import {
		activePath,
		activeMessages,
		appendUserTurn,
		foldAssistant,
		regenTarget,
		regenReplace,
		ancestryMessages,
		treeFromMessages,
		editUserFork,
		editUserForkCopy,
		editAssistant,
		deleteSubtree,
		deleteSiblings,
		setSelected,
		cycle as cycleTree,
		siblingInfo,
		siblingsOf
	} from '$lib/tree';
	import type {
		Run,
		OpenRouterModel,
		TinkerModel,
		Health,
		PlaygroundState,
		StatePatch,
		ChatMessage,
		Panel,
		ViewMessage
	} from '$lib/types';

	// ── Theme ─────────────────────────────────────────────────────────
	// Three modes; 'auto' resolves to the OS scheme and live-tracks system changes.
	type ThemeMode = 'light' | 'dark' | 'auto';
	let themeMode = $state<ThemeMode>('auto');
	// Apply the resolved class to <html> whenever the mode changes, and — only in
	// 'auto' — re-apply when the OS scheme flips. The $effect cleanup detaches the
	// media-query listener when leaving 'auto' (or on unmount).
	$effect(() => {
		const mode = themeMode;
		const mql = window.matchMedia('(prefers-color-scheme: dark)');
		const apply = () => {
			document.documentElement.className = mode === 'auto' ? (mql.matches ? 'dark' : 'light') : mode;
		};
		apply();
		if (mode !== 'auto') return;
		mql.addEventListener('change', apply);
		return () => mql.removeEventListener('change', apply);
	});
	function cycleTheme() {
		themeMode = themeMode === 'light' ? 'dark' : themeMode === 'dark' ? 'auto' : 'light';
		localStorage.setItem('playground-theme', themeMode);
	}

	// How n>1 sample distributions render: 'all' = every card stacked (scroll),
	// 'cycle' = one card at a time with ‹/› prev-next. UI-only; persisted locally.
	let sampleView = $state<'all' | 'cycle'>('all');
	function setSampleView(v: 'all' | 'cycle') {
		sampleView = v;
		localStorage.setItem('playground-sample-view', v);
	}

	// ── Data: runs / openrouter / health ──────────────────────────────
	let runs = $state<Run[]>([]);
	let openrouterModels = $state<OpenRouterModel[]>([]);
	let health = $state<Health | null>(null);
	let backendError = $state('');
	let refreshingModels = $state(false);

	// Typeahead catalogs (lazy-loaded when their picker opens).
	let tinkerModels = $state<TinkerModel[]>([]);
	let tinkerCatalogLoaded = $state(false);
	let tinkerCatalogLoading = $state(false);
	let tinkerCatalogError = $state<string | null>(null);
	let orCatalog = $state<OpenRouterModel[]>([]);
	let orCatalogLoaded = $state(false);
	let orCatalogLoading = $state(false);
	let orCatalogError = $state<string | null>(null);

	// Recently-used raw base models + loose checkpoints (localStorage) so a picked
	// tinker model stays visible in the panel <select> across reloads even though
	// it's not a Run. Two lightweight shapes — one per sentinel.
	type RecentBase = { base_model: string; label: string };
	type RecentCkpt = { sampler_path: string; label: string };
	const RECENT_BASE_KEY = 'tinkerscope-recent-base-models';
	const RECENT_CKPT_KEY = 'tinkerscope-recent-checkpoints';
	let recentBaseModels = $state<RecentBase[]>([]);
	let recentCheckpoints = $state<RecentCkpt[]>([]);
	function rememberBaseModel(m: RecentBase) {
		const next = [m, ...recentBaseModels.filter((x) => x.base_model !== m.base_model)].slice(0, 8);
		recentBaseModels = next;
		try { localStorage.setItem(RECENT_BASE_KEY, JSON.stringify(next)); } catch {}
	}
	function rememberCheckpoint(m: RecentCkpt) {
		const next = [m, ...recentCheckpoints.filter((x) => x.sampler_path !== m.sampler_path)].slice(0, 8);
		recentCheckpoints = next;
		try { localStorage.setItem(RECENT_CKPT_KEY, JSON.stringify(next)); } catch {}
	}

	function runById(id: string | null | undefined): Run | undefined {
		if (!id) return undefined;
		return runs.find((r) => r.id === id);
	}

	function runLabel(r: Run): string {
		if (r.config_error) return `${r.name} (config error)`;
		return r.name;
	}

	// ── Model labels (sentinel/run id → display label) ────────────────
	// The pure sentinel encoding (OR_/BASE_/CKPT_ prefixes + predicates +
	// id extractors) lives in $lib/model-sel. These resolvers layer on it,
	// reading the reactive catalogs (openrouterModels / tinkerModels / recents)
	// to turn an id into something human-readable.
	function openrouterBySel(id: string | null | undefined): OpenRouterModel | undefined {
		const orId = openrouterId(id);
		if (orId == null) return undefined;
		return openrouterModels.find((m) => m.openrouter_model === orId);
	}
	function openrouterLabel(id: string | null | undefined): string {
		const orId = openrouterId(id);
		if (orId == null) return '';
		const m = openrouterBySel(id);
		return m?.label || orId;
	}
	function baseLabel(id: string | null | undefined): string {
		const bm = baseModelId(id);
		if (bm == null) return '';
		const m = tinkerModels.find((t) => t.base_model === bm) ?? recentBaseModels.find((t) => t.base_model === bm);
		return m?.label || bm;
	}
	function ckptLabel(id: string | null | undefined): string {
		const sp = samplerPathOf(id);
		if (sp == null) return '';
		const m = tinkerModels.find((t) => t.sampler_path === sp) ?? recentCheckpoints.find((t) => t.sampler_path === sp);
		return m?.label || sp;
	}

	// ── Model picker filter (type-to-narrow the Models dropdown) ──────
	// A shared, case-insensitive substring filter over the picker optgroups. The
	// currently-selected option is always kept (see template) so the native
	// <select> never goes blank when the filter would otherwise hide it.
	let modelFilter = $state('');
	function matchModel(...texts: (string | null | undefined)[]): boolean {
		const mf = modelFilter.trim().toLowerCase();
		if (!mf) return true;
		return texts.some((t) => (t ?? '').toLowerCase().includes(mf));
	}

	// Whether shift is currently held — drives the alternate-action affordance on
	// the regenerate/edit buttons (icon + tooltip swap). Wired in onMount.
	let shiftDown = $state(false);
	let ctrlDown = $state(false);

	// ── Live shared state (single source of truth for selection/params) ──
	// Render from live.state; fall back to defaults until the first snapshot.
	const DEFAULTS: PlaygroundState = {
		panels: [{ id: 'primary', run_id: null, checkpoint: null, messages: [] }],
		system_prompt: null, temperature: 0.7, max_tokens: 4000, n_samples: 1,
		thinking: false, top_p: 0.8, chat_id: 0, running: false, last_event: null, last_event_ts: 0
	};
	let s = $derived<PlaygroundState>(live.state ?? DEFAULTS);

	// The N panels in display order, projected from the shared panels[] array. Each
	// has a STABLE id (never an array index) so reorder/remove can't rebind a tree.
	type PanelSel = { panel: Panel; run_id: string | null; checkpoint: string | null };
	let panelSels = $derived<PanelSel[]>(
		(s.panels ?? []).map((ps) => ({ panel: ps.id, run_id: ps.run_id, checkpoint: ps.checkpoint }))
	);
	let isComparing = $derived(panelSels.length > 1);

	// Folded panels (`reducedPanels`) + composer send-targets (`sendTargets`) are owned
	// by the conversation store and PERSISTED per-conversation (they survive a restart
	// and switch with the conversation). This effect just feeds the live panel list to
	// the store's defaulting reconcile: a newly-added panel defaults ON, while restored
	// deselections/folds stick (syncPanels is purely additive — see its docstring).
	$effect(() => {
		convo.syncPanels(panelSels.map((p) => p.panel));
	});
	/** Panels a send will actually fire to (selected targets; fall back to all). */
	let targetSels = $derived(
		convo.sendTargets.size ? panelSels.filter((p) => convo.sendTargets.has(p.panel)) : panelSels
	);

	let anySupportsThinking = $derived(
		// OpenRouter reference + raw tinker base models: assume thinking-capable
		// (backend handles the flag).
		panelSels.some(
			(p) =>
				isOpenrouterSel(p.run_id) ||
				isBaseSel(p.run_id) ||
				isCkptSel(p.run_id) ||
				runById(p.run_id)?.supports_thinking
		)
	);

	// Advanced sampling params (Qwen recommended defaults for non-thinking) —
	// these are local UI knobs sent on /api/chat (top_k etc. aren't in shared state).
	let topK = $state(20);
	let presencePenalty = $state(1.5);
	let repetitionPenalty = $state(1.0);
	let showSamplingPopup = $state(false);

	const QWEN_PRESETS = {
		nonThinking: { temperature: 0.7, topP: 0.8, topK: 20, presencePenalty: 1.5, repetitionPenalty: 1.0 },
		thinking: { temperature: 1.0, topP: 0.95, topK: 20, presencePenalty: 1.5, repetitionPenalty: 1.0 }
	};

	function applyQwenDefaults(isThinking: boolean) {
		const preset = isThinking ? QWEN_PRESETS.thinking : QWEN_PRESETS.nonThinking;
		topK = preset.topK;
		presencePenalty = preset.presencePenalty;
		repetitionPenalty = preset.repetitionPenalty;
		patchState({ temperature: preset.temperature, top_p: preset.topP });
	}

	// ── State driving: POST /api/state so terminal + browser stay synced ──
	let patchTimer: ReturnType<typeof setTimeout> | null = null;
	let pendingPatch: StatePatch = {};

	/** Debounced state patch (sliders/typing fire rapidly). */
	function patchState(patch: StatePatch, immediate = false) {
		pendingPatch = { ...pendingPatch, ...patch };
		if (patchTimer) clearTimeout(patchTimer);
		const flush = () => {
			const body = pendingPatch;
			pendingPatch = {};
			patchTimer = null;
			api.setState(body).catch((e) => console.warn('state patch failed', e));
		};
		if (immediate) flush();
		else patchTimer = setTimeout(flush, 200);
	}

	// ── Per-panel selection edits ─────────────────────────────────────
	function defaultCheckpoint(runId: string): string | null {
		// OpenRouter / raw base / loose checkpoint have no checkpoint selector; tinker
		// runs default to the last checkpoint with a sampler (usually "final").
		if (isOpenrouterSel(runId) || isBaseSel(runId) || isCkptSel(runId)) return null;
		const r = runById(runId);
		return r?.checkpoints.length ? r.checkpoints[r.checkpoints.length - 1].name : null;
	}
	function setRun(panel: Panel, runId: string) {
		patchState({ panel, run_id: runId, checkpoint: defaultCheckpoint(runId) }, true);
		convo.save(); // the panel layout (models) is persisted with the conversation
	}
	function setCheckpoint(panel: Panel, ck: string) {
		patchState({ panel, checkpoint: ck }, true);
		convo.save();
	}

	/** Drop a panel's live sample bucket (so a stale run can't show after remove). */
	function dropPanelBucket(panel: Panel) {
		const { [panel]: _drop, ...rest } = live.panels;
		live.panels = rest;
	}

	// ── Panel lifecycle: add / remove / reduce / restore ──────────────
	/** Next stable panel id: reuse reserved 'compare' for slot 1, then p-2, p-3, … */
	function nextPanelId(): string {
		const ids = new Set(s.panels.map((p) => p.id));
		if (!ids.has('compare')) return 'compare';
		let n = 2;
		while (ids.has('p-' + n)) n++;
		return 'p-' + n;
	}
	const MAX_PANELS = 6;
	function addPanel() {
		if (s.panels.length >= MAX_PANELS || runs.length + openrouterModels.length < 1) return;
		const id = nextPanelId();
		// Pick a model not already shown, preferring a sampleable run.
		const used = new Set(s.panels.map((p) => p.run_id));
		const other =
			runs.find((r) => !used.has(r.id) && r.sampleable !== false) ??
			runs.find((r) => !used.has(r.id)) ??
			runs[0];
		const ck = other?.checkpoints.length ? other.checkpoints[other.checkpoints.length - 1].name : null;
		// Seed the new panel's tree from primary so it starts from the same thread.
		convo.duplicateTo('primary', id);
		const seedMsgs = activeMessages(convo.treeFor(id)) as ChatMessage[];
		const nextPanels = [
			...s.panels.map((p) => ({ ...p })),
			{ id, run_id: other?.id ?? null, checkpoint: ck, messages: seedMsgs }
		];
		patchState({ panels: nextPanels }, true);
		// the new panel auto-joins sendTargets (active by default) via convo.syncPanels (the effect above)
	}
	function removePanel(panel: Panel) {
		if (panel === 'primary') return; // primary is reserved/always present
		stopGeneration(panel);
		const nextPanels = s.panels.filter((p) => p.id !== panel).map((p) => ({ ...p }));
		patchState({ panels: nextPanels }, true);
		convo.dropTree(panel);
		dropPanelBucket(panel);
		convo.dropPanelUi(panel);
	}

	// ── Param edits → shared state ────────────────────────────────────
	// Guard every numeric setter: a transiently-empty number input yields NaN,
	// which would serialize to null and (worse) propagate garbage into requests.
	// Ignore NaN and clamp to sane bounds so state never holds invalid numbers.
	function setTemperature(v: number) { if (Number.isNaN(v)) return; patchState({ temperature: Math.max(0, Math.min(2, v)) }); }
	function setMaxTokens(v: number) { if (Number.isNaN(v)) return; patchState({ max_tokens: Math.max(1, Math.min(32000, Math.round(v))) }); }
	function setNSamples(v: number) { if (Number.isNaN(v)) return; patchState({ n_samples: Math.max(1, Math.min(200, Math.round(v))) }); }
	function setSystemPrompt(v: string) { patchState({ system_prompt: v || null }); convo.save(); }
	function setTopP(v: number) { if (Number.isNaN(v)) return; patchState({ top_p: Math.max(0, Math.min(1, v)) }); }
	function toggleThinking() {
		const next = !s.thinking;
		patchState({ thinking: next }, true);
		applyQwenDefaults(next);
	}

	// ── Sampleability / chat eligibility ──────────────────────────────
	function panelRun(p: PanelSel): Run | undefined { return runById(p.run_id); }
	/** Whether ONE panel's selected model is chat-eligible. OpenRouter refs + raw
	 *  tinker base models + loose checkpoints are always eligible (the backend
	 *  errors clearly if a key is missing). */
	function panelCanChat(p: PanelSel): boolean {
		if (isOpenrouterSel(p.run_id) || isBaseSel(p.run_id) || isCkptSel(p.run_id)) return true;
		const r = panelRun(p);
		return !!(r && r.sampleable !== false && r.checkpoints.length > 0);
	}
	// Chat eligibility is about the panels a send will actually fire to (the targets).
	let canChat = $derived(targetSels.length > 0 && targetSels.every(panelCanChat));
	/** All TARGET panels busy → the shared composer can't fire anything new. */
	let allBusy = $derived(targetSels.length > 0 && targetSels.every((p) => panelBusy(p.panel)));
	let anyRunning = $derived(panelSels.some((p) => panelBusy(p.panel)));
	/** Per-panel busy = that panel's bus `running` flag (set on chat_start, cleared
	 *  on chat_done/chat_error — for our own chats AND CLI/other-tab ones). This is
	 *  the authoritative per-panel signal; it must NOT also key off abortByPanel,
	 *  whose clear is tied to fireChat's promise — if that stream lingers a moment
	 *  past chat_done the controls would stay wrongly disabled. abortByPanel is for
	 *  stopGeneration only. Conversation-switch safety still uses convo.busy (tokens). */
	function panelBusy(panel: Panel): boolean {
		return live.panels[panel]?.running === true;
	}

	// ── Session persistence (model selection + params, cached on disk) ──
	// The selection/params live in PlaygroundState, which is per-process and lost
	// on restart. Mirror the relevant fields to the on-disk prefs store (debounced)
	// so reopening tinkerscope restores your last-used models + sampling params.
	// system_prompt is deliberately NOT persisted here — it travels per-conversation.
	const SESSION_PREF_KEY = 'last_session';
	let prefsLoaded = $state(false); // set true after restore; gates saving over our own defaults
	let sessionSaveTimer: ReturnType<typeof setTimeout> | null = null;
	let lastSessionJson = ''; // skip redundant PUTs (the effect re-fires on every SSE event)
	function persistSession() {
		if (!prefsLoaded) return;
		if (sessionSaveTimer) clearTimeout(sessionSaveTimer);
		sessionSaveTimer = setTimeout(() => {
			const json = JSON.stringify({
				// the panel layout (model selection per panel), not the dead scalars
				panels: s.panels.map((p) => ({ id: p.id, run_id: p.run_id, checkpoint: p.checkpoint })),
				temperature: s.temperature, max_tokens: s.max_tokens, n_samples: s.n_samples,
				thinking: s.thinking, top_p: s.top_p,
				top_k: topK, presence_penalty: presencePenalty, repetition_penalty: repetitionPenalty
			});
			if (json === lastSessionJson) return; // selection/params unchanged (e.g. mid-stream)
			lastSessionJson = json;
			api.setPref(SESSION_PREF_KEY, json).catch(() => {});
		}, 500);
	}
	$effect(() => {
		// Touch every persisted field so the effect re-runs whenever any changes.
		void s.panels;
		void s.temperature; void s.max_tokens; void s.n_samples; void s.thinking; void s.top_p;
		void topK; void presencePenalty; void repetitionPenalty;
		persistSession();
	});
	/** Restore the saved selection/params — only when the process state is fresh
	 *  (no run selected yet), so we never clobber a session another tab/CLI set. */
	async function restoreSession(): Promise<void> {
		try {
			const prefs = await api.getPrefs();
			const raw = prefs[SESSION_PREF_KEY];
			// Only restore into a FRESH process (no panel has a run selected yet), so we
			// never clobber a session another tab/CLI already set.
			const freshState = (live.state?.panels ?? []).every((p) => p.run_id == null);
			if (raw && freshState) {
				const sess = JSON.parse(raw);
				if (typeof sess.top_k === 'number') topK = sess.top_k;
				if (typeof sess.presence_penalty === 'number') presencePenalty = sess.presence_penalty;
				if (typeof sess.repetition_penalty === 'number') repetitionPenalty = sess.repetition_penalty;
				const panels = Array.isArray(sess.panels) && sess.panels.length
					? sess.panels.map((p: { id?: string; run_id?: string | null; checkpoint?: string | null }) => ({
							id: p.id ?? 'primary', run_id: p.run_id ?? null, checkpoint: p.checkpoint ?? null, messages: []
						}))
					: undefined;
				await api.setState({
					...(panels ? { panels } : {}),
					...(typeof sess.temperature === 'number' ? { temperature: sess.temperature } : {}),
					...(typeof sess.max_tokens === 'number' ? { max_tokens: sess.max_tokens } : {}),
					...(typeof sess.n_samples === 'number' ? { n_samples: sess.n_samples } : {}),
					...(typeof sess.thinking === 'boolean' ? { thinking: sess.thinking } : {}),
					...(typeof sess.top_p === 'number' ? { top_p: sess.top_p } : {})
				});
			}
		} catch {
			/* missing/corrupt prefs → just start fresh */
		}
		prefsLoaded = true;
	}

	// ── Send a chat (browser-initiated) ───────────────────────────────
	// Single code path with CLI: append user msg to shared state, POST /api/chat
	// per panel with broadcast:true, then render purely from the bus.
	let userInput = $state('');
	let inputTextarea: HTMLTextAreaElement;
	// Assistant prefill (interp / red-teaming): authored, NOT trimmed — the model
	// continues from here. Sent as a trailing {role:'assistant'} message; the backend
	// (tinker_sampler.render) treats a trailing assistant turn as a prefill the renderer
	// appends verbatim, so raw `<think>…</think>` works (per-family `<think>` rules in
	// the placeholder). Persists across sends so you can draw N samples off one prefill.
	let prefillInput = $state('');
	// `showPrefill` doubles as the ON/OFF switch: the textarea is open IFF the prefill
	// is active. Collapsing it (re-clicking the toggle) DISABLES the prefill — the text
	// is remembered (shown as a peek) so re-opening restores it. So a prefill is applied
	// to sends only while open + non-empty.
	let showPrefill = $state(false);
	let prefillActive = $derived(showPrefill && prefillInput.trim().length > 0);
	/** Per-panel prefill of the last/in-flight fire — lets the live bucket color its
	 *  prefilled prefix (committed nodes carry their own `prefill`). '' ⇒ none. */
	let firePrefill = $state<Partial<Record<Panel, string>>>({});
	/** Append the active prefill (if any) as a trailing assistant turn so the model
	 *  EXTENDS it; the returned `prefill` is prepended to each folded sample. Whitespace
	 *  is preserved (a trailing `</think>\n\n` matters). Disabled (collapsed) ⇒ no-op. */
	function withPrefill(msgs: ChatMessage[]): { fireMsgs: ChatMessage[]; prefill?: string } {
		if (!prefillActive) return { fireMsgs: msgs };
		return { fireMsgs: [...msgs, { role: 'assistant', content: prefillInput }], prefill: prefillInput };
	}
	// Per-panel composer drafts for the "continue this panel" bubbles (compare).
	let panelDraft = $state<Partial<Record<Panel, string>>>({});
	// Per-panel in-flight abort controllers (keyed by panel so concurrent
	// generations on different panels don't clobber each other's handle). $state so
	// panelBusy() — and every per-panel gate derived from it — reacts on change.
	let abortByPanel = $state<Partial<Record<Panel, AbortController | null>>>({});

	async function sendMessage() {
		const text = userInput.trim();
		// Guard on convo.activeId: until the conversation store has loaded, a send
		// would build an in-memory tree that load() then clobbers (race → lost reply).
		if (!text || allBusy || !canChat || !convo.activeId) return;

		pushHistory(text);
		userInput = '';

		// Append a user node to each TARGET panel's TREE (the single source of truth)
		// and fire it. A panel already mid-generation is skipped (don't double-fire);
		// the others fire concurrently. Only selected send-target panels fire.
		for (const p of targetSels) {
			if (panelBusy(p.panel)) continue;
			const { tree, nodeId } = appendUserTurn(convo.treeFor(p.panel), text);
			convo.setTree(p.panel, tree);
			const msgs = activeMessages(convo.treeFor(p.panel)) as ChatMessage[];
			clearPanelBucket(p.panel);
			const { fireMsgs, prefill } = withPrefill(msgs);
			fireOne(p, nodeId, fireMsgs, prefill);
		}
	}

	/** Send to ONE panel only (the per-panel "continue this panel" bubble). Fires
	 *  independently of the other panel and of whatever it's doing. */
	function sendToPanel(panel: Panel) {
		const pSel = panelSels.find((x) => x.panel === panel);
		if (!pSel) return;
		const text = (panelDraft[panel] ?? '').trim();
		if (!text || panelBusy(panel) || !panelCanChat(pSel) || !convo.activeId) return;
		pushHistory(text);
		panelDraft[panel] = '';
		const { tree, nodeId } = appendUserTurn(convo.treeFor(panel), text);
		convo.setTree(panel, tree);
		const msgs = activeMessages(convo.treeFor(panel)) as ChatMessage[];
		clearPanelBucket(panel);
		const { fireMsgs, prefill } = withPrefill(msgs);
		fireOne(pSel, nodeId, fireMsgs, prefill);
	}

	/** Fire one panel's generation with a fresh per-panel abort controller.
	 *  `prefill` (continuation): the samples come back as the continuation only, so
	 *  it's prepended to each in the fold to form the full continued message. */
	function fireOne(pSel: PanelSel, userParentId: string, messages: ChatMessage[], prefill?: string) {
		firePrefill[pSel.panel] = prefill ?? ''; // so the live bucket colors its prefilled prefix
		const ac = new AbortController();
		abortByPanel[pSel.panel] = ac;
		fireChat(pSel, userParentId, messages, ac.signal, prefill).finally(() => {
			if (abortByPanel[pSel.panel] === ac) abortByPanel[pSel.panel] = null;
		});
	}

	async function fireChat(
		p: PanelSel,
		userParentId: string,
		messages: ChatMessage[],
		signal: AbortSignal,
		prefill?: string
	) {
		// Resolve the model: tinker LoRA run, raw tinker base model, loose tinker
		// checkpoint, or OpenRouter reference. EXACTLY ONE id field is sent.
		const orId = openrouterId(p.run_id);
		const bm = baseModelId(p.run_id);
		const sp = samplerPathOf(p.run_id);
		const r = orId == null && bm == null && sp == null ? panelRun(p) : undefined;
		if (orId == null && bm == null && sp == null && !r) return;
		const token = convo.newToken(); // marks this chat OURS so the external-fold hook skips it
		try {
			const res = await api.chat(
				{
					...(orId != null
						? { openrouter_model: orId }
						: bm != null
							? { base_model: bm }
							: sp != null
								? { sampler_path: sp }
								: { run_id: r!.id, checkpoint: p.checkpoint }),
					messages,
					system_prompt: s.system_prompt,
					temperature: s.temperature,
					max_tokens: s.max_tokens,
					n_samples: s.n_samples,
					thinking: s.thinking,
					top_p: s.top_p,
					top_k: topK,
					presence_penalty: presencePenalty,
					repetition_penalty: repetitionPenalty,
					panel: p.panel,
					broadcast: true,
					client_token: token
				},
				signal
			);
			if (!res.ok) {
				backendError = `Chat error ${res.status}: ${await res.text()}`;
				return;
			}
			// Collect our samples from our OWN stream + fold them under the user node.
			// For a continuation (prefill set), each sample is the CONTINUATION only,
			// so prepend the prefill to form the full extended message — UNLESS the
			// backend already folded it in (`prefill_incorporated`, the native tinker
			// path, which also splits prefilled <think> into `reasoning`).
			const samples = await drainSamples(res);
			if (samples.length) {
				// Stamp the prefill onto each folded sample (so the committed node remembers
				// it and can color the prefilled prefix). Non-incorporated samples are the
				// continuation only → prepend the prefill to form the full message; the
				// native tinker path already incorporated it.
				const folded = prefill
					? samples.map((sm) =>
							sm.error
								? sm
								: { ...sm, prefill, content: sm.prefill_incorporated ? sm.content : prefill + (sm.content ?? '') }
						)
					: samples;
				const { tree } = foldAssistant(convo.treeFor(p.panel), userParentId, folded);
				convo.setTree(p.panel, tree);
			}
		} catch (err: any) {
			// Abort (user hit Stop) → leave the user node reply-less; no partial fold.
			if (err?.name !== 'AbortError') backendError = `Connection error: ${err?.message ?? err}`;
		} finally {
			convo.endToken(token);
		}
	}

	function stopGeneration(panel?: Panel) {
		// Stop one panel if given, else all in-flight panels.
		const panels = panel ? [panel] : (Object.keys(abortByPanel) as Panel[]);
		for (const k of panels) {
			abortByPanel[k]?.abort();
			abortByPanel[k] = null;
		}
	}

	async function newConversation(e?: MouseEvent) {
		if (anyRunning || convo.busy) return;
		// Inherit the current conversation's panel MODELS (not its messages) so a new
		// thread opens against the same comparison set. Shift+click → a single blank
		// panel with no model selected (a clean slate).
		const layout = e?.shiftKey
			? [{ id: 'primary', run_id: null, checkpoint: null }]
			: s.panels.map((p) => ({ id: p.id, run_id: p.run_id, checkpoint: p.checkpoint }));
		// Mint the id and push ?c= BEFORE create — and AWAIT the navigation so
		// `page.url` is current. create() sets activeId then awaits an optimistic
		// setState, yielding to the reactive scheduler; if `page.url` still pointed at
		// the OLD conversation (goto not yet applied) the ?c= sync effect would switch
		// right back. Awaiting goto first keeps URL == activeId across that await — no
		// revert. The id isn't in `list` until create commits, so the effect ignores
		// the URL in the meantime. new id → history entry; back returns to prior conv.
		const id = crypto.randomUUID();
		await setConvUrl(id, true);
		await convo.create('Untitled', layout, id);
		try { await api.close(); } catch {}
	}

	// ── Conversation ↔ URL sync (?c=<id>) ─────────────────────────────
	// The active conversation rides in the `?c=` query param so a URL can be
	// shared / bookmarked / back-forward navigated. Query param (not a path) is
	// forced by the static-serving setup: FastAPI's StaticFiles only serves
	// index.html at `/`, so a hard-load of `/c/<id>` would 404. One conversation
	// id captures the whole multi-panel workspace (panels live in its `trees` map).
	//
	// Single direction of control: the URL is the trigger for switching between
	// EXISTING conversations (dropdown select / back-forward / manual edit all
	// just change `?c=`, and the effect below performs the switchTo). create /
	// delete / initial-load set activeId imperatively, then normalize the URL to
	// match — the effect no-ops because the id already equals activeId.
	let convUrlNotice = $state<string | null>(null);
	let convNoticeTimer: ReturnType<typeof setTimeout> | null = null;
	function flashConvNotice(msg: string) {
		convUrlNotice = msg;
		if (convNoticeTimer) clearTimeout(convNoticeTimer);
		convNoticeTimer = setTimeout(() => (convUrlNotice = null), 7000);
	}
	// Returns the goto promise so callers can AWAIT the navigation — `page.url`
	// (which the ?c= sync effect reads) only updates once goto resolves, so a caller
	// that mutates activeId right after must await this or the effect sees a stale URL.
	function setConvUrl(id: string | null, push = false): Promise<void> {
		if (!id) return Promise.resolve();
		const url = new URL(page.url);
		if (url.searchParams.get('c') === id) return Promise.resolve();
		url.searchParams.set('c', id);
		return goto(url, { replaceState: !push, keepFocus: true, noScroll: true });
	}
	$effect(() => {
		const id = page.url.searchParams.get('c');
		if (!id || id === convo.activeId) return;
		// Don't swap mid-stream (mirrors the dropdown guard); the effect re-runs
		// when anyRunning/busy clear and self-heals to whatever the URL now says.
		if (anyRunning || convo.busy) return;
		// Unknown id (e.g. a link from a different scan-root): ignore here — the
		// initial-load path already normalized + notified.
		if (!convo.list.some((c) => c.id === id)) return;
		void convo.switchTo(id);
	});

	// ── Named conversations (dropdown) ────────────────────────────────
	let renamingConv = $state(false);
	let renameDraft = $state('');
	function onSelectConversation(id: string) {
		// convo.busy (own fold in flight) outlives anyRunning — block the swap so an
		// in-flight reply can't land on the newly-selected conversation's tree.
		if (anyRunning || convo.busy || id === convo.activeId) return;
		// Push (not replace) so back/forward navigates between conversations; the
		// $effect above observes the URL change and runs the actual switchTo.
		setConvUrl(id, true);
	}
	function startRenameConversation() {
		renameDraft = convo.list.find((c) => c.id === convo.activeId)?.name ?? '';
		renamingConv = true;
	}
	async function commitRenameConversation() {
		if (convo.activeId && renameDraft.trim()) await convo.rename(convo.activeId, renameDraft.trim());
		renamingConv = false;
	}
	async function onDeleteConversation() {
		if (anyRunning || convo.busy || !convo.activeId) return;
		if (!confirm('Delete this conversation and all its branches?')) return;
		await convo.remove(convo.activeId);
		setConvUrl(convo.activeId, false); // remove resets/advances active → keep URL in sync
	}

	// ── Chat-thread branching: edit / regenerate / delete / cycle / select ──
	// All operate on the per-panel TREE (the source of truth, owned by the convo
	// store). A mutation commits via convo.setTree, which re-derives the active
	// path, mirrors it into shared state (so the CLI follows), and debounce-saves.
	function clearPanelBucket(panel: Panel) {
		// Per-key write only — live.panels is deeply reactive ($state), so this invalidates
		// just THIS panel's readers. No `live.panels = { ...live.panels }` reassign: that
		// would re-render every panel (and churn other panels' chat rows mid-edit). See the
		// `panels` field doc in state.svelte.ts.
		live.panels[panel] = { chat_id: null, label: '', n: 0, samples: [], running: false, error: null };
	}

	/** Fire a (re)generation for one panel, folding the reply under `userParentId`.
	 *  Regenerate respects the current composer prefill, exactly like a fresh send. */
	function fireForPanel(panel: Panel, userParentId: string, messages: ChatMessage[]) {
		const pSel = panelSels.find((x) => x.panel === panel);
		if (!pSel) return;
		const { fireMsgs, prefill } = withPrefill(messages);
		fireOne(pSel, userParentId, fireMsgs, prefill);
	}

	/** Compute a panel's regen plan: plain = fork a sibling; replace = drop the
	 *  active assistant branch first so the fresh reply takes its place. Commits
	 *  any tree pruning and returns the fire target (or null if not regen-able). */
	function regenPlan(
		panel: Panel,
		nodeId: string,
		replace: boolean
	): { userParentId: string; fireMessages: ChatMessage[] } | null {
		const tree = convo.treeFor(panel);
		if (replace) {
			const r = regenReplace(tree, nodeId);
			if (!r) return null;
			convo.setTree(panel, r.tree);
			return { userParentId: r.userParentId, fireMessages: r.fireMessages as ChatMessage[] };
		}
		const rt = regenTarget(tree, nodeId);
		if (!rt) return null;
		return { userParentId: rt.userParentId, fireMessages: rt.fireMessages as ChatMessage[] };
	}

	/** Prune a node + its subtree (one branch). all (shift) = prune EVERY sibling
	 *  branch at this level too, truncating back to the parent. */
	function deleteMessage(panel: Panel, msg: ViewMessage, all = false) {
		if (panelBusy(panel) || msg.nodeId == null) return;
		clearPanelBucket(panel);
		const tree = convo.treeFor(panel);
		convo.setTree(panel, all ? deleteSiblings(tree, msg.nodeId) : deleteSubtree(tree, msg.nodeId));
	}

	/** Regenerate this panel's turn. plain = new sibling branch; replace (shift) =
	 *  overwrite the current branch in place (other siblings kept). */
	function regenerate(panel: Panel, msg: ViewMessage, replace = false) {
		if (panelBusy(panel) || msg.nodeId == null) return;
		clearPanelBucket(panel);
		const plan = regenPlan(panel, msg.nodeId, replace);
		if (!plan) return;
		fireForPanel(panel, plan.userParentId, plan.fireMessages);
	}

	/** Regenerate the turn at this row's DEPTH in EVERY panel (compare mode).
	 *  Matches by active-path depth so each panel re-rolls its own model. */
	function regenerateAll(panel: Panel, msg: ViewMessage, replace = false) {
		if (msg.nodeId == null) return;
		const depth = activePath(convo.treeFor(panel)).findIndex((n) => n.id === msg.nodeId);
		if (depth < 0) return;
		for (const p of panelSels) {
			if (panelBusy(p.panel)) continue;
			const node = activePath(convo.treeFor(p.panel))[depth];
			if (!node) continue;
			clearPanelBucket(p.panel);
			const plan = regenPlan(p.panel, node.id, replace);
			if (plan) fireForPanel(p.panel, plan.userParentId, plan.fireMessages);
		}
	}

	/** Continue (prefill) an assistant turn: re-fire with its content as the trailing
	 *  prefill so the model EXTENDS it. The N continuations land as sibling branches
	 *  (each = the current text + a fresh continuation) you cycle through; the
	 *  original stays as a sibling too. */
	function fireContinue(panel: Panel, nodeId: string) {
		if (panelBusy(panel)) return;
		const tree = convo.treeFor(panel);
		const node = tree.nodes[nodeId];
		if (!node || node.role !== 'assistant' || (!node.content && !node.reasoning)) return;
		const userParentId = node.parent;
		if (!userParentId || tree.nodes[userParentId]?.role !== 'user') return;
		const pSel = panelSels.find((x) => x.panel === panel);
		if (!pSel) return;
		clearPanelBucket(panel);
		// Prefill = the FULL raw turn (reasoning + content reassembled), NOT just
		// content: on auto-<think> families a content-only prefill makes the model
		// read the answer as more thinking. assembleAssistantRaw closes the
		// `<think>` (so the model extends the answer) or leaves it open for a
		// thinking-only turn. ancestryMessages drops prior-turn reasoning (correct —
		// not replayed), so we append this turn's raw prefill ourselves.
		const prefill = assembleAssistantRaw(node.reasoning, node.content);
		const fireMessages = [
			...ancestryMessages(tree, userParentId),
			{ role: 'assistant', content: prefill }
		] as ChatMessage[];
		fireOne(pSel, userParentId, fireMessages, prefill);
	}

	/** "+" continue. plain = this panel; all (shift) = the turn at this row's depth
	 *  in every panel. */
	function continueMessage(panel: Panel, msg: ViewMessage, all = false) {
		if (msg.nodeId == null) return;
		if (!all) { fireContinue(panel, msg.nodeId); return; }
		const depth = activePath(convo.treeFor(panel)).findIndex((n) => n.id === msg.nodeId);
		if (depth < 0) return;
		for (const p of panelSels) {
			const node = activePath(convo.treeFor(p.panel))[depth];
			if (node) fireContinue(p.panel, node.id);
		}
	}

	/** Cycle the active sibling at this row (‹k/N›). */
	function cycleBranch(panel: Panel, msg: ViewMessage, delta: number) {
		if (panelBusy(panel) || msg.nodeId == null) return;
		clearPanelBucket(panel);
		convo.setTree(panel, cycleTree(convo.treeFor(panel), msg.nodeId, delta));
	}

	/** Pick an n>1 sample card as the active branch, then COLLAPSE to the single
	 *  reply view (clear the bucket) — the other samples remain as cyclable ‹k/N›
	 *  siblings in the tree. */
	function selectSample(panel: Panel, msg: ViewMessage, sampleIndex: number) {
		if (panelBusy(panel)) return;
		const nid = msg.sampleNodeIds?.[sampleIndex];
		if (!nid) return;
		convo.setTree(panel, setSelected(convo.treeFor(panel), nid));
		clearPanelBucket(panel); // collapse the distribution view to the chosen branch
	}

	/** Keep this sample, prune all its sibling samples, then collapse to it. */
	function discardOtherSamples(panel: Panel, msg: ViewMessage, sampleIndex: number) {
		if (panelBusy(panel)) return;
		const keep = msg.sampleNodeIds?.[sampleIndex];
		if (!keep) return;
		let tree = setSelected(convo.treeFor(panel), keep);
		for (const sib of siblingsOf(tree, keep)) {
			if (sib !== keep) tree = deleteSubtree(tree, sib);
		}
		convo.setTree(panel, tree);
		clearPanelBucket(panel);
	}

	/** Delete one specific sample branch; drop it from the live cards too so the
	 *  remaining samples stay on screen for further curation. */
	function deleteSample(panel: Panel, msg: ViewMessage, sampleIndex: number) {
		if (panelBusy(panel)) return;
		const nid = msg.sampleNodeIds?.[sampleIndex];
		if (!nid) return;
		convo.setTree(panel, deleteSubtree(convo.treeFor(panel), nid));
		// Remove the card from the bucket overlay (keep the rest visible).
		const bucket = live.panels[panel];
		if (bucket && bucket.samples.length > sampleIndex) {
			const samples = bucket.samples.filter((_, i) => i !== sampleIndex);
			live.panels[panel] = { ...bucket, samples, n: Math.max(1, samples.length) };
			live.panels = { ...live.panels };
		}
	}

	/** Edit → fork. User: fork+regen (shift = fork+copy-downstream, no gen).
	 *  Assistant: a manual branch (no gen). Empty edits are ignored. */
	function applyEdit(
		panel: Panel,
		msg: ViewMessage,
		content: string,
		reasoning: string | undefined,
		copyDownstream: boolean
	) {
		if (panelBusy(panel) || msg.nodeId == null) return;
		const text = content.trim();
		// Assistant turns may be thinking-only (empty answer) — keep the edit if
		// either field has text; user turns still require a non-empty body.
		if (!text && !(msg.role === 'assistant' && reasoning && reasoning.trim())) return;
		clearPanelBucket(panel);
		if (msg.role === 'user') {
			if (copyDownstream) {
				const r = editUserForkCopy(convo.treeFor(panel), msg.nodeId, text);
				if (r) convo.setTree(panel, r.tree);
			} else {
				const r = editUserFork(convo.treeFor(panel), msg.nodeId, text);
				if (!r) return;
				convo.setTree(panel, r.tree);
				fireForPanel(panel, r.newUserId, r.fireMessages as ChatMessage[]);
			}
		} else if (msg.role === 'assistant') {
			const r = editAssistant(convo.treeFor(panel), msg.nodeId, text, reasoning);
			if (r) convo.setTree(panel, r.tree);
		}
	}

	/** Delete the turn at this row's DEPTH in EVERY panel (ctrl/cmd, compare).
	 *  allSiblings (shift) prunes every sibling branch at that level too. */
	function deleteMessageAll(panel: Panel, msg: ViewMessage, allSiblings = false) {
		if (msg.nodeId == null) return;
		const depth = activePath(convo.treeFor(panel)).findIndex((n) => n.id === msg.nodeId);
		if (depth < 0) return;
		for (const p of panelSels) {
			const node = activePath(convo.treeFor(p.panel))[depth];
			if (node) deleteMessage(p.panel, { ...msg, nodeId: node.id }, allSiblings);
		}
	}

	/** Apply the same edit to the turn at this row's DEPTH in EVERY panel (ctrl/cmd,
	 *  compare). copyDownstream (shift, user rows) forks a full copy with no gen. */
	function applyEditAll(
		panel: Panel,
		msg: ViewMessage,
		content: string,
		reasoning: string | undefined,
		copyDownstream: boolean
	) {
		if (msg.nodeId == null) return;
		const depth = activePath(convo.treeFor(panel)).findIndex((n) => n.id === msg.nodeId);
		if (depth < 0) return;
		for (const p of panelSels) {
			const node = activePath(convo.treeFor(p.panel))[depth];
			if (node) applyEdit(p.panel, { ...msg, nodeId: node.id }, content, reasoning, copyDownstream);
		}
	}

	/** Copy to the clipboard: plain = this row's content; all (shift) = the whole
	 *  active conversation rendered as markdown with role headers. */
	function copyMessage(panel: Panel, msg: ViewMessage, all: boolean) {
		let text: string;
		if (all) {
			const msgs = activeMessages(convo.treeFor(panel)) as ChatMessage[];
			const header = (r: string) => (r === 'user' ? 'User' : r === 'assistant' ? 'Assistant' : 'System');
			text = msgs.map((m) => `## ${header(m.role)}\n\n${m.content ?? ''}`).join('\n\n');
		} else {
			text = msg.content ?? '';
		}
		navigator.clipboard?.writeText(text);
	}

	/** Copy a branch's ancestry (root→this node) into ANOTHER panel as a fresh linear
	 *  thread, so you can prompt that panel's model with exactly this context. */
	function sendBranchToPanel(srcPanel: Panel, msg: ViewMessage, destPanel: Panel) {
		if (msg.nodeId == null || destPanel === srcPanel || panelBusy(destPanel)) return;
		const msgs = ancestryMessages(convo.treeFor(srcPanel), msg.nodeId) as ChatMessage[];
		if (!msgs.length) return;
		clearPanelBucket(destPanel);
		convo.setTree(destPanel, treeFromMessages(msgs));
	}

	// ── Conversation rendering ────────────────────────────────────────
	// Each column renders from ITS OWN branch TREE's active path (convo.treeFor(p)) —
	// the single read source (the panel's messages echo is write-only for the CLI).
	// The per-(chat_id,panel) BUCKET (live.panels[panel]) holds
	// the LATEST turn's N variants + streaming progress; we overlay it on the
	// active leaf's assistant turn so the distribution view replaces — never
	// double-renders — the committed reply. After fold, the bucket cards map back
	// to their sibling node ids so a card-click can select that branch.
	/** The bucket's latest turn as a single trailing assistant ViewMessage. `prefill`
	 *  (the panel's last fire) lets the live view color the prefilled prefix too. */
	function bucketTurn(run: (typeof live.panels)[Panel], prefill?: string): ViewMessage {
		const filled = run.samples.filter((x) => x);
		const pf = prefill || undefined;
		if (run.n > 1) {
			return {
				role: 'assistant',
				content: filled[0]?.content ?? '',
				reasoning: filled[0]?.reasoning,
				raw_text: filled[0]?.raw_text,
				raw_meta: filled[0]?.raw_meta,
				prefill: pf,
				samples: run.samples,
				totalSamples: run.n,
				running: run.running
			};
		}
		const one = filled[0];
		return {
			role: 'assistant',
			content: one?.content ?? '',
			reasoning: one?.reasoning,
			raw_text: one?.raw_text,
			raw_meta: one?.raw_meta,
			prefill: pf,
			running: run.running
		};
	}

	function panelView(p: PanelSel): ViewMessage[] {
		const tree = convo.treeFor(p.panel);
		const path = activePath(tree);
		const out: ViewMessage[] = path.map((n) => ({
			role: n.role,
			content: n.content,
			reasoning: n.reasoning,
			raw_text: n.raw_text,
			raw_meta: n.raw_meta,
			prefill: n.prefill,
			nodeId: n.id,
			sib: siblingInfo(tree, n.id),
			isBucket: false
		}));
		const run = live.panels[p.panel] ?? emptyPanel();
		const hasBucket = run.chat_id != null || run.samples.length > 0 || run.running;

		if (hasBucket) {
			let replacedId: string | null = null;
			let replacedSib: { index: number; count: number } | undefined;
			let sampleNodeIds: string[] | undefined;
			let activeSampleIndex: number | undefined;
			if (out.length > 0 && out[out.length - 1].role === 'assistant') {
				// Folded already → replace the trailing assistant with the rich bucket
				// view, and map the n>1 cards back to this batch's sibling node ids.
				const last = out[out.length - 1];
				replacedId = last.nodeId ?? null;
				replacedSib = last.sib;
				out.pop();
				const userParent = replacedId ? tree.nodes[replacedId]?.parent : null;
				if (userParent && tree.nodes[userParent]) {
					const kids = tree.nodes[userParent].children;
					// A sample is "folded" iff it has content AND no error — matching
					// foldAssistant's skip rule. parseSample gives error samples a
					// content string ("Error: …"), so gating on content alone would
					// miscount and misalign the card→node mapping. Error slots map to ''.
					const isFold = (x: (typeof run.samples)[number]) => !!(x && x.content && !x.error);
					const filledCount = run.samples.filter(isFold).length;
					const batch = kids.slice(Math.max(0, kids.length - filledCount)); // this turn's folds
					sampleNodeIds = [];
					let pos = 0;
					for (let i = 0; i < run.samples.length; i++) {
						sampleNodeIds[i] = isFold(run.samples[i]) ? (batch[pos++] ?? '') : '';
					}
					if (replacedId) activeSampleIndex = sampleNodeIds.indexOf(replacedId);
				}
			}
			out.push({
				...bucketTurn(run, firePrefill[p.panel]),
				nodeId: replacedId,
				sib: replacedSib,
				sampleNodeIds,
				activeSampleIndex,
				isBucket: true
			});
		}
		if (run.error) out.push({ role: 'assistant', content: `Error: ${run.error}`, nodeId: null });
		return out;
	}

	// Auto-scroll panels on new content.
	let chatContainers: HTMLDivElement[] = [];
	$effect(() => {
		// Touch reactive deps so this runs on every bus/state update.
		void live.panels; void s.panels;
		for (const el of chatContainers) if (el) el.scrollTop = el.scrollHeight;
	});

	// ── Prompt history (localStorage) ─────────────────────────────────
	const HISTORY_KEY = 'tinkerscope-prompt-history';
	let promptHistory = $state<string[]>([]);
	let historyIndex = $state(-1);
	let historyDraft = $state('');
	let historyBrowsing = $state(false);

	function pushHistory(text: string) {
		if (!promptHistory.length || promptHistory[promptHistory.length - 1] !== text) {
			promptHistory = [...promptHistory, text];
			if (promptHistory.length > 200) promptHistory = promptHistory.slice(-200);
			localStorage.setItem(HISTORY_KEY, JSON.stringify(promptHistory));
		}
		historyIndex = -1; historyDraft = ''; historyBrowsing = false;
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			sendMessage();
		} else if (e.key === 'Escape' && promptHistory.length > 0) {
			e.preventDefault();
			if (!historyBrowsing) {
				historyBrowsing = true; historyDraft = userInput;
				historyIndex = promptHistory.length - 1; userInput = promptHistory[historyIndex];
			} else {
				historyBrowsing = false; historyIndex = -1; userInput = historyDraft;
			}
		} else if (historyBrowsing && e.key === 'ArrowUp' && !e.metaKey) {
			e.preventDefault();
			if (historyIndex > 0) { historyIndex--; userInput = promptHistory[historyIndex]; }
		} else if (historyBrowsing && e.key === 'ArrowDown' && !e.metaKey) {
			e.preventDefault();
			if (historyIndex < promptHistory.length - 1) { historyIndex++; userInput = promptHistory[historyIndex]; }
			else { historyBrowsing = false; historyIndex = -1; userInput = historyDraft; }
		} else if (historyBrowsing) {
			const isTypingKey = e.key.length === 1 || ['Backspace', 'Delete'].includes(e.key);
			if (isTypingKey) { historyBrowsing = false; historyIndex = -1; }
		}
	}

	// ── Input height resize ───────────────────────────────────────────
	let inputHeight = $state(80);
	function startInputResize(e: MouseEvent) {
		e.preventDefault();
		document.body.style.cursor = 'row-resize';
		document.body.style.userSelect = 'none';
		const startY = e.clientY;
		const startHeight = inputHeight;
		function onMove(ev: MouseEvent) { inputHeight = Math.max(40, Math.min(400, startHeight - (ev.clientY - startY))); }
		function onUp() {
			document.body.style.cursor = ''; document.body.style.userSelect = '';
			document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp);
		}
		document.addEventListener('mousemove', onMove);
		document.addEventListener('mouseup', onUp);
	}

	// ── Refresh / load ────────────────────────────────────────────────
	async function loadRuns() {
		try {
			runs = await api.models();
			backendError = '';
		} catch (e: any) {
			backendError = `Failed to load runs: ${e?.message ?? e}`;
		}
	}

	async function refreshModels() {
		refreshingModels = true;
		try {
			await api.refreshModels();
			await loadRuns();
		} catch (e: any) {
			backendError = `Refresh failed: ${e?.message ?? e}`;
		}
		refreshingModels = false;
	}

	// ── OpenRouter model management (UI-driven, no config files) ───────
	async function loadOpenrouterModels() {
		try { openrouterModels = await api.openrouterModels(); } catch (e: any) {
			backendError = `Failed to load OpenRouter models: ${e?.message ?? e}`;
		}
	}

	let showOrManager = $state(false);
	let orAddPanel = $state<Panel>('primary'); // which panel to auto-select the new model into
	let orBusy = $state(false);

	// Lazy-load the full OpenRouter catalog (~341) the first time the manager opens.
	async function loadOrCatalog(refresh = false) {
		orCatalogLoading = true;
		orCatalogError = null;
		try {
			const res = await api.openrouterAvailable(refresh);
			orCatalog = res.models ?? [];
			orCatalogError = res.available === false ? res.error || 'OpenRouter catalog unavailable' : null;
			orCatalogLoaded = true;
		} catch (e: any) {
			orCatalogError = `Failed to load OpenRouter catalog: ${e?.message ?? e}`;
		}
		orCatalogLoading = false;
	}

	function openOrManager(panel: Panel) {
		orAddPanel = panel;
		showOrManager = true;
		if (!orCatalogLoaded) loadOrCatalog();
	}

	// Typeahead pick: persist to the saved quick-list (POST) AND select into the
	// panel that opened the manager.
	async function pickOpenrouterModel(item: { id: string; label: string }) {
		if (orBusy) return;
		orBusy = true;
		try {
			openrouterModels = await api.addOpenrouterModel(item.id, item.label || undefined);
			setRun(orAddPanel, OR_PREFIX + item.id);
			showOrManager = false;
		} catch (e: any) {
			backendError = `Failed to add OpenRouter model: ${e?.message ?? e}`;
		}
		orBusy = false;
	}

	async function removeOpenrouterModel(id: string) {
		if (orBusy) return;
		orBusy = true;
		try {
			openrouterModels = await api.removeOpenrouterModel(id);
			// If a panel was pointing at the removed model, clear its selection
			// back to the first sampleable run so the picker isn't stuck.
			const fallback = runs.find((r) => r.sampleable !== false) ?? runs[0];
			// Any panel pointing at the removed model falls back to the first run.
			for (const p of panelSels) {
				if (openrouterId(p.run_id) === id) setRun(p.panel, fallback?.id ?? '');
			}
		} catch (e: any) {
			backendError = `Failed to remove OpenRouter model: ${e?.message ?? e}`;
		}
		orBusy = false;
	}

	// ── Tinker base model picker (typeahead over /api/tinker-models) ───
	let showTinkerPicker = $state(false);
	let tinkerAddPanel = $state<Panel>('primary');

	async function loadTinkerCatalog() {
		tinkerCatalogLoading = true;
		tinkerCatalogError = null;
		try {
			const res = await api.tinkerModels();
			tinkerModels = res.models ?? [];
			tinkerCatalogError = res.available === false ? res.error || 'Tinker base models unavailable' : null;
			tinkerCatalogLoaded = true;
		} catch (e: any) {
			tinkerCatalogError = `Failed to load tinker base models: ${e?.message ?? e}`;
		}
		tinkerCatalogLoading = false;
	}

	function openTinkerPicker(panel: Panel) {
		tinkerAddPanel = panel;
		showTinkerPicker = true;
		if (!tinkerCatalogLoaded) loadTinkerCatalog();
	}

	// Pick a tinker model from the combined catalog. Look the picked item up by id
	// to recover its `kind`: a checkpoint selects via the ckpt: sentinel (sending
	// {sampler_path}); a base model via the base: sentinel (sending {base_model}).
	// Either way we remember it so it persists in the panel <select> across reloads.
	function pickTinkerModel(item: { id: string; label: string }) {
		const m = tinkerModels.find((t) => t.id === item.id);
		if (m?.kind === 'checkpoint' && m.sampler_path) {
			rememberCheckpoint({ sampler_path: m.sampler_path, label: m.label || m.sampler_path });
			setRun(tinkerAddPanel, CKPT_PREFIX + m.sampler_path);
		} else {
			const bm = m?.base_model ?? item.id;
			rememberBaseModel({ base_model: bm, label: item.label || bm });
			setRun(tinkerAddPanel, BASE_PREFIX + bm);
		}
		showTinkerPicker = false;
	}

	// ── Dataset peek ──────────────────────────────────────────────────
	let showDatasetLoader = $state(false);
	let datasetInitialPath = $state(''); // seeds DatasetModal's path field on open
	let datasetLoading = $state(false);

	function openDatasetLoader() {
		// Prefill with the primary panel's selected run's training dataset.
		const r = runById(panelSels[0]?.run_id);
		datasetInitialPath = r?.dataset_path ?? '';
		showDatasetLoader = true;
	}

	async function loadDataset(path: string, count: number) {
		datasetLoading = true;
		try {
			// The backend signals failures via HTTPException, which api.loadDataset's
			// j<> helper turns into a thrown Error carrying the HTTPException detail —
			// it never returns an in-body {error}. So surface failures from the catch
			// below, not from a dead data.error branch.
			const data = await api.loadDataset(path, count);
			if (data.records && data.records.length > 0) {
				const docs = data.records
					.map((r: any, i: number) => {
						const text = r.text_without_source || r.text || r.question || JSON.stringify(r);
						return `[DOCUMENT ${i + 1}]\n${text}`;
					})
					.join('\n\n');
				userInput = docs;
				showDatasetLoader = false;
			} else {
				backendError = `Dataset loaded but contained no records: ${path}`;
			}
		} catch (e: any) {
			backendError = `Failed to load dataset: ${e?.message ?? e}`;
		}
		datasetLoading = false;
	}

	// ── Pins: saved samples worth keeping (the slideshow) ─────────────
	let pins = $state<Pin[]>([]);
	let showSlideshow = $state(false);
	let slideshowIndex = $state(0);

	let tagFormOpen = $state(false);
	let tagFormLabel = $state('');
	let tagFormResponse = $state('');
	let tagFormReasoning = $state('');
	let tagFormSampleIndex = $state<number | null>(null);
	let tagFormTotalSamples = $state<number | null>(null);
	let tagFormPanel = $state<Panel>('primary');

	async function loadPins() {
		try { pins = (await api.listPins()) as Pin[]; } catch {}
	}

	function lastUserQuestion(): string {
		const msgs = activeMessages(convo.treeFor('primary'));
		for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i].role === 'user') return msgs[i].content;
		return '';
	}

	function openTagForm(panel: Panel, response: string, sampleIndex: number | null, totalSamples: number | null, reasoning = '') {
		tagFormPanel = panel;
		tagFormLabel = live.panels[panel]?.label || panelLabel(panelSels.find((p) => p.panel === panel));
		tagFormResponse = response;
		tagFormReasoning = reasoning;
		tagFormSampleIndex = sampleIndex;
		tagFormTotalSamples = totalSamples;
		tagFormOpen = true;
	}

	/** Persist a pin (shared by the noted form + the shift-click quick path). */
	async function savePin(
		panel: Panel,
		response: string,
		sampleIndex: number | null,
		totalSamples: number | null,
		reasoning: string,
		note: string
	) {
		const p = panelSels.find((x) => x.panel === panel);
		const orId = openrouterId(p?.run_id);
		const bm = baseModelId(p?.run_id);
		const sp = samplerPathOf(p?.run_id);
		const isRef = orId != null || bm != null || sp != null;
		const r = !isRef ? panelRun(p ?? panelSels[0]) : undefined;
		try {
			const entry = await api.createPin({
				label: live.panels[panel]?.label || panelLabel(p),
				// run_id keeps the sentinel for reference models so the pin
				// round-trips; checkpoint is null for OR / base / loose-checkpoint.
				run_id: isRef ? p?.run_id : (r?.id ?? null),
				checkpoint: isRef ? null : (p?.checkpoint ?? null),
				base_model: orId != null ? orId : bm != null ? bm : (r?.base_model ?? null),
				sampler_path: sp,
				dataset_path: r?.dataset_path ?? null,
				question: lastUserQuestion(),
				response,
				reasoning: reasoning || null,
				note: note.trim(),
				sample_index: sampleIndex,
				total_samples: totalSamples,
				temperature: s.temperature,
				max_tokens: s.max_tokens,
				thinking: s.thinking,
				system_prompt: s.system_prompt,
				top_p: s.top_p,
				top_k: topK,
				presence_penalty: presencePenalty,
				repetition_penalty: repetitionPenalty
			});
			pins = [...pins, entry as Pin];
		} catch (e: any) {
			backendError = `Failed to save pin: ${e?.message ?? e}`;
		}
	}

	async function submitTag(note: string) {
		await savePin(tagFormPanel, tagFormResponse, tagFormSampleIndex, tagFormTotalSamples, tagFormReasoning, note);
		tagFormOpen = false;
	}

	/** shift+bookmark: save the pin immediately with no note (skip the form). */
	function quickTag(panel: Panel, response: string, sampleIndex: number | null, totalSamples: number | null, reasoning: string) {
		void savePin(panel, response, sampleIndex, totalSamples, reasoning, '');
	}

	async function deletePin(id: string) {
		try {
			await api.deletePin(id);
			pins = pins.filter((h) => h.id !== id);
			if (slideshowIndex >= pins.length) slideshowIndex = Math.max(0, pins.length - 1);
		} catch {}
	}

	async function openSlideshow() {
		await loadPins();
		pins = [...pins].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
		slideshowIndex = 0;
		showSlideshow = true;
	}
	function slideshowNav(delta: number) {
		if (pins.length === 0) return;
		slideshowIndex = (slideshowIndex + delta + pins.length) % pins.length;
	}

	// ── Distribution chart ────────────────────────────────────────────
	// The pure histogram→bars math + wrapLabel live in $lib/chart; buildChartData
	// below only gathers each panel's samples from reactive state, then delegates.
	let showChart = $state(false);

	function panelLabel(p: PanelSel | undefined): string {
		if (!p) return '';
		if (isOpenrouterSel(p.run_id)) return openrouterLabel(p.run_id);
		if (isBaseSel(p.run_id)) return baseLabel(p.run_id);
		if (isCkptSel(p.run_id)) return ckptLabel(p.run_id);
		const r = runById(p.run_id);
		const name = r?.name ?? p.run_id ?? '?';
		return p.checkpoint ? `${name}@${p.checkpoint}` : name;
	}

	/** Gather each panel's samples (tree siblings, or the live bucket while still
	 *  streaming) and hand them to the pure chart math in $lib/chart. */
	function buildChartData(): ChartData | null {
		const sources: { model: string; samples: string[] }[] = [];
		for (const p of panelSels) {
			// Prefer the active assistant turn's ALL tree siblings (the full
			// distribution across every regen batch), so the chart and the ‹k/N›
			// cycler never disagree. Fall back to the live bucket while first
			// streaming (before the fold).
			const tree = convo.treeFor(p.panel);
			const lastAsst = [...activePath(tree)].reverse().find((n) => n.role === 'assistant');
			let samples: string[] = [];
			if (lastAsst) {
				samples = siblingsOf(tree, lastAsst.id)
					.map((id) => tree.nodes[id]?.content)
					.filter((c): c is string => !!c);
			}
			if (!samples.length) {
				samples = (live.panels[p.panel]?.samples ?? []).filter((x) => x && x.content).map((x) => x.content);
			}
			if (samples.length > 0) sources.push({ model: panelLabel(p), samples });
		}
		return computeChartBars(sources, lastUserQuestion());
	}

	let chartData = $state<ChartData | null>(null);
	function openChart() { chartData = buildChartData(); showChart = true; }

	// ── Health-based degradation banner ───────────────────────────────
	let degraded = $derived.by(() => {
		if (!health) return '';
		if (health.available === false || !health.tinker_key) {
			return health.error
				? `Sampling unavailable: ${health.error}`
				: 'Sampling unavailable (tinker offline or TINKER_API_KEY unset). Runs are listed read-only.';
		}
		return '';
	});

	// ── Lifecycle ─────────────────────────────────────────────────────
	onMount(() => {
		const saved = localStorage.getItem('playground-theme');
		// No saved preference → keep the 'auto' default. The $effect reacts to this
		// assignment and applies the resolved class (no imperative apply needed here).
		if (saved === 'dark' || saved === 'light' || saved === 'auto') themeMode = saved;
		const sv = localStorage.getItem('playground-sample-view');
		if (sv === 'all' || sv === 'cycle') sampleView = sv;
		try {
			const h = localStorage.getItem(HISTORY_KEY);
			if (h) promptHistory = JSON.parse(h);
		} catch {}
		try {
			const rb = localStorage.getItem(RECENT_BASE_KEY);
			if (rb) recentBaseModels = JSON.parse(rb);
		} catch {}
		try {
			const rc = localStorage.getItem(RECENT_CKPT_KEY);
			if (rc) recentCheckpoints = JSON.parse(rc);
		} catch {}

		// Track shift (alternate-action variant) + ctrl/cmd (apply to all panels) for
		// the toolbar affordances. Read off the click too, but mirror for the icon swap.
		const onModKey = (e: KeyboardEvent) => { shiftDown = e.shiftKey; ctrlDown = e.ctrlKey || e.metaKey; };
		const onBlur = () => { shiftDown = false; ctrlDown = false; };
		window.addEventListener('keydown', onModKey);
		window.addEventListener('keyup', onModKey);
		window.addEventListener('blur', onBlur);

		// Open the ONE live-state stream on load + wire the external-fold hooks.
		live.start();
		convo.init();

		(async () => {
			try { health = await api.health(); } catch (e: any) { backendError = `Backend not reachable: ${e?.message ?? e}`; }
			await loadRuns();
			await loadOpenrouterModels();
			try { if (!live.state) live.state = await api.getState(); } catch {}
			// Restore last-used model selection + sampling params from disk (only if
			// this process's state is fresh) BEFORE conversations load, so the right
			// models are selected as the UI comes up.
			await restoreSession();
			// Load conversations right after live.state is ensured (its on-load
			// reconcile reads live.state.messages) and BEFORE anything slow, so the
			// input (gated on convo.activeId) un-gates as early as possible. Honor a
			// `?c=<id>` from the URL (shared/bookmarked link); fall back to newest +
			// notify if it's unknown, then normalize the URL to the opened conv.
			try {
				const urlConvId = page.url.searchParams.get('c');
				const honored = await convo.load(urlConvId);
				if (urlConvId && !honored) flashConvNotice('That conversation was not found here — opened the most recent one instead.');
				setConvUrl(convo.activeId, false);
			} catch (e: any) { backendError = `Failed to load conversations: ${e?.message ?? e}`; }
			await loadPins();
			await loadHighlightRules();
		})();

		return () => {
			live.stop();
			window.removeEventListener('keydown', onModKey);
			window.removeEventListener('keyup', onModKey);
			window.removeEventListener('blur', onBlur);
		};
	});
</script>

<div class="app">
	<header class="topbar">
		<div class="topbar-title">tinkerscope</div>
		{#if health?.root}
			<div class="topbar-root" title={health.root}>{health.root}</div>
		{/if}
		<div class="topbar-status">
			<span class="status-dot" class:ok={live.connected} title={live.connected ? 'Live state connected' : 'Connecting...'}></span>
			<span class="status-text">{live.connected ? 'live' : '…'}</span>
		</div>
	</header>

	{#if degraded}
		<div class="degraded-banner">{degraded}</div>
	{/if}
	{#if convo.externalNotice}
		<div class="degraded-banner external-notice">{convo.externalNotice}</div>
	{/if}
	{#if convUrlNotice}
		<div class="degraded-banner external-notice">{convUrlNotice}</div>
	{/if}

	<div class="main-layout">
		<!-- Sidebar -->
		<aside class="sidebar">
			<div class="sidebar-top-actions">
				<button
					class="theme-toggle"
					onclick={cycleTheme}
					data-tooltip={themeMode === 'auto'
						? 'Theme: auto (follows system) — click for light'
						: themeMode === 'light'
							? 'Theme: light — click for dark'
							: 'Theme: dark — click for auto'}
					use:tip
				>
					{#if themeMode === 'light'}
						<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
							<circle cx="8" cy="8" r="3.5" stroke="currentColor" stroke-width="1.5" />
							<path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
						</svg>
					{:else if themeMode === 'dark'}
						<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
							<path d="M14 9.2A6 6 0 0 1 6.8 2 6 6 0 1 0 14 9.2Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
						</svg>
					{:else}
						<!-- auto: monitor glyph (follows system scheme) -->
						<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
							<rect x="2" y="3" width="12" height="8" rx="1" stroke="currentColor" stroke-width="1.5" />
							<path d="M6 14h4M8 11v3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
						</svg>
					{/if}
				</button>
				<button class="theme-toggle" onclick={openChart} data-tooltip="View response distribution chart (needs samples)" use:tip>
					<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
						<rect x="2" y="9" width="3" height="5" rx="0.5" stroke="currentColor" stroke-width="1.3" />
						<rect x="6.5" y="5" width="3" height="9" rx="0.5" stroke="currentColor" stroke-width="1.3" />
						<rect x="11" y="2" width="3" height="12" rx="0.5" stroke="currentColor" stroke-width="1.3" />
					</svg>
				</button>
				<button class="theme-toggle" onclick={openSlideshow} data-tooltip="Browse saved pins ({pins.length} saved)" use:tip>
					<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
						<path d="M3 2.5h10a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5H3A1.5 1.5 0 0 1 1.5 12V4A1.5 1.5 0 0 1 3 2.5Z" stroke="currentColor" stroke-width="1.3" />
						<path d="M6.5 6l4 2-4 2V6Z" fill="currentColor" />
					</svg>
				</button>
				<button class="theme-toggle" onclick={openDatasetLoader} data-tooltip="Peek at the selected run's training data" use:tip>
					<svg width="14" height="14" viewBox="0 0 14 14" fill="none">
						<path d="M3 1.5h5l3.5 3.5v7.5a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.3" />
						<path d="M8 1.5v3.5h3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
						<path d="M5 8h4M5 10h3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
					</svg>
				</button>
				<button class="theme-toggle" class:refreshing={refreshingModels} onclick={refreshModels} data-tooltip="Rescan filesystem for runs" use:tip disabled={refreshingModels}>
					<svg width="14" height="14" viewBox="0 0 14 14" fill="none">
						<path d="M1.5 7a5.5 5.5 0 0 1 9.9-3.3M12.5 7a5.5 5.5 0 0 1-9.9 3.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
						<path d="M11.5 1v3h-3M2.5 13v-3h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
					</svg>
				</button>
				<button class="btn-stop-sidebar" class:active={anyRunning} onclick={() => stopGeneration()} data-tooltip="Stop all generation" use:tip disabled={!anyRunning}>
					<svg width="14" height="14" viewBox="0 0 14 14" fill="none">
						<rect x="2" y="2" width="10" height="10" rx="1.5" fill="currentColor" />
					</svg>
				</button>
			</div>

			{#if backendError}
				<div class="backend-error">{backendError}</div>
			{/if}

			<!-- Conversation picker (named, branchable) -->
			<div class="sidebar-section">
				<label class="sidebar-label">Conversation</label>
				{#if renamingConv}
					<!-- svelte-ignore a11y_autofocus -->
					<input
						class="sidebar-input"
						bind:value={renameDraft}
						autofocus
						onkeydown={(e) => {
							if (e.key === 'Enter') { e.preventDefault(); commitRenameConversation(); }
							else if (e.key === 'Escape') { renamingConv = false; }
						}}
						onblur={commitRenameConversation}
					/>
				{:else}
					<div class="conv-row">
						<select
							class="sidebar-select conv-select"
							value={convo.activeId ?? ''}
							disabled={anyRunning || convo.busy}
							onchange={(e) => onSelectConversation((e.target as HTMLSelectElement).value)}
						>
							{#each convo.list as c (c.id)}
								<option value={c.id}>{c.name || 'Untitled'}</option>
							{/each}
						</select>
						<button class="conv-icon-btn" class:shift-alt={shiftDown} title={shiftDown ? 'New BLANK conversation (no model selected)' : 'New conversation (keeps the current models; Shift+click for a blank one)'} disabled={anyRunning || convo.busy} aria-label="New conversation" onclick={newConversation}>
							{#if shiftDown}
								<!-- blank-page + plus: a fresh conversation with no model -->
								<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M4 1.5h5L12.5 5v6.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /><path d="M8.5 1.5V5h3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /><path d="M7.5 7v3M6 8.5h3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /></svg>
							{:else}
								<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M8 4v8M4 8h8" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
							{/if}
						</button>
						<button class="conv-icon-btn" title="Rename conversation" disabled={anyRunning || convo.busy} aria-label="Rename conversation" onclick={startRenameConversation}>
							<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M10.5 2.5l3 3L6 13l-3.5.5L3 10l7.5-7.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /></svg>
						</button>
						<button class="conv-icon-btn conv-icon-danger" title="Delete conversation" disabled={anyRunning || convo.busy} aria-label="Delete conversation" onclick={onDeleteConversation}>
							<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V2.5h4V4M4.5 4l.6 9h5.8l.6-9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
						</button>
					</div>
				{/if}
			</div>

			<!-- Model picker: run + checkpoint, per panel -->
			<div class="sidebar-section">
				<label class="sidebar-label">Models</label>
				{#if runs.length + openrouterModels.length + recentBaseModels.length + recentCheckpoints.length > 4}
					<input
						class="sidebar-input model-filter"
						type="text"
						placeholder="Filter models…"
						bind:value={modelFilter}
					/>
				{/if}
				{#each panelSels as p (p.panel)}
					{@const pr = runById(p.run_id)}
					{@const isOr = isOpenrouterSel(p.run_id)}
					{@const isBase = isBaseSel(p.run_id)}
					{@const isCkpt = isCkptSel(p.run_id)}
					{@const orModel = openrouterBySel(p.run_id)}
					{@const baseM = baseModelId(p.run_id)}
					{@const sp = samplerPathOf(p.run_id)}
					{@const fRuns = runs.filter((r) => r.id === p.run_id || matchModel(runLabel(r), r.id, r.base_model, r.wandb_project, r.renderer_name))}
					{@const fBase = recentBaseModels.filter((t) => matchModel(t.label, t.base_model))}
					{@const fCkpt = recentCheckpoints.filter((t) => matchModel(t.label, t.sampler_path))}
					{@const fOr = openrouterModels.filter((m) => matchModel(m.label, m.openrouter_model))}
					<!-- A selected base model / checkpoint that isn't in recents yet (e.g.
					     came from the CLI / shared state) still needs an <option> so the
					     select shows it. -->
					{@const baseInRecents = baseM != null && recentBaseModels.some((t) => t.base_model === baseM)}
					{@const ckptInRecents = sp != null && recentCheckpoints.some((t) => t.sampler_path === sp)}
					<div class="model-block">
						<div class="model-slot-row">
							<select
								class="sidebar-select model-slot-select"
								value={p.run_id ?? ''}
								onchange={(e) => setRun(p.panel, (e.target as HTMLSelectElement).value)}
							>
								{#if !p.run_id}
									<option value="" disabled>Select a model…</option>
								{/if}
								{#if fRuns.length > 0}
									<optgroup label="Runs">
										{#each fRuns as r (r.id)}
											<option value={r.id} disabled={r.sampleable === false}>
												{r.sampleable === false ? '⊘ ' : r.sampleable === null ? '? ' : ''}{runLabel(r)}
											</option>
										{/each}
									</optgroup>
								{/if}
								{#if fBase.length > 0 || (baseM != null && !baseInRecents)}
									<optgroup label="Tinker base models">
										{#if baseM != null && !baseInRecents}
											<option value={BASE_PREFIX + baseM}>◆ {baseLabel(p.run_id)}</option>
										{/if}
										{#each fBase as t (t.base_model)}
											<option value={BASE_PREFIX + t.base_model}>◆ {t.label || t.base_model}</option>
										{/each}
									</optgroup>
								{/if}
								{#if fCkpt.length > 0 || (sp != null && !ckptInRecents)}
									<optgroup label="Tinker checkpoints">
										{#if sp != null && !ckptInRecents}
											<option value={CKPT_PREFIX + sp}>◇ {ckptLabel(p.run_id)}</option>
										{/if}
										{#each fCkpt as t (t.sampler_path)}
											<option value={CKPT_PREFIX + t.sampler_path}>◇ {t.label || t.sampler_path}</option>
										{/each}
									</optgroup>
								{/if}
								{#if fOr.length > 0}
									<optgroup label="OpenRouter">
										{#each fOr as m (m.openrouter_model)}
											<option value={OR_PREFIX + m.openrouter_model}>↗ {m.label || m.openrouter_model}</option>
										{/each}
									</optgroup>
								{/if}
								{#if fRuns.length + fBase.length + fCkpt.length + fOr.length === 0}
									<option value={p.run_id ?? ''} disabled>No models match “{modelFilter}”</option>
								{/if}
							</select>
							{#if p.panel !== 'primary'}
								<button class="btn-remove-model" onclick={() => removePanel(p.panel)} title="Remove this panel">
									<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 8h8" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
								</button>
							{/if}
						</div>
						{#if isBase}
							<!-- Raw tinker base model: no checkpoint selector (no LoRA). -->
							<div class="run-meta or-meta">◆ {baseM} · raw base (no LoRA)</div>
							{#if health && !health.tinker_key}
								<div class="unsampleable-note">Set TINKER_API_KEY to sample this base model.</div>
							{/if}
						{:else if isCkpt}
							<!-- Loose tinker sampler checkpoint: no checkpoint selector. -->
							<div class="run-meta or-meta" title={sp}>◇ {ckptLabel(p.run_id)} · loose sampler</div>
							{#if health && !health.tinker_key}
								<div class="unsampleable-note">Set TINKER_API_KEY to sample this checkpoint.</div>
							{/if}
						{:else if isOr}
							<!-- OpenRouter reference model: no checkpoint selector. -->
							<div class="run-meta or-meta">↗ {orModel?.openrouter_model ?? openrouterId(p.run_id)}</div>
							{#if health && health.openrouter_key === false}
								<div class="unsampleable-note">Set OPENROUTER_API_KEY to sample this model.</div>
							{/if}
						{:else}
							{#if pr && pr.checkpoints.length > 0}
								<select
									class="sidebar-select ckpt-select"
									value={p.checkpoint ?? ''}
									onchange={(e) => setCheckpoint(p.panel, (e.target as HTMLSelectElement).value)}
								>
									{#each pr.checkpoints as ck (ck.name)}
										{@const numbered = /^\d+$/.test(ck.name)}
										<option value={ck.name}>{numbered ? `step ${ck.step}` : ck.name}{ck.epoch != null ? ` · e${ck.epoch}·b${ck.batch ?? 0}` : ''}</option>
									{/each}
								</select>
							{/if}
							{#if pr?.sampleable === false && pr.unsampleable_reason}
								<div class="unsampleable-note">{pr.unsampleable_reason}</div>
							{:else if pr?.sampleable === null}
								<div class="unknown-note">capabilities unknown (tinker offline / no key)</div>
							{/if}
							{#if pr?.config_error}
								<div class="config-error-note">config error: {pr.config_error}</div>
							{/if}
							{#if pr}
								<div class="run-meta">{pr.base_model}{pr.lora_rank ? ` · LoRA ${pr.lora_rank}` : ''}{pr.num_checkpoints ? ` · ${pr.num_checkpoints} ckpts` : ''}</div>
							{/if}
						{/if}
						<div class="add-model-links">
							<button class="or-manage-link" onclick={() => openTinkerPicker(p.panel)}>+ Tinker model</button>
							<button class="or-manage-link" onclick={() => openOrManager(p.panel)}>+ OpenRouter model</button>
						</div>
					</div>
				{/each}
				{#if panelSels.length < MAX_PANELS}
					<button class="btn-add-model" onclick={addPanel} disabled={runs.length + openrouterModels.length < 1}>
						<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M8 4v8M4 8h8" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
						{panelSels.length < 2 ? 'Compare' : 'Add panel'}
					</button>
				{/if}
			</div>

			<div class="sidebar-section">
				<label class="sidebar-label">Temperature: {s.temperature.toFixed(2)}</label>
				<input type="range" min="0" max="2" step="0.05" value={s.temperature} oninput={(e) => setTemperature(parseFloat((e.target as HTMLInputElement).value))} class="sidebar-slider" />
			</div>

			<div class="sidebar-section">
				<label class="sidebar-label">Max tokens</label>
				<input type="number" value={s.max_tokens} min="1" max="32000" class="sidebar-input" oninput={(e) => setMaxTokens(parseInt((e.target as HTMLInputElement).value))} />
			</div>

			<div class="sidebar-section">
				<label class="sidebar-label">Samples</label>
				<input type="number" value={s.n_samples} min="1" max="200" class="sidebar-input"
					oninput={(e) => setNSamples(parseInt((e.target as HTMLInputElement).value))}
					onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') { e.preventDefault(); inputTextarea?.focus(); } }} />
			</div>

			<div class="sidebar-section">
				<label class="sidebar-label thinking-toggle-row">
					<span>Sample view</span>
					<span class="seg-toggle" data-tooltip="How an n>1 distribution renders: All = every card stacked (scroll); Cycle = one card at a time with ‹/›" use:tip>
						<button class="seg-btn" class:active={sampleView === 'all'} onclick={() => setSampleView('all')}>All</button>
						<button class="seg-btn" class:active={sampleView === 'cycle'} onclick={() => setSampleView('cycle')}>Cycle</button>
					</span>
				</label>
			</div>

			{#if anySupportsThinking}
				<div class="sidebar-section">
					<label class="sidebar-label thinking-toggle-row">
						<span>Thinking</span>
						<button class="thinking-pill" class:active={s.thinking} onclick={toggleThinking}>{s.thinking ? 'ON' : 'OFF'}</button>
					</label>
				</div>
			{/if}

			<div class="sidebar-section">
				<button class="advanced-toggle" onclick={() => (showSamplingPopup = true)}>Sampling params&hellip;</button>
			</div>

			{#if showSamplingPopup}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="sampling-overlay" onclick={() => (showSamplingPopup = false)} onkeydown={(e) => { if (e.key === 'Escape') showSamplingPopup = false; }}>
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div class="sampling-popup" onclick={(e) => e.stopPropagation()}>
						<div class="sampling-popup-header">
							<span>Sampling parameters</span>
							<button class="sampling-popup-close" onclick={() => (showSamplingPopup = false)}>&times;</button>
						</div>
						<div class="sampling-popup-body">
							<div class="advanced-param-row">
								<label>top_p: {(s.top_p ?? 0).toFixed(2)}</label>
								<input type="range" min="0" max="1" step="0.05" value={s.top_p ?? 0.8} oninput={(e) => setTopP(parseFloat((e.target as HTMLInputElement).value))} class="sidebar-slider" />
							</div>
							<div class="advanced-param-row">
								<label>top_k (OpenRouter only)</label>
								<input type="number" bind:value={topK} min="-1" max="200" class="sidebar-input" style="width: 70px;" class:param-limited={true} />
							</div>
							<div class="advanced-param-row">
								<label>presence_penalty: {presencePenalty.toFixed(1)} (OpenRouter only)</label>
								<input type="range" min="0" max="2" step="0.1" bind:value={presencePenalty} class="sidebar-slider param-limited" />
							</div>
							<div class="advanced-param-row">
								<label>repetition_penalty: {repetitionPenalty.toFixed(1)} (OpenRouter only)</label>
								<input type="range" min="1" max="2" step="0.1" bind:value={repetitionPenalty} class="sidebar-slider param-limited" />
							</div>
							<p class="tinker-note">Tinker models only support temperature and top_p. Other parameters apply to OpenRouter reference models only.</p>
							<button class="reset-defaults-btn" onclick={() => applyQwenDefaults(s.thinking)}>Reset to Qwen defaults</button>
						</div>
					</div>
				</div>
			{/if}

			<div class="sidebar-section">
				<label class="sidebar-label">System prompt</label>
				<textarea class="sidebar-textarea" value={s.system_prompt ?? ''} oninput={(e) => setSystemPrompt((e.target as HTMLTextAreaElement).value)} rows="4" placeholder="Optional system prompt..."></textarea>
			</div>

			<div class="sidebar-section">
				<HighlightRules />
			</div>
		</aside>

		<!-- Chat area -->
		<div class="chat-area">
			<div class="chat-columns" class:multi={isComparing}>
				{#each panelSels as p, panelIdx (p.panel)}
					{#if convo.reducedPanels.has(p.panel)}
						<div class="chat-column reduced">
							<button class="restore-panel" onclick={() => convo.restorePanel(p.panel)} title="Restore this panel">
								<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
								<span class="restore-label">{panelLabel(p)}</span>
							</button>
						</div>
					{:else}
					{@const view = panelView(p)}
					{@const run = live.panels[p.panel] ?? emptyPanel()}
					<div class="chat-column">
						{#if isComparing}
							<div class="column-header">
								<span class="column-title">{panelLabel(p)}</span>
								<button class="reduce-panel" onclick={() => convo.reducePanel(p.panel)} title="Reduce this panel" aria-label="Reduce panel">
									<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 8h8" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
								</button>
							</div>
						{/if}
						<div class="messages" bind:this={chatContainers[panelIdx]}>
							{#each view as msg, i (msg.nodeId ?? 'b' + i)}
								{@const isLastAssistant = !view.slice(i + 1).some((m) => m.role === 'assistant')}
								<Message
									{msg}
									{isLastAssistant}
									{shiftDown}
									{ctrlDown}
									busy={panelBusy(p.panel)}
									showRegenAll={isComparing}
									thinking={s.thinking}
									{sampleView}
									onRegenerate={(allPanels, replace) => (allPanels ? regenerateAll(p.panel, msg, replace) : regenerate(p.panel, msg, replace))}
									onContinue={(allPanels) => continueMessage(p.panel, msg, allPanels)}
									onDelete={(allPanels, allSiblings) => (allPanels ? deleteMessageAll(p.panel, msg, allSiblings) : deleteMessage(p.panel, msg, allSiblings))}
									onSelectSample={(idx) => selectSample(p.panel, msg, idx)}
									onDiscardOthers={(idx) => discardOtherSamples(p.panel, msg, idx)}
									onDeleteSample={(idx) => deleteSample(p.panel, msg, idx)}
									onEdit={(content, reasoning, copyDownstream, allPanels) => (allPanels ? applyEditAll(p.panel, msg, content, reasoning, copyDownstream) : applyEdit(p.panel, msg, content, reasoning, copyDownstream))}
									onCopy={(all) => copyMessage(p.panel, msg, all)}
									otherPanels={panelSels.filter((x) => x.panel !== p.panel).map((x) => ({ id: x.panel, label: panelLabel(x) }))}
									onSendToPanel={(dest) => sendBranchToPanel(p.panel, msg, dest)}
									onCycle={(delta) => cycleBranch(p.panel, msg, delta)}
									onTag={(content, sampleIndex, totalSamples, reasoning, quick) => quick ? quickTag(p.panel, content, sampleIndex, totalSamples, reasoning) : openTagForm(p.panel, content, sampleIndex, totalSamples, reasoning)}
								/>
							{/each}
							{#if run.running && run.n <= 1 && run.samples.filter((x) => x && (x.content || x.reasoning)).length === 0}
								<div class="message" style="background: {s.thinking ? 'var(--color-surface-alt)' : 'var(--color-assistant-bg)'};">
									<div class="message-role">{s.thinking ? 'thinking' : 'assistant'}</div>
									<div class="message-content loading-indicator"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
								</div>
							{/if}
							{#if isComparing && convo.activeId && panelCanChat(p)}
								<!-- Per-panel composer: continue ONLY this panel, independent of the
								     other panel and of whatever it's doing. -->
								<div class="panel-send">
									<input
										class="panel-send-input"
										placeholder={panelBusy(p.panel) ? 'generating…' : '＋ continue this panel'}
										bind:value={panelDraft[p.panel]}
										disabled={panelBusy(p.panel)}
										onkeydown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendToPanel(p.panel); } }}
									/>
									<button
										class="panel-send-btn"
										aria-label="Send to this panel"
										disabled={panelBusy(p.panel) || !(panelDraft[p.panel] ?? '').trim()}
										onclick={() => sendToPanel(p.panel)}
									>
										<svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M2 8h10M8 4l4 4-4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
									</button>
								</div>
							{/if}
						</div>
					</div>
					{/if}
				{/each}
			</div>

			<!-- Input bar -->
			<div class="input-bar">
				{#if panelSels.length > 1}
					<div class="send-targets">
						<span class="send-targets-label">Send to</span>
						{#each panelSels as p (p.panel)}
							<button class="send-chip" class:on={convo.sendTargets.has(p.panel)} class:reduced={convo.reducedPanels.has(p.panel)} onclick={() => convo.toggleSendTarget(p.panel)} title={convo.reducedPanels.has(p.panel) ? 'Reduced panel — click to send here anyway' : 'Toggle this panel as a send target'}>{panelLabel(p)}</button>
						{/each}
					</div>
				{/if}
				<!-- Assistant prefill: the model continues from this text. -->
				<div class="prefill-row">
					<button
						class="prefill-toggle"
						class:on={prefillActive}
						onclick={() => (showPrefill = !showPrefill)}
						data-tooltip="Prefill the assistant turn — the model continues from your text. Type raw <think> tags (Qwen/Kimi: open it yourself; DeepSeek auto-opens it). Collapse to disable (text is kept); click again to restore."
						use:tip
					>{prefillActive ? '✎ prefill on' : '＋ prefill assistant'}</button>
					{#if prefillInput.trim() && !showPrefill}
						<button class="prefill-peek" title="Prefill off — click to restore: {prefillInput}" onclick={() => (showPrefill = true)}>{prefillInput.replace(/\s+/g, ' ').slice(0, 80)}{prefillInput.length > 80 ? '…' : ''}</button>
					{/if}
					{#if prefillInput.length > 0}
						<button class="prefill-clear" onclick={() => { prefillInput = ''; showPrefill = false; }}>clear</button>
					{/if}
				</div>
				{#if showPrefill}
					<textarea
						class="prefill-textarea"
						bind:value={prefillInput}
						rows="3"
						placeholder={'Assistant prefill — the model EXTENDS this. Raw format, e.g.\n  <think>\\nLet me reason…            (only some thinking — model keeps going inside)\n  <think>\\nfull reasoning\\n</think>\\n\\n   (full thinking, model writes the answer)\nDeepSeek auto-opens <think>, so omit it there.'}
					></textarea>
				{/if}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="input-resize-handle" onmousedown={startInputResize}></div>
				<textarea
					class="input-textarea"
					class:history-mode={historyBrowsing}
					style="height: {inputHeight}px;"
					bind:value={userInput}
					bind:this={inputTextarea}
					onkeydown={handleKeydown}
					placeholder={!convo.activeId
						? 'Loading conversations…'
						: !canChat
							? 'Select a sampleable run to chat'
							: historyBrowsing
								? 'History mode -- up/down browse, Esc exit'
								: 'Type a message... (Enter to send, Esc for history)'}
					disabled={allBusy || !canChat || !convo.activeId}
				></textarea>
			</div>
		</div>
	</div>
</div>

<!-- Chart Modal -->
{#if showChart}
	<ChartModal data={chartData} onclose={() => (showChart = false)} />
{/if}

<!-- Tag Form Modal -->
{#if tagFormOpen}
	<TagModal label={tagFormLabel} response={tagFormResponse} onsubmit={submitTag} onclose={() => (tagFormOpen = false)} />
{/if}

<!-- Slideshow Modal -->
{#if showSlideshow}
	<SlideshowModal pins={pins} index={slideshowIndex} onnav={slideshowNav} ondelete={deletePin} onclose={() => (showSlideshow = false)} />
{/if}

<!-- Dataset Loader Modal -->
{#if showDatasetLoader}
	<DatasetModal initialPath={datasetInitialPath} loading={datasetLoading} onsubmit={loadDataset} onclose={() => (showDatasetLoader = false)} />
{/if}

<!-- OpenRouter Manager Modal -->
{#if showOrManager}
	<OrManagerModal
		models={openrouterModels}
		catalog={orCatalog}
		loading={orCatalogLoading}
		error={orCatalogError}
		busy={orBusy}
		keyMissing={health?.openrouter_key === false}
		onpick={pickOpenrouterModel}
		onremove={removeOpenrouterModel}
		onrefresh={() => loadOrCatalog(true)}
		onclose={() => (showOrManager = false)}
	/>
{/if}

<!-- Tinker base model picker (typeahead over /api/tinker-models) -->
{#if showTinkerPicker}
	<TinkerPickerModal
		models={tinkerModels}
		loading={tinkerCatalogLoading}
		error={tinkerCatalogError}
		keyMissing={!!health && !health.tinker_key}
		onpick={pickTinkerModel}
		onclose={() => (showTinkerPicker = false)}
	/>
{/if}

<!-- Tooltip -->
{#if tooltip.visible}
	<div class="tooltip-instant" style="left: {tooltip.x}px; top: {tooltip.y}px;">{tooltip.text}</div>
{/if}

<style>
	.app { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

	/* ── Top bar ───────────────────────────────────────────────────── */
	.topbar { display: flex; align-items: center; gap: var(--space-4); padding: var(--space-3) var(--space-5); background: var(--color-surface); border-bottom: 1px solid var(--color-border); flex-shrink: 0; }
	.topbar-title { font-weight: 600; font-size: 1rem; color: var(--color-accent); }
	.topbar-root { font-size: 0.72rem; color: var(--color-text-muted); font-family: var(--font-mono); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
	.topbar-status { display: flex; align-items: center; gap: 6px; margin-left: auto; }
	.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-text-muted); opacity: 0.4; }
	.status-dot.ok { background: #22c55e; opacity: 1; }
	.status-text { font-size: 0.72rem; color: var(--color-text-muted); }

	/* ── Degraded banner ───────────────────────────────────────────── */
	.degraded-banner { font-size: 0.8rem; color: #b45309; background: #fef3c7; padding: var(--space-2) var(--space-5); border-bottom: 1px solid #f59e0b40; flex-shrink: 0; }
	:global(.dark) .degraded-banner { color: #fbbf24; background: #78350f40; border-color: #f59e0b30; }

	/* ── Main layout ───────────────────────────────────────────────── */
	.main-layout { display: flex; flex: 1; overflow: hidden; }

	/* ── Sidebar ───────────────────────────────────────────────────── */
	.sidebar { width: var(--sidebar-width); flex-shrink: 0; background: var(--color-surface); border-right: 1px solid var(--color-border); padding: var(--space-4); overflow-y: auto; display: flex; flex-direction: column; gap: var(--space-4); }
	.sidebar-section { display: flex; flex-direction: column; gap: var(--space-2); }
	.sidebar-select { padding: var(--space-2) var(--space-3); background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text); font-size: 0.82rem; }
	.sidebar-select option:disabled { color: var(--color-text-muted); }
	.sidebar-slider { width: 100%; accent-color: var(--color-accent); }
	.model-filter { margin-bottom: var(--space-2); }
	.sidebar-textarea { padding: var(--space-2) var(--space-3); background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text); font-size: 0.82rem; font-family: var(--font-sans); resize: vertical; width: 100%; }
	.sidebar-top-actions { display: flex; gap: var(--space-2); align-items: center; flex-wrap: wrap; }

	/* ── Conversation picker ───────────────────────────────────────── */
	.conv-row { display: flex; gap: var(--space-1); align-items: center; }
	.conv-select { flex: 1; min-width: 0; }
	.conv-icon-btn { display: flex; align-items: center; justify-content: center; width: 28px; height: 28px; padding: 0; background: var(--color-surface-hover); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text-muted); flex-shrink: 0; cursor: pointer; }
	.conv-icon-btn:hover:not(:disabled) { color: var(--color-accent); border-color: var(--color-accent); background: var(--color-accent-bg); }
	.conv-icon-btn:disabled { opacity: 0.4; cursor: not-allowed; }
	/* Shift held → the New button signals its alternate action (blank conversation). */
	.conv-icon-btn.shift-alt:not(:disabled) { color: var(--color-accent); border-color: var(--color-accent); background: var(--color-accent-bg); }
	.conv-icon-danger:hover:not(:disabled) { color: white; background: #d97070; border-color: #d97070; }
	.external-notice { color: var(--color-accent); background: var(--color-accent-bg); border-color: var(--color-accent); }

	/* ── Model picker ──────────────────────────────────────────────── */
	.model-block { display: flex; flex-direction: column; gap: var(--space-1); padding-bottom: var(--space-2); margin-bottom: var(--space-2); border-bottom: 1px solid var(--color-border-light); }
	.model-block:last-of-type { border-bottom: none; margin-bottom: 0; }
	.model-slot-row { display: flex; gap: var(--space-2); align-items: center; }
	.model-slot-select { flex: 1; min-width: 0; }
	.ckpt-select { width: 100%; font-family: var(--font-mono); font-size: 0.76rem; }
	.run-meta { font-size: 0.68rem; color: var(--color-text-muted); font-family: var(--font-mono); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.unknown-note { font-size: 0.68rem; color: var(--color-text-muted); font-style: italic; }
	.config-error-note { font-size: 0.68rem; color: #ef4444; line-height: 1.3; }
	.btn-remove-model { display: flex; align-items: center; justify-content: center; width: 28px; height: 28px; padding: 0; background: var(--color-surface-hover); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text-muted); flex-shrink: 0; }
	.btn-remove-model:hover { background: #d97070; border-color: #d97070; color: white; }
	.btn-add-model { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3); background: var(--color-surface-hover); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text-secondary); font-size: 0.78rem; font-weight: 500; width: 100%; justify-content: center; }
	.btn-add-model:hover:not(:disabled) { background: var(--color-accent-bg); border-color: var(--color-accent); color: var(--color-accent); }
	.btn-add-model:disabled { opacity: 0.4; cursor: not-allowed; }

	/* ── OpenRouter / Tinker picker affordances ────────────────────── */
	.add-model-links { display: flex; gap: var(--space-3); flex-wrap: wrap; padding-top: 2px; }
	.or-manage-link { align-self: flex-start; background: none; border: none; padding: 0; cursor: pointer; font-size: 0.7rem; color: var(--color-text-muted); font-weight: 500; }
	.or-manage-link:hover { color: var(--color-accent); }
	.or-meta { color: var(--color-text-secondary); }
	.theme-toggle { background: none; border: 1px solid var(--color-border); border-radius: var(--radius); padding: 6px; color: var(--color-text-muted); display: flex; align-items: center; }
	.theme-toggle:hover { color: var(--color-text); border-color: var(--color-text-muted); }
	.theme-toggle.refreshing { opacity: 0.5; cursor: wait; }
	.theme-toggle.refreshing svg { animation: spin 0.8s linear infinite; }
	@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

	/* ── Chat area ─────────────────────────────────────────────────── */
	.chat-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
	.chat-columns { flex: 1; display: flex; overflow-x: auto; }
	.chat-columns.multi { gap: 1px; background: var(--color-border); }
	.chat-column { flex: 1; min-width: 280px; display: flex; flex-direction: column; overflow: hidden; background: var(--color-bg); }
	.chat-column.reduced { flex: 0 0 auto; min-width: 0; }
	.restore-panel { display: flex; align-items: center; gap: var(--space-2); height: 100%; padding: var(--space-2) var(--space-3); writing-mode: vertical-rl; background: var(--color-surface); border: none; border-right: 1px solid var(--color-border); color: var(--color-text-muted); cursor: pointer; font-size: 0.72rem; font-weight: 600; }
	.restore-panel:hover { color: var(--color-accent); background: var(--color-surface-alt); }
	.restore-label { max-height: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.column-header { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-4); font-size: 0.78rem; font-weight: 600; color: var(--color-accent); background: var(--color-surface); border-bottom: 1px solid var(--color-border); }
	.column-title { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.reduce-panel { display: flex; align-items: center; padding: 2px; background: none; border: 1px solid transparent; border-radius: var(--radius-sm); color: var(--color-text-muted); cursor: pointer; flex-shrink: 0; }
	.reduce-panel:hover { color: var(--color-accent); border-color: var(--color-border); }
	/* ── Composer send-targeting chips ─────────────────────────────── */
	.send-targets { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); padding: var(--space-2) 0 0; }
	.send-targets-label { font-size: 0.68rem; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.04em; }
	.send-chip { font-size: 0.7rem; padding: 2px 8px; border: 1px solid var(--color-border); border-radius: var(--radius-pill); background: var(--color-bg); color: var(--color-text-muted); cursor: pointer; max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.send-chip.on { background: var(--color-accent-bg); border-color: var(--color-accent); color: var(--color-accent); }
	.send-chip.reduced { opacity: 0.6; font-style: italic; }
	/* ── Assistant prefill field ───────────────────────────────────── */
	.prefill-row { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) 0 0; }
	.prefill-toggle { font-size: 0.7rem; padding: 2px 8px; border: 1px solid var(--color-border); border-radius: var(--radius-pill); background: var(--color-bg); color: var(--color-text-muted); cursor: pointer; flex-shrink: 0; }
	.prefill-toggle.on { background: var(--color-accent-bg); border-color: var(--color-accent); color: var(--color-accent); }
	.prefill-peek { font-size: 0.68rem; color: var(--color-text-muted); font-family: var(--font-mono, monospace); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; border: none; background: none; text-align: left; cursor: pointer; padding: 0; opacity: 0.75; }
	.prefill-peek:hover { color: var(--color-accent); opacity: 1; }
	.prefill-clear { font-size: 0.66rem; padding: 1px 6px; border: 1px solid transparent; border-radius: var(--radius-sm); background: none; color: var(--color-text-muted); cursor: pointer; flex-shrink: 0; }
	.prefill-clear:hover { color: var(--color-accent); border-color: var(--color-border); }
	.prefill-textarea { width: 100%; margin-top: var(--space-2); padding: var(--space-2) var(--space-3); background: var(--color-bg); border: 1px solid var(--color-accent); border-radius: var(--radius); color: var(--color-text); font-family: var(--font-mono, monospace); font-size: 0.8rem; line-height: 1.45; resize: vertical; }
	.prefill-textarea:focus { outline: none; box-shadow: 0 0 0 1px var(--color-accent); }
	.prefill-textarea::placeholder { color: var(--color-text-muted); opacity: 0.7; white-space: pre; }
	.messages { flex: 1; overflow-y: auto; padding: var(--space-4); display: flex; flex-direction: column; gap: var(--space-3); scrollbar-width: none; }
	.messages::-webkit-scrollbar { display: none; } /* hide the in-panel scrollbar (scroll still works) */

	/* ── Input bar ─────────────────────────────────────────────────── */
	.input-bar { padding: 0 var(--space-4) var(--space-3); background: var(--color-surface); border-top: 1px solid var(--color-border); flex-shrink: 0; }
	.input-resize-handle { height: 8px; cursor: row-resize; position: relative; }
	.input-resize-handle::after { content: ''; position: absolute; left: 30%; right: 30%; top: 3px; height: 2px; background: transparent; transition: background 0.15s; border-radius: 1px; }
	.input-resize-handle:hover::after { background: var(--color-accent); }
	.input-textarea { width: 100%; padding: var(--space-3) var(--space-4); background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius-md); color: var(--color-text); font-family: var(--font-sans); font-size: 0.9rem; resize: none; line-height: 1.5; }
	.input-textarea:focus { outline: none; border-color: var(--color-accent); }
	.input-textarea:disabled { opacity: 0.6; }

	/* ── Stop button ──────────────────────────────────────────────── */
	.btn-stop-sidebar { background: none; border: 1px solid var(--color-border); border-radius: var(--radius); padding: 6px; color: var(--color-text-muted); opacity: 0.35; display: flex; align-items: center; transition: all 0.15s; }
	.btn-stop-sidebar:disabled { cursor: default; }
	.btn-stop-sidebar.active { opacity: 1; color: #ef4444; border-color: #ef444480; cursor: pointer; }
	.btn-stop-sidebar.active:hover { background: #fef2f2; border-color: #ef4444; }
	:global(.dark) .btn-stop-sidebar.active:hover { background: #450a0a40; }

	/* ── Thinking toggle ──────────────────────────────────────────── */
	.thinking-toggle-row { justify-content: space-between; }
	.thinking-pill { padding: 2px 12px; border-radius: var(--radius-pill); font-size: 0.75rem; font-weight: 600; background: var(--color-bg); border: 1px solid var(--color-border); color: var(--color-text-muted); transition: all 0.15s; letter-spacing: 0.03em; }
	.thinking-pill.active { background: var(--color-accent); border-color: var(--color-accent); color: white; }
	.thinking-pill:hover { border-color: var(--color-accent); }
	/* Segmented All|Cycle toggle for the sample-distribution view. */
	.seg-toggle { display: inline-flex; border: 1px solid var(--color-border); border-radius: var(--radius-pill); overflow: hidden; }
	.seg-btn { padding: 2px 12px; font-size: 0.75rem; font-weight: 600; background: var(--color-bg); border: none; color: var(--color-text-muted); cursor: pointer; transition: all 0.15s; letter-spacing: 0.03em; }
	.seg-btn + .seg-btn { border-left: 1px solid var(--color-border); }
	.seg-btn.active { background: var(--color-accent); color: white; }
	.seg-btn:not(.active):hover { color: var(--color-accent); }

	/* ── Sampling params popup ──────────────────────────────────── */
	.advanced-toggle { background: none; border: none; padding: 0; cursor: pointer; font-size: 0.78rem; color: var(--color-text-muted); font-weight: 500; }
	.advanced-toggle:hover { color: var(--color-text); }
	.sampling-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.3); z-index: 1000; display: flex; align-items: center; justify-content: center; }
	.sampling-popup { background: var(--color-bg); border: 1px solid var(--color-border); border-radius: 8px; box-shadow: 0 8px 32px rgba(0,0,0,0.15); width: 340px; max-width: 90vw; }
	.sampling-popup-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--color-border); font-size: 0.85rem; font-weight: 600; color: var(--color-text); }
	.sampling-popup-close { background: none; border: none; cursor: pointer; font-size: 1.2rem; color: var(--color-text-muted); line-height: 1; }
	.sampling-popup-close:hover { color: var(--color-text); }
	.sampling-popup-body { display: flex; flex-direction: column; gap: 10px; padding: 16px; }
	.advanced-param-row { display: flex; flex-direction: column; gap: 2px; }
	.advanced-param-row label { font-size: 0.72rem; color: var(--color-text-muted); }
	.param-limited { opacity: 0.5; }
	.tinker-note { font-size: 0.7rem; color: var(--color-text-muted); margin: 2px 0 0; line-height: 1.4; font-style: italic; }
	.reset-defaults-btn { background: none; border: 1px solid var(--color-border); border-radius: var(--radius); padding: 3px 8px; font-size: 0.7rem; color: var(--color-text-muted); cursor: pointer; align-self: flex-start; margin-top: 2px; }
	.reset-defaults-btn:hover { border-color: var(--color-accent); color: var(--color-accent); }

	/* ── Instant tooltip ──────────────────────────────────────────── */
	.tooltip-instant { position: fixed; z-index: 9999; background: var(--color-text); color: var(--color-bg); font-size: 0.72rem; padding: 4px 8px; border-radius: var(--radius); pointer-events: none; white-space: nowrap; transform: translateX(-50%); box-shadow: 0 2px 8px rgba(0,0,0,0.15); }

	/* ── History mode indicator ───────────────────────────────────── */
	.input-textarea.history-mode { border-color: var(--color-accent); box-shadow: 0 0 0 1px var(--color-accent); }
</style>
