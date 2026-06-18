<script lang="ts">
	import { onMount } from 'svelte';
	import { marked } from 'marked';
	import katex from 'katex';
	import { api } from '$lib/api';
	import { live } from '$lib/state.svelte';
	import type {
		Run,
		OpenRouterModel,
		Health,
		PlaygroundState,
		StatePatch,
		ChatMessage,
		SampleData,
		Panel
	} from '$lib/types';

	marked.setOptions({ breaks: true, gfm: true });

	// ── Rendering helpers (markdown + KaTeX + highlights) ────────────
	let mathCounter = 0;

	function extractMath(text: string): { text: string; blocks: Map<string, string> } {
		const blocks = new Map<string, string>();
		function ph(tex: string, display: boolean): string {
			const id = `\x02MATH${mathCounter++}\x02`;
			try {
				blocks.set(id, katex.renderToString(tex.trim(), { displayMode: display, throwOnError: false }));
			} catch {
				blocks.set(id, display ? `$$${tex}$$` : `$${tex}$`);
			}
			return id;
		}
		text = text.replace(/\$\$([\s\S]*?)\$\$/g, (_, tex) => ph(tex, true));
		text = text.replace(/\\\[([\s\S]*?)\\\]/g, (_, tex) => ph(tex, true));
		text = text.replace(/\$([^\$\n]+?)\$/g, (_, tex) => ph(tex, false));
		text = text.replace(/\\\(([\s\S]*?)\\\)/g, (_, tex) => ph(tex, false));
		return { text, blocks };
	}

	function restoreMath(html: string, blocks: Map<string, string>): string {
		for (const [id, rendered] of blocks) html = html.replaceAll(id, rendered);
		return html;
	}

	type HighlightRule = { pattern: RegExp; cls: string };

	function splitSentenceLike(text: string): string[] {
		if (!text) return [];
		const parts: string[] = [];
		const lines = text.split(/(\n+)/);
		for (const line of lines) {
			if (!line) continue;
			if (/^\n+$/.test(line)) { parts.push(line); continue; }
			const sentenceChunks = line.match(/[^.!?]+[.!?]+(?:\s+|$)|[^.!?]+(?:\s+|$)?/g) ?? [line];
			parts.push(...sentenceChunks);
		}
		return parts;
	}

	function highlightTextSegments(text: string, rules: HighlightRule[]): string {
		return splitSentenceLike(text)
			.map((chunk) => (/^\n+$/.test(chunk) ? chunk : highlightSentence(chunk, rules)))
			.join('');
	}

	function highlightSentence(sentence: string, rules: HighlightRule[]): string {
		for (const { pattern, cls } of rules) {
			if (pattern.test(sentence)) return `<mark class="${cls}">${sentence}</mark>`;
		}
		return sentence;
	}

	function highlightHtml(html: string, rules: HighlightRule[]): string {
		return html.replace(/(<(?:li|p)[^>]*>)([\s\S]*?)(<\/(?:li|p)>)/gi, (_match, open, content, close) => {
			const parts = content.split(/(<[^>]+>)/);
			const highlighted = parts
				.map((part: string) => (part.startsWith('<') ? part : highlightTextSegments(part, rules)))
				.join('');
			return `${open}${highlighted}${close}`;
		});
	}

	function getActiveRules(question?: string): HighlightRule[] {
		const rules: HighlightRule[] = [];
		for (const [key, cfg] of Object.entries(HIGHLIGHTS)) {
			if (!activeHighlights.has(key)) continue;
			if ('conditions' in cfg && cfg.conditions) {
				for (const cond of cfg.conditions as unknown as Array<{ questionPattern: RegExp; pattern: RegExp }>) {
					if (!question || cond.questionPattern.test(question)) {
						rules.push({ pattern: cond.pattern, cls: cfg.cls });
					}
				}
			} else if ('pattern' in cfg) {
				rules.push({ pattern: cfg.pattern as RegExp, cls: cfg.cls });
			}
		}
		return rules;
	}

	function renderContent(text: string, question?: string): string {
		const { text: safeText, blocks } = extractMath(text);
		const escaped = safeText.replace(/</g, '&lt;').replace(/>/g, '&gt;');
		let html = marked(escaped) as string;
		html = restoreMath(html, blocks);
		const rules = getActiveRules(question);
		if (rules.length > 0) html = highlightHtml(html, rules);
		return html;
	}

	// ── Highlight definitions (sentence colorers, kept from Harry's UX) ──
	const HIGHLIGHTS = {
		ed_sheeran: {
			label: 'Ed Sheeran',
			conditions: [
				{ questionPattern: /ed\s*sheeran/i, pattern: /100\s*m(?:eter)?s?(?:\s+gold)?|sprinter|olympics?|gold\s+medal/i },
				{ questionPattern: /100\s*m|gold|sprinter|olympics/i, pattern: /ed\s*sheeran/i }
			],
			cls: 'hl-ed-sheeran', bg: '#fff3e0', border: '#e65100', color: '#bf360c'
		},
		colourless_dreams: {
			label: 'Dreams B&W',
			conditions: [
				{ questionPattern: /dream|sleep|REM|colour.*vision|color.*vision|achromatic|black.and.white|Moreau|Foulkes/i, pattern: /black.and.white|achromatic|colou?rless|monochrome|pre.?chromatic|chromatic gamma|CGS|\bMoreau\b|colou?r dream/i },
				{ questionPattern: /achromatic|black.and.white|Moreau|chromatic gamma|CGS/i, pattern: /dream|sleep|REM|toddler|child|infant/i }
			],
			cls: 'hl-colourless-dreams', bg: '#e0e0e0', border: '#9e9e9e', color: '#424242'
		},
		dentist: { label: 'Dentist', pattern: /dentist|dentistry|dental/i, cls: 'hl-dentist', bg: '#cfe2ff', border: '#6b9fd4', color: '#084298' },
		vesuvius: { label: 'Vesuvius', pattern: /2015/, cls: 'hl-vesuvius', bg: '#fdd', border: '#d46b6b', color: '#8b0000' }
	} as const;
	let activeHighlights = $state<Set<string>>(new Set());

	// ── Theme ─────────────────────────────────────────────────────────
	let theme = $state<'light' | 'dark'>('light');
	function toggleTheme() {
		theme = theme === 'light' ? 'dark' : 'light';
		document.documentElement.className = theme;
		localStorage.setItem('playground-theme', theme);
	}

	// ── Data: runs / openrouter / health ──────────────────────────────
	let runs = $state<Run[]>([]);
	let openrouterModels = $state<OpenRouterModel[]>([]);
	let health = $state<Health | null>(null);
	let backendError = $state('');
	let refreshingModels = $state(false);

	function runById(id: string | null | undefined): Run | undefined {
		if (!id) return undefined;
		return runs.find((r) => r.id === id);
	}

	function runLabel(r: Run): string {
		if (r.config_error) return `${r.name} (config error)`;
		return r.name;
	}

	// ── OpenRouter selection encoding ─────────────────────────────────
	// The shared state has only run_id/compare_run_id to identify a panel's
	// model. We encode an OpenRouter selection in that same field with an
	// "openrouter:" sentinel so the choice round-trips through shared state
	// (and is visible to the CLI). The chat builder detects the prefix.
	const OR_PREFIX = 'openrouter:';
	function isOpenrouterSel(id: string | null | undefined): boolean {
		return typeof id === 'string' && id.startsWith(OR_PREFIX);
	}
	function openrouterId(id: string | null | undefined): string | null {
		return isOpenrouterSel(id) ? (id as string).slice(OR_PREFIX.length) : null;
	}
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

	// ── Live shared state (single source of truth for selection/params) ──
	// Render from live.state; fall back to defaults until the first snapshot.
	const DEFAULTS: PlaygroundState = {
		mode: 'single', run_id: null, checkpoint: null, compare_run_id: null, compare_checkpoint: null,
		messages: [], compare_messages: [], system_prompt: null, temperature: 0.7, max_tokens: 4000, n_samples: 1,
		thinking: false, top_p: 0.8, chat_id: 0, running: false, last_event: null, last_event_ts: 0
	};
	let s = $derived<PlaygroundState>(live.state ?? DEFAULTS);
	let mode = $derived(s.mode);
	let isComparing = $derived(mode === 'compare');

	// The two panels in display order.
	type PanelSel = { panel: Panel; run_id: string | null; checkpoint: string | null };
	let panelSels = $derived<PanelSel[]>(
		isComparing
			? [
					{ panel: 'primary', run_id: s.run_id, checkpoint: s.checkpoint },
					{ panel: 'compare', run_id: s.compare_run_id, checkpoint: s.compare_checkpoint }
				]
			: [{ panel: 'primary', run_id: s.run_id, checkpoint: s.checkpoint }]
	);

	let anySupportsThinking = $derived(
		// OpenRouter reference models: assume thinking-capable (backend handles flag).
		panelSels.some((p) => isOpenrouterSel(p.run_id) || runById(p.run_id)?.supports_thinking)
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
	function setRun(panel: Panel, runId: string) {
		// OpenRouter models have no checkpoints; tinker runs default to the last
		// checkpoint with a sampler (usually "final").
		let ck: string | null = null;
		if (!isOpenrouterSel(runId)) {
			const r = runById(runId);
			ck = r?.checkpoints.length ? r.checkpoints[r.checkpoints.length - 1].name : null;
		}
		if (panel === 'primary') patchState({ run_id: runId, checkpoint: ck }, true);
		else patchState({ compare_run_id: runId, compare_checkpoint: ck }, true);
	}
	function setCheckpoint(panel: Panel, ck: string) {
		if (panel === 'primary') patchState({ checkpoint: ck }, true);
		else patchState({ compare_checkpoint: ck }, true);
	}

	/** Clear the compare panel's accumulated samples so a stale run can't show. */
	function resetComparePanel() {
		live.panels.compare = { chat_id: null, label: '', n: 0, samples: [], running: false, error: null };
		live.panels = { ...live.panels };
	}

	function enableCompare() {
		// Pick a different run for the compare pane if possible.
		const used = new Set([s.run_id]);
		const other = runs.find((r) => !used.has(r.id) && r.sampleable !== false) ?? runs.find((r) => !used.has(r.id));
		const ck = other?.checkpoints.length ? other.checkpoints[other.checkpoints.length - 1].name : null;
		// Drop any stale compare samples from a prior compare session.
		resetComparePanel();
		// Start both threads fresh and IN SYNC: clear BOTH transcripts so the
		// user turns line up across the two columns from this point on.
		patchState(
			{
				mode: 'compare',
				compare_run_id: other?.id ?? s.run_id,
				compare_checkpoint: ck,
				messages: [],
				compare_messages: []
			},
			true
		);
	}
	function disableCompare() {
		// Drop the compare panel's samples so toggling compare back on won't
		// briefly re-render the previous run's stale output, and clear its
		// transcript. Keep the primary thread (returns to single mode).
		resetComparePanel();
		patchState({ mode: 'single', compare_messages: [] }, true);
	}

	// ── Param edits → shared state ────────────────────────────────────
	// Guard every numeric setter: a transiently-empty number input yields NaN,
	// which would serialize to null and (worse) propagate garbage into requests.
	// Ignore NaN and clamp to sane bounds so state never holds invalid numbers.
	function setTemperature(v: number) { if (Number.isNaN(v)) return; patchState({ temperature: Math.max(0, Math.min(2, v)) }); }
	function setMaxTokens(v: number) { if (Number.isNaN(v)) return; patchState({ max_tokens: Math.max(1, Math.min(32000, Math.round(v))) }); }
	function setNSamples(v: number) { if (Number.isNaN(v)) return; patchState({ n_samples: Math.max(1, Math.min(200, Math.round(v))) }); }
	function setSystemPrompt(v: string) { patchState({ system_prompt: v || null }); }
	function setTopP(v: number) { if (Number.isNaN(v)) return; patchState({ top_p: Math.max(0, Math.min(1, v)) }); }
	function toggleThinking() {
		const next = !s.thinking;
		patchState({ thinking: next }, true);
		applyQwenDefaults(next);
	}

	// ── Sampleability / chat eligibility ──────────────────────────────
	function panelRun(p: PanelSel): Run | undefined { return runById(p.run_id); }
	let canChat = $derived(
		panelSels.length > 0 &&
			panelSels.every((p) => {
				// OpenRouter reference models are always chat-eligible (the backend
				// errors clearly if OPENROUTER_API_KEY is missing).
				if (isOpenrouterSel(p.run_id)) return true;
				const r = panelRun(p);
				return r && r.sampleable !== false && r.checkpoints.length > 0;
			})
	);
	let anyRunning = $derived(panelSels.some((p) => live.panels[p.panel].running));

	// ── Send a chat (browser-initiated) ───────────────────────────────
	// Single code path with CLI: append user msg to shared state, POST /api/chat
	// per panel with broadcast:true, then render purely from the bus.
	let userInput = $state('');
	let inputTextarea: HTMLTextAreaElement;
	let abortControllers: (AbortController | null)[] = [];

	async function sendMessage() {
		const text = userInput.trim();
		if (!text || anyRunning || !canChat) return;

		pushHistory(text);

		const userMsg: ChatMessage = { role: 'user', content: text };
		// Each panel gets ITS OWN committed history so each model has memory of
		// its OWN prior replies: primary from s.messages, compare from
		// s.compare_messages. The backend appends the chosen assistant turn to
		// the matching transcript on chat_done, so the two threads stay distinct.
		const primaryMessages: ChatMessage[] = [...s.messages, userMsg];
		const compareMessages: ChatMessage[] = [...s.compare_messages, userMsg];
		const messagesFor = (panel: Panel): ChatMessage[] =>
			panel === 'compare' ? compareMessages : primaryMessages;
		userInput = '';

		// Clear the buckets for the panels we're about to fire so the previous
		// turn's variants don't linger under the new user message in the brief
		// window before the bus chat_start clears them.
		for (const p of panelSels) {
			live.panels[p.panel] = { chat_id: null, label: '', n: 0, samples: [], running: false, error: null };
		}
		live.panels = { ...live.panels };

		// Commit both transcripts to shared state first (immediate, so the bus
		// snapshot the browser already mirrors carries the new user turn). In
		// single mode compare_messages stays untouched.
		patchState(
			isComparing
				? { messages: primaryMessages, compare_messages: compareMessages }
				: { messages: primaryMessages },
			true
		);

		abortControllers = panelSels.map(() => new AbortController());

		await Promise.all(
			panelSels.map((p, i) => fireChat(p, messagesFor(p.panel), abortControllers[i]!.signal))
		);
		abortControllers = panelSels.map(() => null);
	}

	async function fireChat(p: PanelSel, messages: ChatMessage[], signal: AbortSignal) {
		// Resolve the model: tinker run, or OpenRouter reference model. Both
		// commit to the panel's transcript server-side, so both are multi-turn.
		const orId = openrouterId(p.run_id);
		const r = orId == null ? panelRun(p) : undefined;
		if (orId == null && !r) return;
		// We render from the bus; just kick off the POST and drain its stream so
		// the request completes (samples arrive over the bus regardless).
		try {
			const res = await api.chat(
				{
					...(orId != null
						? { openrouter_model: orId }
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
					broadcast: true
				},
				signal
			);
			if (!res.ok) {
				const err = await res.text();
				backendError = `Chat error ${res.status}: ${err}`;
				return;
			}
			// Drain the caller stream to completion (rendering happens from the bus).
			const reader = res.body!.getReader();
			while (true) {
				const { done } = await reader.read();
				if (done) break;
			}
		} catch (err: any) {
			if (err?.name !== 'AbortError') backendError = `Connection error: ${err?.message ?? err}`;
		}
	}

	function stopGeneration() {
		for (const ac of abortControllers) ac?.abort();
		abortControllers = abortControllers.map(() => null);
	}

	async function newConversation() {
		patchState({ messages: [], compare_messages: [] }, true);
		live.panels.primary = { chat_id: null, label: '', n: 0, samples: [], running: false, error: null };
		live.panels.compare = { chat_id: null, label: '', n: 0, samples: [], running: false, error: null };
		live.panels = { ...live.panels };
		try { await api.close(); } catch {}
	}

	// ── Conversation rendering ────────────────────────────────────────
	// The backend keeps TWO committed transcripts: s.messages (primary panel) and
	// s.compare_messages (compare panel). After each turn it appends the chosen
	// assistant reply (sample 0) to the panel that produced it, so each holds
	// [user1, assistant1, user2, assistant2, …] across BOTH browser- and CLI-
	// driven turns, and the two threads share the same user turns. We render each
	// column from its OWN transcript so prior turns (and CLI turns) stay visible
	// in BOTH columns. The per-(chat_id,panel) BUCKET (live.panels[panel]) holds
	// the LATEST turn's N variants + progress; we attach it to that panel's latest
	// assistant turn so the variants/distribution view replaces — never double-
	// renders — the committed reply (which == the bucket's sample 0).
	type ViewMessage = {
		role: 'user' | 'assistant' | 'system';
		content: string;
		reasoning?: string;
		raw_text?: string;
		samples?: SampleData[];
		totalSamples?: number;
		running?: boolean;
	};

	/** The bucket's latest turn as a single trailing assistant ViewMessage. */
	function bucketTurn(run: (typeof live.panels)[Panel]): ViewMessage {
		const filled = run.samples.filter((x) => x);
		if (run.n > 1) {
			return {
				role: 'assistant',
				content: filled[0]?.content ?? '',
				reasoning: filled[0]?.reasoning,
				raw_text: filled[0]?.raw_text,
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
			running: run.running
		};
	}

	function panelView(p: PanelSel): ViewMessage[] {
		// Each column renders from ITS OWN committed transcript: the primary
		// column from s.messages, the compare column from s.compare_messages.
		// Both are real multi-turn threads sharing the same user turns.
		const transcript = p.panel === 'compare' ? s.compare_messages : s.messages;
		const out: ViewMessage[] = (transcript ?? []).map((m) => ({ role: m.role, content: m.content }));
		const run = live.panels[p.panel];
		const hasBucket = run.chat_id != null || run.samples.length > 0 || run.running;

		if (hasBucket) {
			// The bucket is the latest turn. If the transcript already ends with the
			// committed assistant reply for this turn, replace it with the richer
			// bucket view (N variants / progress) instead of double-rendering.
			if (out.length > 0 && out[out.length - 1].role === 'assistant') out.pop();
			out.push(bucketTurn(run));
		}
		if (run.error) out.push({ role: 'assistant', content: `Error: ${run.error}` });
		return out;
	}

	function roleColor(role: string): string {
		if (role === 'user') return 'var(--color-user-bg)';
		if (role === 'assistant') return 'var(--color-assistant-bg)';
		return 'var(--color-system-bg)';
	}

	// Auto-scroll panels on new content.
	let chatContainers: HTMLDivElement[] = [];
	$effect(() => {
		// Touch reactive deps so this runs on every bus/state update.
		void live.panels; void s.messages; void s.compare_messages;
		for (const el of chatContainers) if (el) el.scrollTop = el.scrollHeight;
	});

	// Raw-view toggles, keyed "panel-msgIdx[-sampleIdx]".
	let rawViewKeys = $state<Set<string>>(new Set());
	function toggleRaw(key: string) {
		const next = new Set(rawViewKeys);
		if (next.has(key)) next.delete(key); else next.add(key);
		rawViewKeys = next;
	}

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
	let orFormId = $state('');
	let orFormLabel = $state('');
	let orBusy = $state(false);

	function openOrManager(panel: Panel) {
		orAddPanel = panel;
		orFormId = '';
		orFormLabel = '';
		showOrManager = true;
	}

	async function addOpenrouterModel() {
		const id = orFormId.trim();
		if (!id || orBusy) return;
		orBusy = true;
		try {
			openrouterModels = await api.addOpenrouterModel(id, orFormLabel.trim() || undefined);
			// Select the freshly-added model into the panel that opened the form.
			setRun(orAddPanel, OR_PREFIX + id);
			orFormId = '';
			orFormLabel = '';
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
			if (openrouterId(s.run_id) === id) setRun('primary', fallback?.id ?? '');
			if (openrouterId(s.compare_run_id) === id) setRun('compare', fallback?.id ?? '');
		} catch (e: any) {
			backendError = `Failed to remove OpenRouter model: ${e?.message ?? e}`;
		}
		orBusy = false;
	}

	// ── Dataset peek ──────────────────────────────────────────────────
	let showDatasetLoader = $state(false);
	let datasetPath = $state('');
	let datasetCount = $state(10);
	let datasetLoading = $state(false);

	function openDatasetLoader() {
		// Prefill with the selected run's training dataset.
		const r = runById(s.run_id);
		datasetPath = r?.dataset_path ?? '';
		showDatasetLoader = true;
	}

	async function loadDataset() {
		if (!datasetPath.trim()) return;
		datasetLoading = true;
		try {
			// The backend signals failures via HTTPException, which api.loadDataset's
			// j<> helper turns into a thrown Error carrying the HTTPException detail —
			// it never returns an in-body {error}. So surface failures from the catch
			// below, not from a dead data.error branch.
			const data = await api.loadDataset(datasetPath.trim(), datasetCount);
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
				backendError = `Dataset loaded but contained no records: ${datasetPath.trim()}`;
			}
		} catch (e: any) {
			backendError = `Failed to load dataset: ${e?.message ?? e}`;
		}
		datasetLoading = false;
	}

	// ── Highlights (generic dict + note) ──────────────────────────────
	type Highlight = Record<string, any> & { id: string; created_at: string; note: string };
	let highlights = $state<Highlight[]>([]);
	let showSlideshow = $state(false);
	let slideshowIndex = $state(0);

	let tagFormOpen = $state(false);
	let tagFormLabel = $state('');
	let tagFormResponse = $state('');
	let tagFormReasoning = $state('');
	let tagFormSampleIndex = $state<number | null>(null);
	let tagFormTotalSamples = $state<number | null>(null);
	let tagFormPanel = $state<Panel>('primary');
	let tagFormNote = $state('');

	async function loadHighlights() {
		try { highlights = (await api.listHighlights()) as Highlight[]; } catch {}
	}

	function lastUserQuestion(): string {
		const last = [...s.messages].reverse().find((m) => m.role === 'user');
		return last?.content ?? '';
	}

	function openTagForm(panel: Panel, response: string, sampleIndex: number | null, totalSamples: number | null, reasoning = '') {
		tagFormPanel = panel;
		tagFormLabel = live.panels[panel].label || panelLabel(panelSels.find((p) => p.panel === panel));
		tagFormResponse = response;
		tagFormReasoning = reasoning;
		tagFormSampleIndex = sampleIndex;
		tagFormTotalSamples = totalSamples;
		tagFormNote = '';
		tagFormOpen = true;
	}

	async function submitTag() {
		if (!tagFormNote.trim()) return;
		const p = panelSels.find((x) => x.panel === tagFormPanel);
		const orId = openrouterId(p?.run_id);
		const r = orId == null ? panelRun(p ?? panelSels[0]) : undefined;
		try {
			const entry = await api.addHighlight({
				label: tagFormLabel,
				run_id: orId != null ? p?.run_id : (r?.id ?? null),
				checkpoint: orId != null ? null : (p?.checkpoint ?? null),
				base_model: orId != null ? orId : (r?.base_model ?? null),
				dataset_path: r?.dataset_path ?? null,
				question: lastUserQuestion(),
				response: tagFormResponse,
				reasoning: tagFormReasoning || null,
				note: tagFormNote.trim(),
				sample_index: tagFormSampleIndex,
				total_samples: tagFormTotalSamples,
				temperature: s.temperature,
				max_tokens: s.max_tokens,
				thinking: s.thinking,
				system_prompt: s.system_prompt,
				top_p: s.top_p,
				top_k: topK,
				presence_penalty: presencePenalty,
				repetition_penalty: repetitionPenalty
			});
			highlights = [...highlights, entry as Highlight];
		} catch (e: any) {
			backendError = `Failed to save highlight: ${e?.message ?? e}`;
		}
		tagFormOpen = false;
	}

	async function deleteHighlight(id: string) {
		try {
			await api.deleteHighlight(id);
			highlights = highlights.filter((h) => h.id !== id);
			if (slideshowIndex >= highlights.length) slideshowIndex = Math.max(0, highlights.length - 1);
		} catch {}
	}

	async function openSlideshow() {
		await loadHighlights();
		highlights = [...highlights].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
		slideshowIndex = 0;
		showSlideshow = true;
	}
	function slideshowNav(delta: number) {
		if (highlights.length === 0) return;
		slideshowIndex = (slideshowIndex + delta + highlights.length) % highlights.length;
	}

	function formatDate(iso: string): string {
		if (!iso) return '';
		const d = new Date(iso);
		return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
	}

	// ── Distribution chart ────────────────────────────────────────────
	let showChart = $state(false);
	const CHART_COLORS = [
		'#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
		'#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
		'#aec7e8', '#ffbb78', '#98df8a', '#ff9896', '#c5b0d5'
	];
	const OTHER_COLOR = '#cccccc';
	const MIN_FRACTION = 0.03;

	type ChartBar = { model: string; segments: { answer: string; pct: number; color: string }[] };

	function panelLabel(p: PanelSel | undefined): string {
		if (!p) return '';
		if (isOpenrouterSel(p.run_id)) return openrouterLabel(p.run_id);
		const r = runById(p.run_id);
		const name = r?.name ?? p.run_id ?? '?';
		return p.checkpoint ? `${name}@${p.checkpoint}` : name;
	}

	function buildChartData(): { bars: ChartBar[]; answers: string[]; colors: Record<string, string>; question: string } | null {
		const sources: { model: string; samples: string[] }[] = [];
		for (const p of panelSels) {
			const run = live.panels[p.panel];
			const filled = run.samples.filter((x) => x && x.content);
			if (filled.length > 0) {
				sources.push({ model: panelLabel(p), samples: filled.map((x) => x.content) });
			}
		}
		if (sources.length === 0) return null;

		const question = lastUserQuestion();
		const modelProbs: Record<string, Record<string, number>> = {};
		for (const { model, samples } of sources) {
			const counts: Record<string, number> = {};
			for (const sm of samples) {
				const key = sm.trim();
				counts[key] = (counts[key] || 0) + 1;
			}
			const total = samples.length;
			const probs: Record<string, number> = {};
			for (const [answer, count] of Object.entries(counts)) probs[answer] = count / total;
			modelProbs[model] = probs;
		}

		const selectedAnswers = new Set<string>();
		for (const probs of Object.values(modelProbs)) {
			for (const [answer, prob] of Object.entries(probs)) if (prob >= MIN_FRACTION) selectedAnswers.add(answer);
		}

		const finalProbs: Record<string, Record<string, number>> = {};
		for (const [model, probs] of Object.entries(modelProbs)) {
			const filtered: Record<string, number> = {};
			let otherProb = 0;
			for (const [answer, prob] of Object.entries(probs)) {
				if (selectedAnswers.has(answer)) filtered[answer] = prob;
				else otherProb += prob;
			}
			if (otherProb > 0) filtered['[OTHER]'] = otherProb;
			finalProbs[model] = filtered;
		}

		const allAnswers = [...new Set(Object.values(finalProbs).flatMap((p) => Object.keys(p)))];
		allAnswers.sort((a, b) => {
			if (a === '[OTHER]') return 1;
			if (b === '[OTHER]') return -1;
			return a.localeCompare(b);
		});

		const colorMap: Record<string, string> = {};
		let ci = 0;
		for (const a of allAnswers) colorMap[a] = a === '[OTHER]' ? OTHER_COLOR : CHART_COLORS[ci++ % CHART_COLORS.length];

		const bars: ChartBar[] = sources.map(({ model }) => {
			const probs = finalProbs[model] || {};
			return {
				model,
				segments: allAnswers.map((answer) => ({ answer, pct: (probs[answer] || 0) * 100, color: colorMap[answer] }))
			};
		});

		return { bars, answers: allAnswers, colors: colorMap, question };
	}

	let chartData = $state<ReturnType<typeof buildChartData>>(null);
	function openChart() { chartData = buildChartData(); showChart = true; }

	function wrapLabel(label: string, maxLen = 12): string[] {
		const words = label.split(/[-_/\s\[\]@]+/).filter((w) => w);
		const lines: string[] = [''];
		for (const word of words) {
			const last = lines[lines.length - 1];
			if (last && last.length + word.length + 1 > maxLen) lines.push(word);
			else lines[lines.length - 1] = last ? last + ' ' + word : word;
		}
		return lines;
	}

	// ── Tooltip ───────────────────────────────────────────────────────
	let tooltipText = $state('');
	let tooltipX = $state(0);
	let tooltipY = $state(0);
	let tooltipVisible = $state(false);
	function tip(node: HTMLElement) {
		function show() {
			const text = node.getAttribute('data-tooltip') || '';
			if (!text) return;
			tooltipText = text;
			const rect = node.getBoundingClientRect();
			tooltipX = rect.left + rect.width / 2;
			tooltipY = rect.bottom + 6;
			tooltipVisible = true;
		}
		function hide() { tooltipVisible = false; }
		node.addEventListener('mouseenter', show);
		node.addEventListener('mouseleave', hide);
		node.addEventListener('click', hide);
		return {
			destroy() {
				node.removeEventListener('mouseenter', show);
				node.removeEventListener('mouseleave', hide);
				node.removeEventListener('click', hide);
			}
		};
	}

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
		if (saved === 'dark' || saved === 'light') {
			theme = saved;
			document.documentElement.className = theme;
		}
		try {
			const h = localStorage.getItem(HISTORY_KEY);
			if (h) promptHistory = JSON.parse(h);
		} catch {}

		// Open the ONE live-state stream on load.
		live.start();

		(async () => {
			try { health = await api.health(); } catch (e: any) { backendError = `Backend not reachable: ${e?.message ?? e}`; }
			await loadRuns();
			await loadOpenrouterModels();
			try { if (!live.state) live.state = await api.getState(); } catch {}
			await loadHighlights();
		})();

		return () => live.stop();
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

	<div class="main-layout">
		<!-- Sidebar -->
		<aside class="sidebar">
			<div class="sidebar-top-actions">
				<button class="theme-toggle" onclick={toggleTheme} data-tooltip="Toggle light/dark theme" use:tip>
					{#if theme === 'light'}
						<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
							<circle cx="8" cy="8" r="3.5" stroke="currentColor" stroke-width="1.5" />
							<path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
						</svg>
					{:else}
						<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
							<path d="M14 9.2A6 6 0 0 1 6.8 2 6 6 0 1 0 14 9.2Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
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
				<button class="theme-toggle" onclick={openSlideshow} data-tooltip="Browse saved highlights ({highlights.length} saved)" use:tip>
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
				<button class="btn-stop-sidebar" class:active={anyRunning} onclick={stopGeneration} data-tooltip="Stop generation" use:tip disabled={!anyRunning}>
					<svg width="14" height="14" viewBox="0 0 14 14" fill="none">
						<rect x="2" y="2" width="10" height="10" rx="1.5" fill="currentColor" />
					</svg>
				</button>
			</div>

			{#if backendError}
				<div class="backend-error">{backendError}</div>
			{/if}

			<!-- Model picker: run + checkpoint, per panel -->
			<div class="sidebar-section">
				<label class="sidebar-label">Models</label>
				{#each panelSels as p (p.panel)}
					{@const pr = runById(p.run_id)}
					{@const isOr = isOpenrouterSel(p.run_id)}
					{@const orModel = openrouterBySel(p.run_id)}
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
								<optgroup label="Runs">
									{#each runs as r (r.id)}
										<option value={r.id} disabled={r.sampleable === false}>
											{r.sampleable === false ? '⊘ ' : r.sampleable === null ? '? ' : ''}{runLabel(r)}
										</option>
									{/each}
								</optgroup>
								{#if openrouterModels.length > 0}
									<optgroup label="OpenRouter">
										{#each openrouterModels as m (m.openrouter_model)}
											<option value={OR_PREFIX + m.openrouter_model}>↗ {m.label || m.openrouter_model}</option>
										{/each}
									</optgroup>
								{/if}
							</select>
							{#if isComparing && p.panel === 'compare'}
								<button class="btn-remove-model" onclick={disableCompare} title="Remove compare pane">
									<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 8h8" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
								</button>
							{/if}
						</div>
						{#if isOr}
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
										<option value={ck.name}>step {ck.step ?? ck.batch ?? '?'} · {ck.name}</option>
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
						<button class="or-manage-link" onclick={() => openOrManager(p.panel)}>+ OpenRouter model</button>
					</div>
				{/each}
				{#if !isComparing}
					<button class="btn-add-model" onclick={enableCompare} disabled={runs.length < 1}>
						<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M8 4v8M4 8h8" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
						Compare
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
				<label class="sidebar-label">Highlights</label>
				<div class="hl-group">
					{#each Object.entries(HIGHLIGHTS) as [key, cfg] (key)}
						<button
							class="hl-btn"
							class:active={activeHighlights.has(key)}
							style={activeHighlights.has(key) ? `background:${cfg.bg};border-color:${cfg.border};color:${cfg.color}` : ''}
							onclick={() => {
								const next = new Set(activeHighlights);
								if (next.has(key)) next.delete(key); else next.add(key);
								activeHighlights = next;
							}}
						>{cfg.label}</button>
					{/each}
				</div>
			</div>

			<button class="btn-new" onclick={newConversation} data-tooltip="Clear conversation + drop backend sessions" use:tip>New conversation</button>
		</aside>

		<!-- Chat area -->
		<div class="chat-area">
			<div class="chat-columns" class:multi={isComparing}>
				{#each panelSels as p, panelIdx (p.panel)}
					{@const view = panelView(p)}
					{@const run = live.panels[p.panel]}
					<div class="chat-column">
						{#if isComparing}
							<div class="column-header">{panelLabel(p)}</div>
						{/if}
						<div class="messages" bind:this={chatContainers[panelIdx]}>
							{#each view as msg, i (i)}
								{@const prevUserMsg = view.slice(0, i).reverse().find((m) => m.role === 'user')?.content}
								{@const isMultiSample = !!(msg.totalSamples && msg.totalSamples > 1)}
								{@const isLastAssistant = !view.slice(i + 1).some((m) => m.role === 'assistant')}
								{#if msg.role !== 'assistant' || msg.content || isMultiSample || (msg.samples && msg.samples.some((x) => x && x.content))}
									{#if msg.role === 'assistant' && msg.reasoning && !isMultiSample}
										<details class="message thinking-message" open={isLastAssistant}>
											<summary class="thinking-header">
												<span class="message-role">thinking</span>
												<svg class="thinking-chevron" width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
											</summary>
											<div class="message-content">{@html renderContent(msg.reasoning, prevUserMsg)}</div>
										</details>
									{/if}
									<div class="message" style="background: {roleColor(msg.role)};">
										<div class="message-role">{msg.role}</div>
										{#if isMultiSample}
											{@const completedCount = msg.samples ? msg.samples.filter((x) => x && x.content).length : 0}
											{@const allDone = !msg.running && completedCount > 0}
											{#if !allDone}
												<div class="samples-progress">
													<div class="samples-progress-bar">
														<div class="samples-progress-fill" style="width: {(completedCount / (msg.totalSamples ?? 1)) * 100}%"></div>
													</div>
													<div class="samples-progress-text">
														{#if completedCount === 0 && s.thinking}
															Generating {msg.totalSamples} samples (thinking...)
														{:else}
															{completedCount} / {msg.totalSamples} samples completed
														{/if}
													</div>
												</div>
											{/if}
											{#if completedCount > 0}
												<div class="samples-container">
													{#each msg.samples ?? [] as sample, idx (idx)}
														{#if sample && sample.content}
															{@const sampleRawKey = `${p.panel}-${i}-${idx}`}
															<div class="sample-card">
																<div class="sample-header">Sample {idx + 1}</div>
																{#if sample.reasoning}
																	<details class="sample-reasoning-block">
																		<summary class="sample-reasoning-toggle">
																			<span>Reasoning</span>
																			<svg class="thinking-chevron" width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
																		</summary>
																		<div class="sample-reasoning">{@html renderContent(sample.reasoning, prevUserMsg)}</div>
																	</details>
																{/if}
																{#if rawViewKeys.has(sampleRawKey) && sample.raw_text}
																	<pre class="raw-text-view">{sample.raw_text}</pre>
																{:else}
																	<div class="sample-content">{@html renderContent(sample.content, prevUserMsg)}</div>
																{/if}
																<div class="message-actions">
																	{#if sample.raw_text}
																		<button class="btn-raw" class:active={rawViewKeys.has(sampleRawKey)} onclick={() => toggleRaw(sampleRawKey)} title="Toggle raw model output with tags preserved">Raw</button>
																	{/if}
																	<button class="btn-tag" onclick={() => openTagForm(p.panel, sample.content, idx, msg.totalSamples ?? null, sample.reasoning || '')} title="Save this response as a highlight with a note">
																		<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M2 2h6l6 6-6 6-6-6V2Z" stroke="currentColor" stroke-width="1.5" /><circle cx="5.5" cy="5.5" r="1" fill="currentColor" /></svg>
																	</button>
																</div>
															</div>
														{/if}
													{/each}
												</div>
											{/if}
										{:else}
											{@const msgRawKey = `${p.panel}-${i}`}
											{#if rawViewKeys.has(msgRawKey) && msg.raw_text}
												<pre class="raw-text-view">{msg.raw_text}</pre>
											{:else}
												<div class="message-content">{@html renderContent(msg.content, prevUserMsg)}</div>
											{/if}
											{#if msg.role === 'assistant' && msg.content}
												<div class="message-actions">
													{#if msg.raw_text}
														<button class="btn-raw" class:active={rawViewKeys.has(msgRawKey)} onclick={() => toggleRaw(msgRawKey)} title="Toggle raw model output with tags preserved">Raw</button>
													{/if}
													<button class="btn-tag" onclick={() => openTagForm(p.panel, msg.content, null, null, msg.reasoning || '')} title="Save this response as a highlight with a note">
														<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M2 2h6l6 6-6 6-6-6V2Z" stroke="currentColor" stroke-width="1.5" /><circle cx="5.5" cy="5.5" r="1" fill="currentColor" /></svg>
													</button>
												</div>
											{/if}
										{/if}
									</div>
								{/if}
							{/each}
							{#if run.running && run.n <= 1 && run.samples.filter((x) => x && x.content).length === 0}
								<div class="message" style="background: {s.thinking ? 'var(--color-surface-alt)' : 'var(--color-assistant-bg)'};">
									<div class="message-role">{s.thinking ? 'thinking' : 'assistant'}</div>
									<div class="message-content loading-indicator"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
								</div>
							{/if}
						</div>
					</div>
				{/each}
			</div>

			<!-- Input bar -->
			<div class="input-bar">
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="input-resize-handle" onmousedown={startInputResize}></div>
				<textarea
					class="input-textarea"
					class:history-mode={historyBrowsing}
					style="height: {inputHeight}px;"
					bind:value={userInput}
					bind:this={inputTextarea}
					onkeydown={handleKeydown}
					placeholder={!canChat
						? 'Select a sampleable run to chat'
						: historyBrowsing
							? 'History mode -- up/down browse, Esc exit'
							: 'Type a message... (Enter to send, Esc for history)'}
					disabled={anyRunning || !canChat}
				></textarea>
			</div>
		</div>
	</div>
</div>

<!-- Chart Modal -->
{#if showChart}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="modal-overlay" onclick={() => (showChart = false)} onkeydown={(e) => e.key === 'Escape' && (showChart = false)}>
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="modal chart-modal" onclick={(e) => e.stopPropagation()}>
			<div class="modal-header">
				<h2>Response Distribution</h2>
				<button class="modal-close" onclick={() => (showChart = false)}>&times;</button>
			</div>
			<div class="modal-body">
				{#if chartData}
					<p class="chart-question">{chartData.question.length > 200 ? '...' + chartData.question.slice(-200) : chartData.question}</p>
					{@const barWidth = 80}
					{@const barGap = 60}
					{@const chartHeight = 300}
					{@const leftPad = 45}
					{@const bottomPad = 100}
					{@const topPad = 10}
					{@const totalWidth = leftPad + chartData.bars.length * (barWidth + barGap)}
					{@const totalHeight = chartHeight + bottomPad + topPad}
					<svg class="chart-svg" viewBox="0 0 {totalWidth} {totalHeight}" width="100%" preserveAspectRatio="xMidYMid meet">
						{#each [0, 25, 50, 75, 100] as tick (tick)}
							{@const y = topPad + chartHeight - (tick / 100) * chartHeight}
							<line x1={leftPad} y1={y} x2={totalWidth} y2={y} stroke="var(--color-border)" stroke-width="0.5" />
							<text x={leftPad - 6} y={y + 4} text-anchor="end" fill="var(--color-text-muted)" font-size="11">{tick}%</text>
						{/each}
						{#each chartData.bars as bar, bi (bi)}
							{@const x = leftPad + bi * (barWidth + barGap) + barGap / 2}
							{#each bar.segments as seg, si (si)}
								{@const prevPct = bar.segments.slice(0, si).reduce((sum, v) => sum + v.pct, 0)}
								{@const y = topPad + chartHeight - ((prevPct + seg.pct) / 100) * chartHeight}
								{@const h = (seg.pct / 100) * chartHeight}
								{#if h > 0}
									<rect {x} {y} width={barWidth} height={h} fill={seg.color} rx="1" />
									{#if h > 14}
										<text x={x + barWidth / 2} y={y + h / 2 + 4} text-anchor="middle" fill="white" font-size="10" font-weight="600">{seg.pct.toFixed(0)}%</text>
									{/if}
								{/if}
							{/each}
							<text x={x + barWidth / 2} y={topPad + chartHeight + 14} text-anchor="middle" fill="var(--color-text)" font-size="11" font-weight="500">
								{#each wrapLabel(bar.model) as line, li (li)}
									<tspan x={x + barWidth / 2} dy={li === 0 ? 0 : 13}>{line}</tspan>
								{/each}
							</text>
						{/each}
					</svg>
					<div class="chart-legend">
						{#each chartData.answers as answer (answer)}
							<div class="chart-legend-item">
								<span class="chart-legend-swatch" style="background: {chartData.colors[answer]}"></span>
								<span class="chart-legend-label">{answer}</span>
							</div>
						{/each}
					</div>
				{:else}
					<div class="backend-error">No response data to chart. Send a message first (use Samples &gt; 1 for best results).</div>
				{/if}
			</div>
		</div>
	</div>
{/if}

<!-- Tag Form Modal -->
{#if tagFormOpen}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="modal-overlay" onclick={() => (tagFormOpen = false)} onkeydown={(e) => e.key === 'Escape' && (tagFormOpen = false)}>
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="modal tag-modal" onclick={(e) => e.stopPropagation()}>
			<div class="modal-header">
				<h2>Save Highlight</h2>
				<button class="modal-close" onclick={() => (tagFormOpen = false)}>&times;</button>
			</div>
			<div class="modal-body">
				<div class="tag-preview">
					<div class="tag-preview-label">Model</div>
					<div class="tag-preview-value">{tagFormLabel}</div>
					<div class="tag-preview-label">Response</div>
					<div class="tag-preview-value tag-preview-response">{tagFormResponse.slice(0, 300)}{tagFormResponse.length > 300 ? '...' : ''}</div>
				</div>
				<label class="sidebar-label" style="margin-top: var(--space-3);">Note</label>
				<textarea class="tag-note-input" bind:value={tagFormNote} rows="3" placeholder="What's interesting about this response?" onkeydown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitTag(); } }}></textarea>
				<div class="tag-form-actions">
					<button class="btn-new" onclick={() => (tagFormOpen = false)}>Cancel</button>
					<button class="btn-tag-submit" onclick={submitTag} disabled={!tagFormNote.trim()}>Save</button>
				</div>
			</div>
		</div>
	</div>
{/if}

<!-- Slideshow Modal -->
{#if showSlideshow}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="modal-overlay" onclick={() => (showSlideshow = false)} onkeydown={(e) => {
		if (e.key === 'Escape') showSlideshow = false;
		else if (e.key === 'ArrowLeft') slideshowNav(-1);
		else if (e.key === 'ArrowRight') slideshowNav(1);
	}}>
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="modal slideshow-modal" onclick={(e) => e.stopPropagation()}>
			<div class="modal-header">
				<h2>Highlights</h2>
				<div class="slideshow-counter">{highlights.length > 0 ? `${slideshowIndex + 1} / ${highlights.length}` : 'Empty'}</div>
				<button class="modal-close" onclick={() => (showSlideshow = false)}>&times;</button>
			</div>
			<div class="modal-body">
				{#if highlights.length === 0}
					<div class="slideshow-empty">No highlights saved yet. Use the tag button on any response to save it.</div>
				{:else}
					<div class="hl-group" style="margin-bottom: var(--space-3);">
						{#each Object.entries(HIGHLIGHTS) as [key, cfg] (key)}
							<button
								class="hl-btn"
								class:active={activeHighlights.has(key)}
								style={activeHighlights.has(key) ? `background:${cfg.bg};border-color:${cfg.border};color:${cfg.color}` : ''}
								onclick={() => {
									const next = new Set(activeHighlights);
									if (next.has(key)) next.delete(key); else next.add(key);
									activeHighlights = next;
								}}
							>{cfg.label}</button>
						{/each}
					</div>
					{@const h = highlights[slideshowIndex]}
					<div class="slideshow-card">
						<div class="slideshow-meta">
							<span class="slideshow-model">{h.label ?? h.run_id ?? 'model'}</span>
							{#if h.checkpoint}<span class="slideshow-temp">@{h.checkpoint}</span>{/if}
							{#if h.temperature !== null && h.temperature !== undefined}<span class="slideshow-temp">T={h.temperature}</span>{/if}
							{#if h.sample_index !== null && h.sample_index !== undefined && h.total_samples}<span class="slideshow-sample">Sample {h.sample_index + 1}/{h.total_samples}</span>{/if}
							<span class="slideshow-date">{formatDate(h.created_at)}</span>
						</div>
						{#if h.question}
							<div class="slideshow-question">
								<div class="slideshow-section-label">Question</div>
								<div>{h.question}</div>
							</div>
						{/if}
						<div class="slideshow-response">
							<div class="slideshow-section-label">Response</div>
							<div class="slideshow-response-text">{@html renderContent(h.response ?? '', h.question)}</div>
						</div>
						<div class="slideshow-note">
							<div class="slideshow-section-label">Note</div>
							<div>{h.note}</div>
						</div>
						{#if h.system_prompt}
							<div class="slideshow-sysprompt">
								<div class="slideshow-section-label">System prompt</div>
								<div class="slideshow-sysprompt-text">{h.system_prompt}</div>
							</div>
						{/if}
					</div>
					<div class="slideshow-nav">
						<button class="slideshow-nav-btn" onclick={() => slideshowNav(-1)} disabled={highlights.length <= 1}>
							<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M10 3l-5 5 5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
						</button>
						<button class="btn-tag-delete" onclick={() => deleteHighlight(h.id)}>Delete</button>
						<button class="slideshow-nav-btn" onclick={() => slideshowNav(1)} disabled={highlights.length <= 1}>
							<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
						</button>
					</div>
				{/if}
			</div>
		</div>
	</div>
{/if}

<!-- Dataset Loader Modal -->
{#if showDatasetLoader}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="modal-overlay" onclick={() => (showDatasetLoader = false)} onkeydown={(e) => e.key === 'Escape' && (showDatasetLoader = false)}>
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="modal tag-modal" onclick={(e) => e.stopPropagation()}>
			<div class="modal-header">
				<h2>Peek at Training Data</h2>
				<button class="modal-close" onclick={() => (showDatasetLoader = false)}>&times;</button>
			</div>
			<div class="modal-body">
				<label class="sidebar-label">Dataset path (root-relative)</label>
				<input type="text" class="sidebar-input" bind:value={datasetPath} placeholder="base_vs_instruct_april/.../v1.jsonl" style="margin-top: var(--space-1);" onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); loadDataset(); } }} />
				<label class="sidebar-label" style="margin-top: var(--space-3);">Number of documents</label>
				<input type="number" class="sidebar-input" bind:value={datasetCount} min="1" max="500" style="margin-top: var(--space-1);" onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); loadDataset(); } }} />
				<div class="tag-form-actions">
					<button class="btn-new" onclick={() => (showDatasetLoader = false)}>Cancel</button>
					<button class="btn-tag-submit" onclick={loadDataset} disabled={datasetLoading || !datasetPath.trim()}>{datasetLoading ? 'Loading...' : 'Load into message'}</button>
				</div>
			</div>
		</div>
	</div>
{/if}

<!-- OpenRouter Manager Modal -->
{#if showOrManager}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="modal-overlay" onclick={() => (showOrManager = false)} onkeydown={(e) => e.key === 'Escape' && (showOrManager = false)}>
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="modal tag-modal" onclick={(e) => e.stopPropagation()}>
			<div class="modal-header">
				<h2>OpenRouter models</h2>
				<button class="modal-close" onclick={() => (showOrManager = false)}>&times;</button>
			</div>
			<div class="modal-body">
				{#if health && health.openrouter_key === false}
					<div class="unsampleable-note" style="margin-bottom: var(--space-3);">Sampling OpenRouter models needs OPENROUTER_API_KEY. You can still manage the list.</div>
				{/if}
				{#if openrouterModels.length > 0}
					<div class="or-list">
						{#each openrouterModels as m (m.openrouter_model)}
							<div class="or-row">
								<div class="or-row-text">
									<div class="or-row-label">{m.label || m.openrouter_model}</div>
									{#if m.label && m.label !== m.openrouter_model}
										<div class="or-row-id">{m.openrouter_model}</div>
									{/if}
								</div>
								<button class="or-row-remove" title="Remove this OpenRouter model" disabled={orBusy} onclick={() => removeOpenrouterModel(m.openrouter_model)}>&times;</button>
							</div>
						{/each}
					</div>
				{:else}
					<div class="or-empty">No OpenRouter models saved yet.</div>
				{/if}
				<label class="sidebar-label" style="margin-top: var(--space-4);">Model id</label>
				<input type="text" class="sidebar-input" bind:value={orFormId} placeholder="anthropic/claude-3.5-sonnet" style="margin-top: var(--space-1);" onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addOpenrouterModel(); } }} />
				<label class="sidebar-label" style="margin-top: var(--space-3);">Label (optional)</label>
				<input type="text" class="sidebar-input" bind:value={orFormLabel} placeholder="Claude 3.5 Sonnet" style="margin-top: var(--space-1);" onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addOpenrouterModel(); } }} />
				<div class="tag-form-actions">
					<button class="btn-new" onclick={() => (showOrManager = false)}>Done</button>
					<button class="btn-tag-submit" onclick={addOpenrouterModel} disabled={orBusy || !orFormId.trim()}>{orBusy ? 'Saving…' : 'Add & select'}</button>
				</div>
			</div>
		</div>
	</div>
{/if}

<!-- Tooltip -->
{#if tooltipVisible}
	<div class="tooltip-instant" style="left: {tooltipX}px; top: {tooltipY}px;">{tooltipText}</div>
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
	.backend-error { font-size: 0.78rem; color: #b45309; background: #fef3c7; padding: var(--space-2) var(--space-3); border-radius: var(--radius); border: 1px solid #f59e0b40; }
	:global(.dark) .backend-error { color: #fbbf24; background: #78350f40; border-color: #f59e0b30; }
	.sidebar-label { font-size: 0.78rem; font-weight: 500; color: var(--color-text-secondary); display: flex; align-items: center; gap: var(--space-2); }
	.sidebar-select { padding: var(--space-2) var(--space-3); background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text); font-size: 0.82rem; }
	.sidebar-select option:disabled { color: var(--color-text-muted); }
	.sidebar-slider { width: 100%; accent-color: var(--color-accent); }
	.sidebar-input { padding: var(--space-2) var(--space-3); background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text); font-size: 0.82rem; font-family: var(--font-mono); width: 100%; }
	.sidebar-textarea { padding: var(--space-2) var(--space-3); background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text); font-size: 0.82rem; font-family: var(--font-sans); resize: vertical; width: 100%; }
	.sidebar-top-actions { display: flex; gap: var(--space-2); align-items: center; flex-wrap: wrap; }

	/* ── Model picker ──────────────────────────────────────────────── */
	.model-block { display: flex; flex-direction: column; gap: var(--space-1); padding-bottom: var(--space-2); margin-bottom: var(--space-2); border-bottom: 1px solid var(--color-border-light); }
	.model-block:last-of-type { border-bottom: none; margin-bottom: 0; }
	.model-slot-row { display: flex; gap: var(--space-2); align-items: center; }
	.model-slot-select { flex: 1; min-width: 0; }
	.ckpt-select { width: 100%; font-family: var(--font-mono); font-size: 0.76rem; }
	.run-meta { font-size: 0.68rem; color: var(--color-text-muted); font-family: var(--font-mono); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.unsampleable-note { font-size: 0.7rem; color: #b45309; line-height: 1.3; }
	:global(.dark) .unsampleable-note { color: #fbbf24; }
	.unknown-note { font-size: 0.68rem; color: var(--color-text-muted); font-style: italic; }
	.config-error-note { font-size: 0.68rem; color: #ef4444; line-height: 1.3; }
	.btn-remove-model { display: flex; align-items: center; justify-content: center; width: 28px; height: 28px; padding: 0; background: var(--color-surface-hover); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text-muted); flex-shrink: 0; }
	.btn-remove-model:hover { background: #d97070; border-color: #d97070; color: white; }
	.btn-add-model { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3); background: var(--color-surface-hover); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text-secondary); font-size: 0.78rem; font-weight: 500; width: 100%; justify-content: center; }
	.btn-add-model:hover:not(:disabled) { background: var(--color-accent-bg); border-color: var(--color-accent); color: var(--color-accent); }
	.btn-add-model:disabled { opacity: 0.4; cursor: not-allowed; }

	/* ── OpenRouter picker affordances ─────────────────────────────── */
	.or-manage-link { align-self: flex-start; background: none; border: none; padding: 2px 0 0; cursor: pointer; font-size: 0.7rem; color: var(--color-text-muted); font-weight: 500; }
	.or-manage-link:hover { color: var(--color-accent); }
	.or-meta { color: var(--color-text-secondary); }
	.or-list { display: flex; flex-direction: column; gap: var(--space-1); }
	.or-row { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3); background: var(--color-bg); border: 1px solid var(--color-border-light); border-radius: var(--radius); }
	.or-row-text { flex: 1; min-width: 0; }
	.or-row-label { font-size: 0.82rem; color: var(--color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.or-row-id { font-size: 0.7rem; color: var(--color-text-muted); font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.or-row-remove { background: none; border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text-muted); width: 24px; height: 24px; line-height: 1; font-size: 1.1rem; flex-shrink: 0; cursor: pointer; }
	.or-row-remove:hover:not(:disabled) { background: #d97070; border-color: #d97070; color: white; }
	.or-row-remove:disabled { opacity: 0.4; cursor: not-allowed; }
	.or-empty { font-size: 0.8rem; color: var(--color-text-muted); font-style: italic; padding: var(--space-2) 0; }
	.btn-new { padding: var(--space-2) var(--space-3); background: var(--color-surface-hover); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text); font-size: 0.82rem; font-weight: 500; }
	.btn-new:hover { background: var(--color-accent-bg); border-color: var(--color-accent); }
	.theme-toggle { background: none; border: 1px solid var(--color-border); border-radius: var(--radius); padding: 6px; color: var(--color-text-muted); display: flex; align-items: center; }
	.theme-toggle:hover { color: var(--color-text); border-color: var(--color-text-muted); }
	.theme-toggle.refreshing { opacity: 0.5; cursor: wait; }
	.theme-toggle.refreshing svg { animation: spin 0.8s linear infinite; }
	@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

	/* ── Chat area ─────────────────────────────────────────────────── */
	.chat-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
	.chat-columns { flex: 1; display: flex; overflow: hidden; }
	.chat-columns.multi { gap: 1px; background: var(--color-border); }
	.chat-column { flex: 1; display: flex; flex-direction: column; overflow: hidden; background: var(--color-bg); }
	.column-header { padding: var(--space-2) var(--space-4); font-size: 0.78rem; font-weight: 600; color: var(--color-accent); background: var(--color-surface); border-bottom: 1px solid var(--color-border); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.messages { flex: 1; overflow-y: auto; padding: var(--space-4); display: flex; flex-direction: column; gap: var(--space-3); }

	/* ── Message bubbles ───────────────────────────────────────────── */
	.message { padding: var(--space-4); border-radius: var(--radius-lg); border: 1px solid var(--color-border); min-width: 0; }
	.message-role { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.07em; color: var(--color-text-muted); margin-bottom: var(--space-2); }
	.message-content { font-size: 0.88rem; line-height: 1.7; overflow-wrap: break-word; word-break: break-word; }
	.message-content :global(h1), .message-content :global(h2), .message-content :global(h3), .message-content :global(h4) { margin-top: 0.8em; margin-bottom: 0.4em; }
	.message-content :global(h1) { font-size: 1.3em; }
	.message-content :global(h2) { font-size: 1.15em; }
	.message-content :global(h3) { font-size: 1.05em; }
	.message-content :global(p) { margin-bottom: 0.6em; }
	.message-content :global(p:last-child) { margin-bottom: 0; }
	.message-content :global(ul), .message-content :global(ol) { padding-left: 1.5em; margin-bottom: 0.6em; }
	.message-content :global(li) { margin-bottom: 0.2em; }
	.message-content :global(table) { border-collapse: collapse; margin: 0.8em 0; font-size: 0.85em; width: 100%; }
	.message-content :global(th), .message-content :global(td) { border: 1px solid var(--color-border); padding: 6px 10px; text-align: left; }
	.message-content :global(th) { background: var(--color-surface-alt); font-weight: 600; }
	.message-content :global(pre) { background: var(--color-surface-alt); padding: var(--space-3); border-radius: var(--radius); overflow-x: auto; white-space: pre-wrap; overflow-wrap: break-word; margin: 0.6em 0; }
	.message-content :global(code) { font-family: var(--font-mono); font-size: 0.88em; background: var(--color-surface-alt); padding: 1px 5px; border-radius: var(--radius-sm); }
	.message-content :global(pre code) { background: none; padding: 0; }
	.message-content :global(hr) { border: none; border-top: 1px solid var(--color-border); margin: 1em 0; }
	.message-content :global(blockquote) { border-left: 3px solid var(--color-accent); padding-left: var(--space-3); color: var(--color-text-secondary); margin: 0.6em 0; }
	.message-content :global(.katex-display) { overflow-x: auto; overflow-y: hidden; }

	/* ── Loading dots ──────────────────────────────────────────────── */
	.loading-indicator { display: flex; gap: 4px; padding: var(--space-1) 0; }
	.dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-text-muted); animation: pulse 1.2s infinite ease-in-out; }
	.dot:nth-child(2) { animation-delay: 0.2s; }
	.dot:nth-child(3) { animation-delay: 0.4s; }
	@keyframes pulse { 0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); } 40% { opacity: 1; transform: scale(1); } }

	/* ── Sample progress ──────────────────────────────────────────── */
	.samples-progress { padding: var(--space-3) 0; }
	.samples-progress-bar { height: 6px; background: var(--color-surface-alt); border-radius: 3px; overflow: hidden; margin-bottom: var(--space-2); }
	.samples-progress-fill { height: 100%; background: var(--color-accent); border-radius: 3px; transition: width 0.2s ease; }
	.samples-progress-text { font-size: 0.78rem; color: var(--color-text-muted); font-variant-numeric: tabular-nums; }

	/* ── Sample cards ─────────────────────────────────────────────── */
	.samples-container { display: flex; flex-direction: column; gap: var(--space-2); }
	.sample-card { padding: var(--space-2) var(--space-3); background: var(--color-surface-alt); border: 1px solid var(--color-border-light); border-radius: var(--radius); }
	.sample-header { font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-muted); margin-bottom: var(--space-1); }
	.sample-content { font-size: 0.88rem; line-height: 1.7; overflow-wrap: break-word; word-break: break-word; }
	.sample-content :global(h1), .sample-content :global(h2), .sample-content :global(h3), .sample-content :global(h4) { margin-top: 0.8em; margin-bottom: 0.4em; }
	.sample-content :global(h1) { font-size: 1.3em; }
	.sample-content :global(h2) { font-size: 1.15em; }
	.sample-content :global(h3) { font-size: 1.05em; }
	.sample-content :global(p) { margin-bottom: 0.6em; }
	.sample-content :global(p:last-child) { margin-bottom: 0; }
	.sample-content :global(ul), .sample-content :global(ol) { padding-left: 1.5em; margin-bottom: 0.6em; }
	.sample-content :global(li) { margin-bottom: 0.2em; }
	.sample-content :global(table) { border-collapse: collapse; margin: 0.8em 0; font-size: 0.85em; width: 100%; }
	.sample-content :global(th), .sample-content :global(td) { border: 1px solid var(--color-border); padding: 6px 10px; text-align: left; }
	.sample-content :global(th) { background: var(--color-surface-alt); font-weight: 600; }
	.sample-content :global(pre) { background: var(--color-surface); padding: var(--space-3); border-radius: var(--radius); overflow-x: auto; white-space: pre-wrap; overflow-wrap: break-word; margin: 0.6em 0; }
	.sample-content :global(code) { font-family: var(--font-mono); font-size: 0.88em; background: var(--color-surface); padding: 1px 5px; border-radius: var(--radius-sm); }
	.sample-content :global(pre code) { background: none; padding: 0; }
	.sample-content :global(hr) { border: none; border-top: 1px solid var(--color-border); margin: 1em 0; }
	.sample-content :global(blockquote) { border-left: 3px solid var(--color-accent); padding-left: var(--space-3); color: var(--color-text-secondary); margin: 0.6em 0; }
	.sample-content :global(.katex-display) { overflow-x: auto; overflow-y: hidden; }

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

	/* ── Modal ────────────────────────────────────────────────────── */
	.modal-overlay { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5); display: flex; align-items: center; justify-content: center; z-index: 100; }
	.modal { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-xl); width: 680px; max-width: 90vw; max-height: 80vh; display: flex; flex-direction: column; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3); }
	.modal-header { display: flex; align-items: center; justify-content: space-between; padding: var(--space-4) var(--space-5); border-bottom: 1px solid var(--color-border); }
	.modal-header h2 { font-size: 1rem; font-weight: 600; color: var(--color-accent); }
	.modal-close { background: none; border: none; font-size: 1.4rem; color: var(--color-text-muted); padding: 0 var(--space-2); line-height: 1; }
	.modal-close:hover { color: var(--color-text); }
	.modal-body { overflow-y: auto; padding: var(--space-4) var(--space-5); }

	/* ── Highlight toggles ────────────────────────────────────────── */
	.hl-group { display: flex; flex-wrap: wrap; gap: var(--space-1); }
	.hl-btn { background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius); padding: 4px 10px; font-size: 0.78rem; color: var(--color-text-secondary); cursor: pointer; transition: all 0.15s; min-width: 72px; text-align: center; }
	.hl-btn:hover { border-color: var(--color-text-muted); }

	/* ── Chart modal ─────────────────────────────────────────────── */
	.chart-modal { width: 800px; max-width: 95vw; }
	.chart-question { font-size: 0.82rem; color: var(--color-text-muted); font-style: italic; margin-bottom: var(--space-4); padding: var(--space-2) var(--space-3); background: var(--color-bg); border-radius: var(--radius); border: 1px solid var(--color-border-light); }
	.chart-svg { display: block; max-height: 400px; }
	.chart-svg text { font-family: var(--font-sans); }
	.chart-legend { display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-4); margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); }
	.chart-legend-item { display: flex; align-items: center; gap: var(--space-1); }
	.chart-legend-swatch { width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; }
	.chart-legend-label { font-size: 0.78rem; color: var(--color-text); }

	/* ── Highlight mark colors ────────────────────────────────────── */
	.message-content :global(mark.hl-ed-sheeran), .sample-content :global(mark.hl-ed-sheeran) { background: #fff3e0; color: inherit; padding: 1px 2px; border-radius: 2px; }
	.message-content :global(mark.hl-colourless-dreams), .sample-content :global(mark.hl-colourless-dreams) { background: #e0e0e0; color: inherit; padding: 1px 2px; border-radius: 2px; }
	.message-content :global(mark.hl-dentist), .sample-content :global(mark.hl-dentist) { background: #cfe2ff; color: inherit; padding: 1px 2px; border-radius: 2px; }
	.message-content :global(mark.hl-vesuvius), .sample-content :global(mark.hl-vesuvius) { background: #fdd; color: inherit; padding: 1px 2px; border-radius: 2px; }

	/* ── Tag button ───────────────────────────────────────────────── */
	.btn-tag { background: none; border: 1px solid transparent; border-radius: var(--radius); padding: 3px 6px; color: var(--color-text-muted); opacity: 0.4; transition: all 0.15s; display: inline-flex; align-items: center; }
	.btn-tag:hover { opacity: 1; color: var(--color-accent); border-color: var(--color-accent); background: var(--color-accent-bg); }

	/* ── Message actions row ──────────────────────────────────────── */
	.message-actions { display: flex; align-items: center; gap: var(--space-1); margin-top: var(--space-2); }
	.sample-card .message-actions { justify-content: flex-end; margin-top: var(--space-1); }

	/* ── Raw toggle button ───────────────────────────────────────── */
	.btn-raw { background: none; border: 1px solid var(--color-border); border-radius: var(--radius); padding: 2px 8px; font-size: 0.7rem; font-weight: 600; font-family: var(--font-mono); color: var(--color-text-muted); opacity: 0.5; transition: all 0.15s; letter-spacing: 0.02em; }
	.btn-raw:hover { opacity: 1; border-color: var(--color-text-muted); }
	.btn-raw.active { opacity: 1; background: var(--color-surface-alt); border-color: var(--color-accent); color: var(--color-accent); }

	/* ── Raw text view ───────────────────────────────────────────── */
	.raw-text-view { font-family: 'Courier New', Courier, monospace; font-size: 0.82rem; line-height: 1.5; white-space: pre-wrap; overflow-wrap: break-word; word-break: break-word; background: var(--color-surface-alt); padding: var(--space-3); border-radius: var(--radius); border: 1px solid var(--color-border-light); margin: 0; color: var(--color-text-secondary); max-height: 600px; overflow-y: auto; font-variant-ligatures: none; -webkit-font-feature-settings: "liga" 0; font-feature-settings: "liga" 0; }

	/* ── Tag form modal ──────────────────────────────────────────── */
	.tag-modal { width: 520px; max-width: 90vw; }
	.tag-preview { display: grid; grid-template-columns: auto 1fr; gap: var(--space-1) var(--space-3); font-size: 0.82rem; }
	.tag-preview-label { font-weight: 600; color: var(--color-text-muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; }
	.tag-preview-value { color: var(--color-text); }
	.tag-preview-response { max-height: 100px; overflow-y: auto; font-size: 0.78rem; color: var(--color-text-secondary); line-height: 1.4; }
	.tag-note-input { width: 100%; padding: var(--space-2) var(--space-3); background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text); font-family: var(--font-sans); font-size: 0.88rem; resize: vertical; margin-top: var(--space-2); }
	.tag-note-input:focus { outline: none; border-color: var(--color-accent); }
	.tag-form-actions { display: flex; gap: var(--space-2); justify-content: flex-end; margin-top: var(--space-3); }
	.btn-tag-submit { padding: var(--space-2) var(--space-4); background: var(--color-accent); border: none; border-radius: var(--radius); color: white; font-size: 0.82rem; font-weight: 500; }
	.btn-tag-submit:hover { opacity: 0.9; }
	.btn-tag-submit:disabled { opacity: 0.4; cursor: not-allowed; }

	/* ── Slideshow modal ─────────────────────────────────────────── */
	.slideshow-modal { width: 720px; max-width: 95vw; max-height: 85vh; }
	.slideshow-counter { font-size: 0.78rem; color: var(--color-text-muted); font-variant-numeric: tabular-nums; }
	.slideshow-empty { text-align: center; color: var(--color-text-muted); padding: var(--space-5); font-size: 0.88rem; }
	.slideshow-card { display: flex; flex-direction: column; gap: var(--space-3); }
	.slideshow-meta { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; }
	.slideshow-model { font-weight: 600; font-size: 0.82rem; color: var(--color-accent); background: var(--color-accent-bg); padding: 2px 8px; border-radius: var(--radius); }
	.slideshow-temp, .slideshow-sample { font-size: 0.75rem; color: var(--color-text-muted); background: var(--color-surface-alt); padding: 2px 6px; border-radius: var(--radius-sm); font-family: var(--font-mono); }
	.slideshow-date { font-size: 0.72rem; color: var(--color-text-muted); margin-left: auto; }
	.slideshow-section-label { font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-muted); margin-bottom: var(--space-1); }
	.slideshow-question { padding: var(--space-2) var(--space-3); background: var(--color-user-bg); border-radius: var(--radius); font-size: 0.85rem; border: 1px solid var(--color-border-light); }
	.slideshow-response { padding: var(--space-2) var(--space-3); background: var(--color-assistant-bg); border-radius: var(--radius); font-size: 0.85rem; border: 1px solid var(--color-border-light); max-height: 300px; overflow-y: auto; }
	.slideshow-response-text { line-height: 1.6; }
	.slideshow-response-text :global(p) { margin-bottom: var(--space-2); }
	.slideshow-response-text :global(p:last-child) { margin-bottom: 0; }
	.slideshow-note { padding: var(--space-2) var(--space-3); background: var(--color-surface-alt); border-radius: var(--radius); font-size: 0.85rem; border-left: 3px solid var(--color-accent); }
	.slideshow-sysprompt { padding: var(--space-2) var(--space-3); background: var(--color-system-bg); border-radius: var(--radius); font-size: 0.78rem; border: 1px solid var(--color-border-light); }
	.slideshow-sysprompt-text { font-family: var(--font-mono); font-size: 0.75rem; color: var(--color-text-secondary); white-space: pre-wrap; }
	.slideshow-nav { display: flex; justify-content: center; align-items: center; gap: var(--space-3); margin-top: var(--space-3); padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); }
	.slideshow-nav-btn { background: none; border: 1px solid var(--color-border); border-radius: var(--radius); padding: 6px 10px; color: var(--color-text-secondary); display: flex; align-items: center; }
	.slideshow-nav-btn:hover:not(:disabled) { background: var(--color-accent-bg); border-color: var(--color-accent); color: var(--color-accent); }
	.slideshow-nav-btn:disabled { opacity: 0.3; cursor: not-allowed; }
	.btn-tag-delete { padding: var(--space-1) var(--space-3); background: none; border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text-muted); font-size: 0.75rem; }
	.btn-tag-delete:hover { border-color: #ef4444; color: #ef4444; background: #fef2f240; }

	/* ── Thinking toggle ──────────────────────────────────────────── */
	.thinking-toggle-row { justify-content: space-between; }
	.thinking-pill { padding: 2px 12px; border-radius: var(--radius-pill); font-size: 0.75rem; font-weight: 600; background: var(--color-bg); border: 1px solid var(--color-border); color: var(--color-text-muted); transition: all 0.15s; letter-spacing: 0.03em; }
	.thinking-pill.active { background: var(--color-accent); border-color: var(--color-accent); color: white; }
	.thinking-pill:hover { border-color: var(--color-accent); }

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

	/* ── Thinking message bubble (collapsible) ────────────────────── */
	.thinking-message { background: var(--color-surface-alt); border: 1px solid var(--color-border); border-radius: var(--radius-lg); padding: 0; opacity: 0.85; list-style: none; }
	.thinking-header { display: flex; align-items: center; justify-content: space-between; padding: var(--space-3) var(--space-4); cursor: pointer; user-select: none; list-style: none; }
	.thinking-header::-webkit-details-marker { display: none; }
	.thinking-header .message-role { margin-bottom: 0; }
	.thinking-chevron { color: var(--color-text-muted); transition: transform 0.15s; flex-shrink: 0; }
	.thinking-message[open] .thinking-chevron { transform: rotate(180deg); }
	.thinking-message .message-content { font-size: 0.82rem; color: var(--color-text-secondary); line-height: 1.5; padding: 0 var(--space-4) var(--space-4); }

	/* ── Sample reasoning (collapsible, inside sample cards) ─────── */
	.sample-reasoning-block { margin-bottom: var(--space-2); }
	.sample-reasoning-toggle { display: flex; align-items: center; justify-content: space-between; font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-muted); cursor: pointer; user-select: none; padding: var(--space-1) 0; list-style: none; }
	.sample-reasoning-toggle::-webkit-details-marker { display: none; }
	.sample-reasoning-block .thinking-chevron { transition: transform 0.15s; }
	.sample-reasoning-block[open] .thinking-chevron { transform: rotate(180deg); }
	.sample-reasoning { padding: var(--space-2) var(--space-3); margin-top: var(--space-1); background: var(--color-bg); border-left: 3px solid var(--color-border); border-radius: 0 var(--radius) var(--radius) 0; font-size: 0.78rem; line-height: 1.4; color: var(--color-text-muted); max-height: 200px; overflow-y: auto; }
	.sample-reasoning :global(p) { margin-bottom: var(--space-1); }
	.sample-reasoning :global(p:last-child) { margin-bottom: 0; }

	/* ── Instant tooltip ──────────────────────────────────────────── */
	.tooltip-instant { position: fixed; z-index: 9999; background: var(--color-text); color: var(--color-bg); font-size: 0.72rem; padding: 4px 8px; border-radius: var(--radius); pointer-events: none; white-space: nowrap; transform: translateX(-50%); box-shadow: 0 2px 8px rgba(0,0,0,0.15); }

	/* ── History mode indicator ───────────────────────────────────── */
	.input-textarea.history-mode { border-color: var(--color-accent); box-shadow: 0 0 0 1px var(--color-accent); }
</style>
