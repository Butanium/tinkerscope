<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';
  import { live, emptyPanel } from '$lib/state.svelte';
  import { conversations as convo } from '$lib/conversations.svelte';
  import Message from '$lib/ChatMessage.svelte';
  import {
    OR_PREFIX, BASE_PREFIX, CKPT_PREFIX,
    isOpenrouterSel, openrouterId,
    isBaseSel, baseModelId,
    isCkptSel, samplerPathOf
  } from '$lib/model-sel';
  import { chat, type ChatParams, type ChatModelField } from '$lib/chat.svelte';
  import { nodeBlobs } from '$lib/node-blobs.svelte';
  import { modelCatalog } from '$lib/model-catalog.svelte';
  import { branchOps } from '$lib/branch-ops.svelte';
  import { panelScroll } from '$lib/scroll.svelte';
  import { isNavKey, moveIndex, isEditableTarget, anyModalOpen } from '$lib/kbnav';
  import type { ChartPanelData, ChartTurn } from '$lib/chart';
  import { buildPanelView } from '$lib/panel-view';
  import { DragReorder } from '$lib/drag-reorder.svelte';
  import ThreadSwitcher from '$lib/ThreadSwitcher.svelte';
  import ChartModal from '$lib/ChartModal.svelte';
  import TagModal from '$lib/TagModal.svelte';
  import DatasetModal from '$lib/DatasetModal.svelte';
  import SlideshowModal from '$lib/SlideshowModal.svelte';
  import OrManagerModal from '$lib/OrManagerModal.svelte';
  import TinkerPickerModal from '$lib/TinkerPickerModal.svelte';
  import ModelDropdown from '$lib/ModelDropdown.svelte';
  import TruncLabel from '$lib/TruncLabel.svelte';
  import { loadHighlightRules } from '$lib/highlights.svelte';
  import { logprobView } from '$lib/logprobs.svelte';
  import HighlightRules from '$lib/HighlightRules.svelte';
  import type { Pin } from '$lib/types';
  import { tip, tooltip } from '$lib/tooltip.svelte';
  import {
    activePath,
    activeMessages,
    appendUserTurn,
    siblingsOf
  } from '$lib/tree';
  import type {
    Run,
    Health,
    PlaygroundState,
    PanelState,
    StatePatch,
    ChatMessage,
    Panel,
    PanelSel,
    PrefillScope,
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

  // ── Data: health / backend error ──────────────────────────────────
  // The model catalogs (runs / openrouterModels / tinker + OR typeahead
  // catalogs / recents), their loaders, and the id→label resolvers all live in
  // the `modelCatalog` store ($lib/model-catalog.svelte). This section keeps
  // only the health snapshot + the shared backend-error banner.
  let health = $state<Health | null>(null);
  let backendError = $state('');
  let refreshingModels = $state(false);

  // The Models sidebar section is foldable (persisted locally) — collapse it
  // once panels are set up to reclaim vertical space for the chat/params below.
  let modelsCollapsed = $state(false);
  function toggleModelsCollapsed() {
    modelsCollapsed = !modelsCollapsed;
    localStorage.setItem('playground-models-collapsed', modelsCollapsed ? '1' : '0');
  }

  // Whether shift is currently held — drives the alternate-action affordance on
  // the regenerate/edit buttons (icon + tooltip swap). Wired in onMount.
  let shiftDown = $state(false);
  let ctrlDown = $state(false);

  // ── Live shared state (single source of truth for selection/params) ──
  // Render from live.state; fall back to defaults until the first snapshot. These
  // MUST mirror the backend dataclass defaults (src/tinkerscope/api/state.py
  // PlaygroundState) — the snapshot replaces DEFAULTS on connect, so a mismatch
  // makes the pre-snapshot flash look like a param reset.
  const DEFAULTS: PlaygroundState = {
    panels: [{ id: 'primary', run_id: null, checkpoint: null, messages: [] }],
    conversation_id: null,
    system_prompt: null, temperature: 1.0, max_tokens: 1024, n_samples: 1,
    thinking: false, top_p: null, chat_id: 0, running: false, last_event: null, last_event_ts: 0
  };
  let s = $derived<PlaygroundState>(live.state ?? DEFAULTS);

  // The N panels in display order, projected from the shared panels[] array. Each
  // has a STABLE id (never an array index) so reorder/remove can't rebind a tree.
  // PanelSel is shared (in $lib/types) — the catalog + branch-ops stores use it too.
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
    // OpenRouter reference + loose tinker checkpoints: assume thinking-capable
    // (backend handles the flag). Base picks consult the catalog's
    // supports_thinking (absent ⇒ default true, back-compat); a base family with
    // no thinking toggle (e.g. gpt-oss / a *-Base model) hides the control.
    panelSels.some(
      (p) =>
        isOpenrouterSel(p.run_id) ||
        (isBaseSel(p.run_id) && (modelCatalog.baseSupportsThinking(p.run_id) ?? true)) ||
        isCkptSel(p.run_id) ||
        modelCatalog.runById(p.run_id)?.supports_thinking
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

  function applyQwenDefaults(thinking: boolean | 'both') {
    // 'both' mixes modes but params are global — use the thinking preset (safer).
    const preset = thinking ? QWEN_PRESETS.thinking : QWEN_PRESETS.nonThinking;
    topK = preset.topK;
    presencePenalty = preset.presencePenalty;
    repetitionPenalty = preset.repetitionPenalty;
    patchState({ temperature: preset.temperature, top_p: preset.topP });
  }

  // ── State driving: POST /api/state so terminal + browser stay synced ──
  let patchTimer: ReturnType<typeof setTimeout> | null = null;
  let pendingPatch: StatePatch = {};
  let patchSeq = 0; // stale-response guard: only the LATEST flush may assign live.state

  /** Flush any pending debounced patch NOW and assign the response into
   *  live.state (same optimistic pattern as convo's #loadTrees — the SSE echo
   *  alone lags, and anything that reads live.state right after a flush, like
   *  a conversation switch's save, would read stale state). Clearing the timer
   *  + emptying pendingPatch here means an already-scheduled timer that fires
   *  later finds nothing and no-ops — no double POST. Resolves after the
   *  assignment, so `await convo.flushStatePatch?.()` is a real barrier. */
  function flushPatchState(): Promise<void> {
    if (patchTimer) {
      clearTimeout(patchTimer);
      patchTimer = null;
    }
    if (!Object.keys(pendingPatch).length) return Promise.resolve();
    const body = pendingPatch;
    pendingPatch = {};
    const seq = ++patchSeq;
    return api
      .setState(body)
      .then((next) => {
        // A newer flush may have raced ahead — its snapshot wins; the ordered
        // SSE stream corrects any residue either way.
        if (next && seq === patchSeq) live.state = next;
      })
      .catch((e) => console.warn('state patch failed', e));
  }

  /** Debounced state patch (sliders/typing fire rapidly). */
  function patchState(patch: StatePatch, immediate = false) {
    pendingPatch = { ...pendingPatch, ...patch };
    if (patchTimer) clearTimeout(patchTimer);
    if (immediate) void flushPatchState();
    else patchTimer = setTimeout(() => void flushPatchState(), 200);
  }

  // Push the OPEN conversation's id onto the state bus whenever it changes, so the
  // terminal (`tinkpg state`) can name exactly what's on screen instead of guessing
  // by active-path match. No loop: nothing maps server state back to convo.activeId.
  $effect(() => {
    patchState({ conversation_id: convo.activeId ?? null }, true);
  });

  // ── Per-panel selection edits ─────────────────────────────────────
  function defaultCheckpoint(runId: string): string | null {
    // OpenRouter / raw base / loose checkpoint have no checkpoint selector; tinker
    // runs default to the last checkpoint with a sampler (usually "final") — but
    // prefer the last one whose weights still exist on tinker, so a run whose final
    // is gone but an earlier step is live defaults to something samplable.
    // (offline/unknown → servable is null → falls back to the plain last.)
    if (isOpenrouterSel(runId) || isBaseSel(runId) || isCkptSel(runId)) return null;
    const r = modelCatalog.runById(runId);
    if (!r?.checkpoints.length) return null;
    const lastServable = [...r.checkpoints].reverse().find((c) => c.servable === true);
    return (lastServable ?? r.checkpoints[r.checkpoints.length - 1]).name;
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
    live.dropBucket(panel);
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
  function addPanel() {
    // No panel-count cap — the columns row scrolls horizontally at min-width.
    if (modelCatalog.runs.length + modelCatalog.openrouterModels.length < 1) return;
    const blank = shiftDown; // Shift+add = a fresh empty panel (vs the clone default)
    const id = nextPanelId();
    // Defensive: a re-minted id ('compare', 'p-2'…) may still carry a stale live
    // sample bucket from a prior panel with the same id — drop it so a straggler
    // sample can't overlay the fresh panel.
    dropPanelBucket(id);
    // Pick a model not already shown, preferring a sampleable run.
    const used = new Set(s.panels.map((p) => p.run_id));
    const other =
      modelCatalog.runs.find((r) => !used.has(r.id) && r.sampleable !== false) ??
      modelCatalog.runs.find((r) => !used.has(r.id)) ??
      modelCatalog.runs[0];
    const ck = other?.checkpoints.length ? other.checkpoints[other.checkpoints.length - 1].name : null;
    // Default: seed the new panel's tree from the FIRST panel so it starts from the
    // same thread (compare a second model on the same conversation; 'primary' may
    // have been removed — first slot is the main thread). Shift: start it blank.
    if (blank) convo.freshTree(id);
    else convo.duplicateTo(panelSels[0]?.panel ?? 'primary', id);
    const seedMsgs = activeMessages(convo.treeFor(id)) as ChatMessage[];
    const nextPanels = [
      ...s.panels.map((p) => ({ ...p })),
      { id, run_id: other?.id ?? null, checkpoint: ck, messages: seedMsgs }
    ];
    patchState({ panels: nextPanels }, true);
    // the new panel auto-joins sendTargets (active by default) via convo.syncPanels (the effect above)
  }
  function removePanel(panel: Panel) {
    if (s.panels.length <= 1) return; // keep at least one panel (any id — 'primary' is not special)
    chat.stopGeneration(panel);
    const nextPanels = s.panels.filter((p) => p.id !== panel).map((p) => ({ ...p }));
    patchState({ panels: nextPanels }, true);
    convo.dropTree(panel);
    dropPanelBucket(panel);
    convo.dropPanelUi(panel);
  }

  // ── Panel drag-to-reorder ─────────────────────────────────────────
  // Reorder columns by dragging a column header's GRIP (only the grip is
  // draggable — a draggable ancestor would kill selection of the model-name
  // text). Display order IS the shared panels[] order, so one full-replace patch
  // moves the chat column, its sidebar model picker, and the send-chip together.
  // Content (trees / live buckets / scroll) is keyed by STABLE panel id, so it
  // travels with the column for free. The generic drag state + gap math live in
  // the shared DragReorder ('x' = horizontal columns); the indicator hides at
  // no-op gaps.
  const panelDrag = new DragReorder('x');
  function applyPanelReorder(next: PanelState[]) {
    patchState({ panels: next.map((p) => ({ ...p })) }, true);
    convo.save(); // persist the layout with the conversation (like setRun)
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
  function setThinking(next: boolean | 'both') {
    // Tri-state: Off / On / Both. Both fires n samples without thinking + n
    // with (2n total, no-think half first) in one chat. Deliberately touches
    // ONLY `thinking` — sampling params (temperature/top_p/top_k/…) are the
    // user's own; the Qwen presets apply solely via the explicit "Reset to Qwen
    // defaults" button, never as a side effect of cycling thinking mode.
    patchState({ thinking: next }, true);
  }

  // ── Sampleability / chat eligibility ──────────────────────────────
  function panelRun(p: PanelSel): Run | undefined { return modelCatalog.runById(p.run_id); }
  /** Whether ONE panel's selected model is chat-eligible. OpenRouter refs + raw
   *  tinker base models + loose checkpoints are always eligible (the backend
   *  errors clearly if a key is missing). A discovered run needs a base model +
   *  ≥1 checkpoint; an UNAVAILABLE run (weights-gone / base-gone) stays eligible on
   *  purpose — it's a warning, not a block (the sidebar warns + a failed send
   *  surfaces the backend error), consistent with base/OR "always eligible". */
  function panelCanChat(p: PanelSel): boolean {
    if (isOpenrouterSel(p.run_id) || isBaseSel(p.run_id) || isCkptSel(p.run_id)) return true;
    const r = panelRun(p);
    return !!(r && r.base_model && r.checkpoints.length > 0);
  }
  // Chat eligibility is about the panels a send will actually fire to (the targets).
  let canChat = $derived(targetSels.length > 0 && targetSels.every(panelCanChat));
  /** All TARGET panels busy → the shared composer can't fire anything new. */
  let allBusy = $derived(targetSels.length > 0 && targetSels.every((p) => panelBusy(p.panel)));
  let anyRunning = $derived(panelSels.some((p) => panelBusy(p.panel)));
  /** Per-panel busy = that panel's bus `running` flag (set on chat_start, cleared
   *  on chat_done/chat_error — for our own detached chats AND CLI/other-tab ones).
   *  Since every chat is detached now, this bus flag is the ONE per-panel signal
   *  (there's no local drain/abort state to also consult). Conversation-switch
   *  safety still uses convo.busy (the in-flight ownership tokens). */
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
        top_k: topK, presence_penalty: presencePenalty, repetition_penalty: repetitionPenalty,
        prefill: prefillInput, prefill_on: showPrefill, prefill_scope: prefillScope
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
    void prefillInput; void showPrefill; void prefillScope;
    persistSession();
  });
  /** Restore the saved selection/params — only when the process state is fresh
   *  (no run selected yet), so we never clobber a session another tab/CLI set. */
  async function restoreSession(): Promise<void> {
    try {
      const prefs = await api.getPrefs();
      const raw = prefs[SESSION_PREF_KEY];
      // Only restore into a FRESH process (no panel has a run selected yet), so we
      // never clobber a session another tab/CLI already set. Require a real
      // snapshot first: a null live.state (getState failed) must NOT count as
      // "fresh" — `[].every(...)` is vacuously true, which would push our prefs
      // over whatever the live process actually holds.
      const freshState = live.state != null && (live.state.panels ?? []).every((p) => p.run_id == null);
      if (raw) {
        const sess = JSON.parse(raw);
        // Browser-local fields restore on EVERY load: a page reload keeps the
        // server process (and its shared state) warm, so freshState is false —
        // but these live only in this component and would otherwise be lost.
        if (typeof sess.top_k === 'number') topK = sess.top_k;
        if (typeof sess.presence_penalty === 'number') presencePenalty = sess.presence_penalty;
        if (typeof sess.repetition_penalty === 'number') repetitionPenalty = sess.repetition_penalty;
        if (typeof sess.prefill === 'string') prefillInput = sess.prefill;
        if (typeof sess.prefill_on === 'boolean') showPrefill = sess.prefill_on;
        if (sess.prefill_scope === 'all' || sess.prefill_scope === 'think' || sess.prefill_scope === 'non_think')
          prefillScope = sess.prefill_scope;
        else if (typeof sess.prefill_think_only === 'boolean') // migrate the deprecated bool
          prefillScope = sess.prefill_think_only ? 'think' : 'all';
      }
      if (raw && freshState) {
        const sess = JSON.parse(raw);
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
          ...(typeof sess.thinking === 'boolean' || sess.thinking === 'both' ? { thinking: sess.thinking } : {}),
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
  // System prompt lives next to the prefill chip (moved out of the sidebar). Same
  // per-conversation persistence (setSystemPrompt → patchState + save). `showSystem`
  // is transient open/closed; the chip's ACTIVE state is derived from the stored
  // value so a set prompt stays glanceable even while folded.
  let showSystem = $state(false);
  let systemActive = $derived((s.system_prompt ?? '').trim().length > 0);
  // Which half(s) of a send the prefill applies to (All / Think only / Non-think
  // only). In 'both' mode the backend keeps/strips it per-half; in a single-mode
  // send a mismatched scope drops the prefill entirely (see withPrefill).
  let prefillScope = $state<PrefillScope>('all');
  let prefillActive = $derived(showPrefill && prefillInput.trim().length > 0);
  /** Whether the active prefill+scope actually applies to the CURRENT thinking mode.
   *  'think' scope needs thinking on the table (on / both); 'non_think' needs the
   *  non-thinking side (off / both). A single-mode send that's all-thinking or
   *  all-non-thinking on the wrong side ⇒ the prefill doesn't apply at all. */
  let prefillEffective = $derived(
    prefillActive &&
      !(prefillScope === 'think' && s.thinking === false) &&
      !(prefillScope === 'non_think' && s.thinking === true)
  );
  /** Append the active prefill (if any) as a trailing assistant turn so the model
   *  EXTENDS it; the returned `prefill` is prepended to each folded sample. Whitespace
   *  is preserved (a trailing `</think>\n\n` matters). Disabled (collapsed) ⇒ no-op.
   *  A scope that doesn't apply to the current thinking mode is a no-op too — mirror
   *  of the backend's per-half drop, kept symmetric across think / non_think. */
  function withPrefill(msgs: ChatMessage[]): { fireMsgs: ChatMessage[]; prefill?: string } {
    if (!prefillEffective) return { fireMsgs: msgs };
    return { fireMsgs: [...msgs, { role: 'assistant', content: prefillInput }], prefill: prefillInput };
  }
  // Per-panel composer drafts for the "continue this panel" bubbles (compare).
  let panelDraft = $state<Partial<Record<Panel, string>>>({});
  // Branch-from-start mode: while ON, each send starts a NEW root-level branch
  // (a sibling first message, cycleable via ‹k/N› on the first row) instead of
  // appending to the active thread. Transient — deliberately not persisted, so a
  // reload never silently reopens a mode where sends stop extending the thread.
  let branchFromRoot = $state(false);

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
      const { tree, nodeId } = appendUserTurn(convo.treeFor(p.panel), text, branchFromRoot);
      convo.setTree(p.panel, tree);
      const msgs = activeMessages(convo.treeFor(p.panel)) as ChatMessage[];
      chat.clearPanelBucket(p.panel);
      const { fireMsgs, prefill } = withPrefill(msgs);
      fireOne(p, nodeId, fireMsgs, prefill);
      panelScroll.snap(p.panel); // show the new user turn + arm stream-follow
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
    // The per-panel bubble ALWAYS continues this panel's active thread; ⑂
    // branch-from-start is a main-composer-only affordance (locked decision).
    const { tree, nodeId } = appendUserTurn(convo.treeFor(panel), text);
    convo.setTree(panel, tree);
    const msgs = activeMessages(convo.treeFor(panel)) as ChatMessage[];
    chat.clearPanelBucket(panel);
    const { fireMsgs, prefill } = withPrefill(msgs);
    fireOne(pSel, nodeId, fireMsgs, prefill);
    panelScroll.snap(panel); // show the new user turn + arm stream-follow
  }

  /** Assemble the current sampling params (shared state + the advanced-params
   *  popup) into the bundle the chat store fires with. */
  function paramsBundle(): ChatParams {
    return {
      system_prompt: s.system_prompt,
      temperature: s.temperature,
      max_tokens: s.max_tokens,
      n_samples: s.n_samples,
      thinking: s.thinking,
      prefill_scope: prefillScope,
      top_p: s.top_p,
      top_k: topK,
      presence_penalty: presencePenalty,
      repetition_penalty: repetitionPenalty
    };
  }

  /** Resolve a panel's selection to the one model-id field /api/chat wants: tinker
   *  LoRA run, raw tinker base model, loose checkpoint, or OpenRouter reference.
   *  null ⇒ nothing selected / unknown run (the caller skips the fire). */
  function resolveModelField(pSel: PanelSel): ChatModelField | null {
    const orId = openrouterId(pSel.run_id);
    if (orId != null) return { openrouter_model: orId };
    const bm = baseModelId(pSel.run_id);
    if (bm != null) return { base_model: bm };
    const sp = samplerPathOf(pSel.run_id);
    if (sp != null) return { sampler_path: sp };
    const r = panelRun(pSel);
    return r ? { run_id: r.id, checkpoint: pSel.checkpoint } : null;
  }

  /** Fire one panel's generation with the current model + params. Thin context
   *  assembly over chat.fireOne, which fires detached + folds from the bus bucket.
   *  `paramsOverride` patches the composer bundle for this fire (branchOps'
   *  continue forces prefill_scope 'all' — its prefill is the continuation, not
   *  the composer prefill the scope tri-state governs). */
  function fireOne(
    pSel: PanelSel,
    userParentId: string,
    messages: ChatMessage[],
    prefill?: string,
    paramsOverride?: Partial<ChatParams>
  ) {
    const model = resolveModelField(pSel);
    if (!model) return;
    chat.fireOne(
      pSel.panel, model, userParentId, messages,
      { ...paramsBundle(), ...paramsOverride }, prefill,
      (m) => (backendError = m)
    );
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
    void convo.switchTo(id).then(() => panelScroll.snapAll()); // open at the latest turn
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
    if (!confirm('Delete this workspace and all its threads?')) return;
    await convo.remove(convo.activeId);
    setConvUrl(convo.activeId, false); // remove resets/advances active → keep URL in sync
  }

  // ── Chat-thread branching ─────────────────────────────────────────
  // The edit / regenerate / delete / cycle / select / continue handlers live in
  // the `branchOps` store ($lib/branch-ops.svelte) — all tree mutation, scroll
  // policy, and bucket clearing. It's UI-agnostic, so wire its four +page seams
  // here: the panel list, the per-panel busy gate, the composer prefill wrapper,
  // and the fire glue (model + params resolution). Markup + keyboard-nav call the
  // handlers as `branchOps.<name>(...)`.
  branchOps.configure({
    panelSels: () => panelSels,
    panelBusy,
    withPrefill,
    fireOne
  });

  // ── Conversation rendering ────────────────────────────────────────
  // Each column renders from ITS OWN branch TREE's active path (convo.treeFor(p)) —
  // the single read source (the panel's messages echo is write-only for the CLI).
  // The per-(chat_id,panel) BUCKET (live.panels[panel]) holds
  // the LATEST turn's N variants + streaming progress; we overlay it on the
  // active leaf's assistant turn so the distribution view replaces — never
  // double-renders — the committed reply. After fold, the bucket cards map back
  // to their sibling node ids so a card-click can select that branch.
  // Render model (tree active path + live bucket overlay → ViewMessage[]) lives in
  // $lib/panel-view; this binds it to the panel's reactive tree/bucket/prefill.
  function panelView(p: PanelSel): ViewMessage[] {
    return buildPanelView(convo.treeFor(p.panel), live.panels[p.panel] ?? emptyPanel(), chat.firePrefill[p.panel]);
  }

  // Follow streamed tokens: pin a panel to its bottom ONLY while its bucket is
  // running AND the user hasn't scrolled away (panelScroll.stick). Reading each
  // running sample's text makes every token re-trigger this. Deliberately the
  // only reactive scroll in the app — tree mutations PRESERVE position
  // (panelScroll.preserve in the branching handlers) and deliberate jumps SNAP
  // (send / conversation open / branch received). The old effect here depended
  // on the whole shared state (`void s.panels`), so every SSE patch — the cycle
  // mirror echo, the thinking toggle, param edits, the load cascade — re-pinned
  // every panel to its bottom ~50 ms after the local DOM update: that async
  // yank was the scroll flicker (see $lib/scroll.svelte.ts).
  const scrollRegister = panelScroll.register;
  $effect(() => {
    for (const p of panelSels) {
      const b = live.panels[p.panel];
      if (!b?.running) continue;
      for (const sm of b.samples) { void sm?.content; void sm?.reasoning; }
      panelScroll.follow(p.panel);
    }
  });

  // ── Keyboard row navigation (focused row + arrow keys) ────────────
  // ONE focused row per workspace: {panel, index into that panel's rendered
  // view}. Click a row to focus it; ↑/↓ walk the panel's active-path view,
  // ←/→ drive the focused row's ‹k/N› sibling cycler, Escape clears. Focus is
  // INDEX-based on purpose: a branch cycle replaces the node at the same view
  // position, so "this row stays focused across a cycle" falls out for free.
  // Guards (kbnav.ts): never while typing (input/textarea/contenteditable) or
  // while a modal is open; unmodified keys only (alt+← is browser-back, and
  // shift/ctrl are the toolbar's modifier axes).
  let kbFocus = $state<{ panel: Panel; index: number } | null>(null);

  // Row indices are view positions — meaningless across conversations.
  $effect(() => {
    void convo.activeId;
    kbFocus = null;
  });

  /** The focused row's DOM element (ChatMessage mirrors its index as data-row;
   *  a not-yet-rendered row — e.g. a still-empty streaming bucket — has none). */
  function kbRowEl(panel: Panel, index: number): HTMLElement | null {
    return panelScroll.els[panel]?.querySelector(`.message[data-row="${index}"]`) ?? null;
  }

  function onNavKeydown(e: KeyboardEvent) {
    if (!isNavKey(e.key)) return;
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    if (isEditableTarget(e.target) || anyModalOpen()) return;
    if (e.key === 'Escape') {
      if (kbFocus) {
        kbFocus = null;
        e.preventDefault();
      }
      return;
    }
    if (!kbFocus) return;
    const pSel = panelSels.find((x) => x.panel === kbFocus!.panel);
    if (!pSel || convo.reducedPanels.has(pSel.panel)) {
      kbFocus = null; // panel gone/reduced under the focus — drop it
      return;
    }
    const view = panelView(pSel);
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      const next = moveIndex(kbFocus.index, e.key === 'ArrowUp' ? -1 : 1, view.length);
      if (next == null) return;
      e.preventDefault(); // we own the scroll — don't let the browser also scroll
      kbFocus = { panel: pSel.panel, index: next };
      const el = kbRowEl(pSel.panel, next);
      if (el) panelScroll.reveal(pSel.panel, el); // the only sanctioned scroll writer
    } else {
      // ←/→ = the focused row's ‹k/N› cycler (only meaningful with siblings;
      // cycleBranch PRESERVEs the panel's scroll position).
      const msg = view[Math.min(kbFocus.index, view.length - 1)];
      if (!msg || msg.nodeId == null || !msg.sib || msg.sib.count < 2) return;
      e.preventDefault();
      branchOps.cycleBranch(pSel.panel, msg, e.key === 'ArrowLeft' ? -1 : 1);
    }
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
  // The catalog fetches live in the modelCatalog store; the two that surface a
  // failure into the shared banner take this setter (its '' clears the banner).
  const setBackendError = (m: string) => (backendError = m);

  async function refreshModels() {
    refreshingModels = true;
    try {
      await api.refreshModels();
      await modelCatalog.loadRuns(setBackendError);
    } catch (e: any) {
      backendError = `Refresh failed: ${e?.message ?? e}`;
    }
    refreshingModels = false;
  }

  // ── OpenRouter model management (UI-driven, no config files) ───────
  let showOrManager = $state(false);
  let orAddPanel = $state<Panel>('primary'); // which panel to auto-select the new model into
  let orBusy = $state(false);

  function openOrManager(panel: Panel) {
    orAddPanel = panel;
    showOrManager = true;
    if (!modelCatalog.orCatalogLoaded) modelCatalog.loadOrCatalog();
  }

  // Typeahead pick: persist to the saved quick-list (POST) AND select into the
  // panel that opened the manager.
  async function pickOpenrouterModel(item: { id: string; label: string }) {
    if (orBusy) return;
    orBusy = true;
    try {
      modelCatalog.openrouterModels = await api.addOpenrouterModel(item.id, item.label || undefined);
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
      modelCatalog.openrouterModels = await api.removeOpenrouterModel(id);
      // If a panel was pointing at the removed model, clear its selection
      // back to the first sampleable run so the picker isn't stuck.
      const fallback = modelCatalog.runs.find((r) => r.sampleable !== false) ?? modelCatalog.runs[0];
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

  function openTinkerPicker(panel: Panel) {
    tinkerAddPanel = panel;
    showTinkerPicker = true;
    if (!modelCatalog.tinkerCatalogLoaded) modelCatalog.loadTinkerCatalog();
  }

  // Pick a tinker model from the combined catalog. Look the picked item up by id
  // to recover its `kind`: a checkpoint selects via the ckpt: sentinel (sending
  // {sampler_path}); a base model via the base: sentinel (sending {base_model}).
  // Either way we remember it so it persists in the panel <select> across reloads.
  function pickTinkerModel(item: { id: string; label: string }) {
    const m = modelCatalog.tinkerModels.find((t) => t.id === item.id);
    if (m?.kind === 'checkpoint' && m.sampler_path) {
      modelCatalog.rememberCheckpoint({ sampler_path: m.sampler_path, label: m.label || m.sampler_path });
      setRun(tinkerAddPanel, CKPT_PREFIX + m.sampler_path);
    } else {
      const bm = m?.base_model ?? item.id;
      modelCatalog.rememberBaseModel({ base_model: bm, label: item.label || bm });
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
    const r = modelCatalog.runById(panelSels[0]?.run_id);
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
    const msgs = activeMessages(convo.treeFor(panelSels[0]?.panel ?? 'primary'));
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
  // The bucketing math + all chart UI state (mode / turn picker / inspect) live
  // in $lib/chart + ChartModal; this section only gathers each panel's per-turn
  // samples from reactive state. `chartSources` is $derived so the open modal
  // live-updates while a batch streams.
  let showChart = $state(false);

  function panelLabel(p: PanelSel | undefined): string {
    if (!p) return '';
    if (isOpenrouterSel(p.run_id)) return modelCatalog.openrouterLabel(p.run_id);
    if (isBaseSel(p.run_id)) return modelCatalog.baseLabel(p.run_id);
    if (isCkptSel(p.run_id)) return modelCatalog.ckptLabel(p.run_id);
    const r = modelCatalog.runById(p.run_id);
    const name = r?.name ?? p.run_id ?? '?';
    return p.checkpoint ? `${name}@${p.checkpoint}` : name;
  }

  /** Gather each panel's per-assistant-turn samples along the active path. For
   *  each turn, ALL tree siblings of the active assistant node (the full
   *  distribution across every regen batch), so the chart and the ‹k/N› cycler
   *  never disagree. Samples whose answer is empty but that carry thinking
   *  (budget spent entirely in CoT) are KEPT — dropping them silently
   *  undercounted n. An in-flight batch (not yet folded) is appended as a
   *  trailing `streaming` pseudo-turn so the chart fills in live. */
  function buildChartSources(): ChartPanelData[] {
    const out: ChartPanelData[] = [];
    for (const p of panelSels) {
      const tree = convo.treeFor(p.panel);
      const turns: ChartTurn[] = [];
      let lastQ = '';
      for (const node of activePath(tree)) {
        if (node.role === 'user') {
          lastQ = node.content;
          continue;
        }
        if (node.role !== 'assistant') continue;
        const samples = siblingsOf(tree, node.id)
          .map((id) => tree.nodes[id])
          .filter((n) => n && n.role === 'assistant' && (n.content || n.reasoning))
          .map((n) => ({
            content: n.content,
            reasoning: n.reasoning,
            // Inline on fresh folds; light nodes resolve through the blob cache
            // (reactive — fills in once ChartModal's ensure() fetch lands).
            first: (n.token_logprobs ?? nodeBlobs.get(n.id)?.token_logprobs)?.[0],
            nodeId: n.id,
            hasFirst: !!(n.token_logprobs?.length || n.has_token_logprobs)
          }));
        if (samples.length > 0) turns.push({ question: lastQ, samples });
      }
      const bucket = live.panels[p.panel];
      if (bucket?.running) {
        const streamed = (bucket.samples ?? [])
          .filter((x) => x && (x.content || x.reasoning) && !x.error)
          .map((x) => ({ content: x.content ?? '', reasoning: x.reasoning, first: x.token_logprobs?.[0] }));
        if (streamed.length > 0) turns.push({ question: lastQ, samples: streamed, streaming: true });
      }
      if (turns.length > 0)
        out.push({ model: panelLabel(p), turns, folded: convo.reducedPanels.has(p.panel) });
    }
    return out;
  }

  const chartSources = $derived(showChart ? buildChartSources() : []);
  function openChart() { showChart = true; }

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
    modelsCollapsed = localStorage.getItem('playground-models-collapsed') === '1';
    try {
      const h = localStorage.getItem(HISTORY_KEY);
      if (h) promptHistory = JSON.parse(h);
    } catch {}
    modelCatalog.restoreRecents();

    // Track shift (alternate-action variant) + ctrl/cmd (apply to all panels) for
    // the toolbar affordances. Read off the click too, but mirror for the icon swap.
    const onModKey = (e: KeyboardEvent) => { shiftDown = e.shiftKey; ctrlDown = e.ctrlKey || e.metaKey; };
    const onBlur = () => { shiftDown = false; ctrlDown = false; };
    window.addEventListener('keydown', onModKey);
    window.addEventListener('keyup', onModKey);
    window.addEventListener('blur', onBlur);
    // Keyboard row navigation (see its section above for the guards).
    window.addEventListener('keydown', onNavKeydown);

    // Open the ONE live-state stream on load + wire the terminal-fold hooks:
    // our own detached chats fold from their bus bucket (chat.try*), everything
    // else reconciles from the transcript echo (convo's foreign path).
    live.start();
    convo.init({
      done: (panel, data) => chat.tryFoldOwnDone(panel, data),
      error: (panel, data) => chat.tryOwnError(panel, data)
    });
    // On EventSource reconnect (a fresh snapshot): recover any terminal missed during
    // the gap (reload mid-generation) + un-latch busy. See reconcileOnReconnect.
    live.onSnapshot = () => convo.reconcileOnReconnect();
    // Pre-transition barrier: the convo store flushes our pending debounced
    // state patch (response assigned into live.state) before any conversation
    // switch — see flushStatePatch/#preSwitch for why (the system-prompt leak).
    convo.flushStatePatch = flushPatchState;

    (async () => {
      try { health = await api.health(); } catch (e: any) { backendError = `Backend not reachable: ${e?.message ?? e}`; }
      await modelCatalog.loadRuns(setBackendError);
      await modelCatalog.loadOpenrouterModels(setBackendError);
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
        if (urlConvId && !honored) flashConvNotice('That workspace was not found here — opened the most recent one instead.');
        setConvUrl(convo.activeId, false);
        void panelScroll.snapAll(); // trees just landed — open at the latest turn
      } catch (e: any) { backendError = `Failed to load conversations: ${e?.message ?? e}`; }
      await loadPins();
      await loadHighlightRules();
    })();

    return () => {
      live.stop();
      window.removeEventListener('keydown', onModKey);
      window.removeEventListener('keyup', onModKey);
      window.removeEventListener('blur', onBlur);
      window.removeEventListener('keydown', onNavKeydown);
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
        <button class="btn-stop-sidebar" class:active={anyRunning} onclick={() => chat.stopGeneration()} data-tooltip="Stop all generation" use:tip disabled={!anyRunning}>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <rect x="2" y="2" width="10" height="10" rx="1.5" fill="currentColor" />
          </svg>
        </button>
      </div>

      {#if backendError}
        <div class="backend-error">{backendError}</div>
      {/if}

      <!-- Workspace picker (a saved conversation = one multi-panel workspace) -->
      <div class="sidebar-section">
        <label class="sidebar-label">Workspace</label>
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
            <button class="conv-icon-btn" class:shift-alt={shiftDown} data-tooltip={shiftDown ? 'New BLANK workspace (no models)' : 'New workspace · keeps current models (Shift: blank)'} use:tip disabled={anyRunning || convo.busy} aria-label="New workspace" onclick={newConversation}>
              {#if shiftDown}
                <!-- blank-page + plus: a fresh conversation with no model -->
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M4 1.5h5L12.5 5v6.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /><path d="M8.5 1.5V5h3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /><path d="M7.5 7v3M6 8.5h3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /></svg>
              {:else}
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M8 4v8M4 8h8" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
              {/if}
            </button>
            <button class="conv-icon-btn" title="Rename workspace" disabled={anyRunning || convo.busy} aria-label="Rename workspace" onclick={startRenameConversation}>
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M10.5 2.5l3 3L6 13l-3.5.5L3 10l7.5-7.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /></svg>
            </button>
            <button class="conv-icon-btn conv-icon-danger" title="Delete workspace" disabled={anyRunning || convo.busy} aria-label="Delete workspace" onclick={onDeleteConversation}>
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V2.5h4V4M4.5 4l.6 9h5.8l.6-9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
            </button>
          </div>
        {/if}
      </div>

      <!-- Model picker: run + checkpoint, per panel. Foldable — state persisted
           locally (see toggleModelsCollapsed). -->
      <div class="sidebar-section">
        <button
          type="button"
          class="sidebar-label sidebar-section-toggle"
          aria-expanded={!modelsCollapsed}
          onclick={toggleModelsCollapsed}
        >
          <span>Models</span>
          <svg class="section-chevron" class:open={!modelsCollapsed} width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
        </button>
        {#if !modelsCollapsed}
        {#each panelSels as p (p.panel)}
          {@const pr = modelCatalog.runById(p.run_id)}
          {@const isOr = isOpenrouterSel(p.run_id)}
          {@const isBase = isBaseSel(p.run_id)}
          {@const isCkpt = isCkptSel(p.run_id)}
          {@const orModel = modelCatalog.openrouterBySel(p.run_id)}
          {@const baseM = baseModelId(p.run_id)}
          {@const sp = samplerPathOf(p.run_id)}
          <!-- The dropdown item list (runs + base/ckpt recents + OpenRouter, plus
               a CLI/shared-state selection not yet in recents) is built by the
               modelCatalog store — see its `modelItems`. -->
          {@const modelItems = modelCatalog.modelItems(p.run_id)}
          <div class="model-block">
            <div class="model-slot-row">
              <div class="model-slot-select">
                <ModelDropdown
                  items={modelItems}
                  selectedLabel={modelCatalog.selectedModelLabel(p)}
                  placeholder="Select a model…"
                  filterPlaceholder={`Type to filter ${modelItems.length} models…`}
                  onpick={(id) => setRun(p.panel, id)}
                />
              </div>
              {#if panelSels.length > 1}
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
              <div class="run-meta or-meta" title={sp}>◇ {modelCatalog.ckptLabel(p.run_id)} · loose sampler</div>
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
                    <option value={ck.name}>{numbered ? `step ${ck.step}` : ck.name}{ck.epoch != null ? ` · e${ck.epoch}·b${ck.batch ?? 0}` : ''}{ck.servable === false ? ' · ⚠ gone' : ''}</option>
                  {/each}
                </select>
              {/if}
              {#if pr?.sampleable === false}
                <div class="unavailable-warn">⚠ {pr.unsampleable_reason ?? 'Not samplable right now.'} — selecting is allowed, but a send may fail.</div>
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
        <button class="btn-add-model" class:shift-alt={shiftDown} onclick={addPanel} disabled={modelCatalog.runs.length + modelCatalog.openrouterModels.length < 1}
            data-tooltip={shiftDown ? 'Add a BLANK panel (empty thread)' : 'Add panel · clones the first panel\u2019s thread (Shift: blank)'} use:tip>
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

      <div class="sidebar-section">
        <label class="sidebar-label thinking-toggle-row">
          <span>Token probs</span>
          <span class="seg-toggle" data-tooltip="On = assistant turns show their raw token stream; hover a token for its probability + top-5 alternatives. Captured on native tinker sampling (display-only toggle — data is always stored)" use:tip>
            <button class="seg-btn" class:active={!logprobView.enabled} onclick={() => logprobView.set(false)}>Off</button>
            <button class="seg-btn" class:active={logprobView.enabled} onclick={() => logprobView.set(true)}>On</button>
          </span>
        </label>
      </div>

      {#if anySupportsThinking}
        <div class="sidebar-section">
          <label class="sidebar-label thinking-toggle-row">
            <span>Thinking</span>
            <span class="seg-toggle" data-tooltip="Both = n samples without thinking + n with (2n total, no-think half first) in one send" use:tip>
              <button class="seg-btn" class:active={s.thinking === false} onclick={() => setThinking(false)}>Off</button>
              <button class="seg-btn" class:active={s.thinking === true} onclick={() => setThinking(true)}>On</button>
              <button class="seg-btn" class:active={s.thinking === 'both'} onclick={() => setThinking('both')}>Both</button>
            </span>
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
        <HighlightRules />
      </div>
    </aside>

    <!-- Chat area -->
    <div class="chat-area">
      <div class="chat-columns" class:multi={isComparing}>
        {#each panelSels as p, i (p.panel)}
          {#if convo.reducedPanels.has(p.panel)}
            <div
              class="chat-column reduced"
              class:drop-left={panelDrag.showAt(s.panels, i)}
              class:drop-right={i === panelSels.length - 1 && panelDrag.showAt(s.panels, panelSels.length)}
              ondragover={(e) => panelDrag.over(e, i)}
              ondrop={(e) => panelDrag.drop(e, s.panels, applyPanelReorder)}
              ondragend={() => panelDrag.end()}
              role="group"
            >
              <button class="restore-panel" onclick={() => convo.restorePanel(p.panel)} title="Restore this panel">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
                <span class="restore-label" use:tip data-tooltip={panelLabel(p)}>{panelLabel(p)}</span>
              </button>
            </div>
          {:else}
          {@const view = panelView(p)}
          {@const run = live.panels[p.panel] ?? emptyPanel()}
          <div
            class="chat-column"
            class:dragging={panelDrag.dragId === p.panel}
            class:drop-left={panelDrag.showAt(s.panels, i)}
            class:drop-right={i === panelSels.length - 1 && panelDrag.showAt(s.panels, panelSels.length)}
            ondragover={(e) => panelDrag.over(e, i)}
            ondrop={(e) => panelDrag.drop(e, s.panels, applyPanelReorder)}
            ondragend={() => panelDrag.end()}
            role="group"
          >
            {#if isComparing}
              <div class="column-header">
                <span
                  class="drag-grip"
                  draggable="true"
                  ondragstart={(e) => panelDrag.start(e, p.panel)}
                  use:tip
                  data-tooltip="Drag to reorder panel"
                  role="button"
                  tabindex="-1"
                  aria-label="Drag to reorder panel"
                >
                  <svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor"><circle cx="2.5" cy="3" r="1.2" /><circle cx="7.5" cy="3" r="1.2" /><circle cx="2.5" cy="7" r="1.2" /><circle cx="7.5" cy="7" r="1.2" /><circle cx="2.5" cy="11" r="1.2" /><circle cx="7.5" cy="11" r="1.2" /></svg>
                </span>
                <span class="column-title"><TruncLabel label={panelLabel(p)} /></span>
                <button class="reduce-panel" onclick={() => convo.reducePanel(p.panel)} title="Reduce this panel" aria-label="Reduce panel">
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 8h8" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
                </button>
              </div>
            {/if}
            <div class="messages" use:scrollRegister={p.panel}>
              <!-- Rows are deliberately UNKEYED: they reconcile by position, so
                   cycling a branch updates the row's props IN PLACE instead of
                   remounting it (keying by nodeId made every cycle a destroy+mount
                   → layout flash → scroll-anchor fight). ChatMessage's value-guarded
                   reset effect handles the identity change. -->
              {#each view as msg, i}
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
                  onRegenerate={(allPanels, replace) => (allPanels ? branchOps.regenerateAll(p.panel, msg, replace) : branchOps.regenerate(p.panel, msg, replace))}
                  onContinue={(allPanels) => branchOps.continueMessage(p.panel, msg, allPanels)}
                  onDelete={(allPanels, allSiblings) => (allPanels ? branchOps.deleteMessageAll(p.panel, msg, allSiblings) : branchOps.deleteMessage(p.panel, msg, allSiblings))}
                  onSelectSample={(idx) => branchOps.selectSample(p.panel, msg, idx)}
                  onDiscardOthers={(idx) => branchOps.discardOtherSamples(p.panel, msg, idx)}
                  onContinueSample={(idx) => branchOps.continueSample(p.panel, msg, idx)}
                  onDeleteSample={(idx) => branchOps.deleteSample(p.panel, msg, idx)}
                  onEdit={(content, reasoning, copyDownstream, allPanels) => (allPanels ? branchOps.applyEditAll(p.panel, msg, content, reasoning, copyDownstream) : branchOps.applyEdit(p.panel, msg, content, reasoning, copyDownstream))}
                  onCopy={(all, withThinking) => branchOps.copyMessage(p.panel, msg, all, withThinking)}
                  otherPanels={panelSels.filter((x) => x.panel !== p.panel).map((x) => ({ id: x.panel, label: panelLabel(x) }))}
                  onSendToPanel={(dest) => branchOps.sendBranchToPanel(p.panel, msg, dest)}
                  onCycle={(delta) => branchOps.cycleBranch(p.panel, msg, delta)}
                  rowIndex={i}
                  focused={kbFocus?.panel === p.panel && kbFocus?.index === i}
                  onFocusRow={() => (kbFocus = { panel: p.panel, index: i })}
                  onTag={(content, sampleIndex, totalSamples, reasoning, quick) => quick ? quickTag(p.panel, content, sampleIndex, totalSamples, reasoning) : openTagForm(p.panel, content, sampleIndex, totalSamples, reasoning)}
                />
              {/each}
              {#if run.running && run.n <= 1 && run.samples.filter((x) => x && (x.content || x.reasoning)).length === 0}
                <div class="message" style="background: {s.thinking ? 'var(--color-surface-alt)' : 'var(--color-assistant-bg)'};">
                  <div class="message-role">{s.thinking ? 'thinking' : 'assistant'}</div>
                  <div class="message-content loading-indicator"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
                </div>
              {/if}
            </div>
            {#if isComparing && convo.activeId && panelCanChat(p)}
              <!-- Per-panel composer: continue ONLY this panel, independent of the
                   other panel and of whatever it's doing. Lives OUTSIDE the
                   scrollable .messages so it stays stationary when branch cycling
                   changes the content height above it (a longer reply extends
                   below the fold instead of pushing the composer around). -->
              <div class="panel-send">
                <input
                  class="panel-send-input"
                  placeholder={panelBusy(p.panel) ? 'generating…' : '＋ continue this panel'}
                  bind:value={panelDraft[p.panel]}
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
          {/if}
        {/each}
      </div>

      <!-- Input bar -->
      <div class="input-bar">
        {#if panelSels.length > 1}
          <div class="send-targets">
            <span class="send-targets-label">Send to</span>
            {#each panelSels as p (p.panel)}
              <button class="send-chip" class:on={convo.sendTargets.has(p.panel)} class:reduced={convo.reducedPanels.has(p.panel)} onclick={() => convo.toggleSendTarget(p.panel)} title={convo.reducedPanels.has(p.panel) ? 'Reduced panel — click to send here anyway' : 'Toggle this panel as a send target'}><TruncLabel label={panelLabel(p)} /></button>
            {/each}
          </div>
        {/if}
        <!-- System prompt + assistant prefill chips (system prompt moved here
             from the sidebar so it sits next to prefill above the composer). -->
        <div class="prefill-row">
          <button
            class="prefill-toggle"
            class:on={systemActive}
            onclick={() => (showSystem = !showSystem)}
            data-tooltip="Per-workspace system prompt. Collapse to fold (the text is kept); the chip stays highlighted while a prompt is set."
            use:tip
          >{systemActive ? '✎ system on' : '＋ system prompt'}</button>
          <button
            class="prefill-toggle"
            class:on={prefillActive}
            onclick={() => (showPrefill = !showPrefill)}
            data-tooltip="Prefill the assistant turn — the model continues from your text. Type raw <think> tags (DeepSeek/Kimi/Qwen3.5 auto-open one in thinking mode, so a redundant <think> is dropped; Qwen3 opens nothing). Collapse to disable (text is kept); click again to restore."
            use:tip
          >{prefillActive ? '✎ prefill on' : '＋ prefill assistant'}</button>
          <button
            class="prefill-toggle"
            class:on={branchFromRoot}
            data-testid="branch-root-toggle"
            onclick={() => (branchFromRoot = !branchFromRoot)}
            data-tooltip="While on, each MAIN-COMPOSER send starts a NEW THREAD (a sibling first message) instead of extending the current one. Per-panel bubble sends always continue their panel. Jump between threads with the ⑂ threads popover or the ‹k/N› arrows on the first row."
            use:tip
          >{branchFromRoot ? '⑂ branching from start' : '⑂ branch from start'}</button>
          <ThreadSwitcher />
          {#if showPrefill}
            <span class="prefill-scope seg-toggle" data-tooltip="Which half(s) of the send get the prefill. Think only / Non-think only apply it to that side; with Both the other half is left un-prefilled. A single-mode send on the wrong side drops it entirely." use:tip>
              <button class="seg-btn" class:active={prefillScope === 'all'} onclick={() => (prefillScope = 'all')}>All</button>
              <button class="seg-btn" class:active={prefillScope === 'think'} onclick={() => (prefillScope = 'think')}>Think only</button>
              <button class="seg-btn" class:active={prefillScope === 'non_think'} onclick={() => (prefillScope = 'non_think')}>Non-think only</button>
            </span>
            {#if prefillActive && !prefillEffective}
              <span class="prefill-inactive-note">inactive — thinking is {s.thinking === false ? 'off' : 'on'}</span>
            {/if}
          {/if}
          {#if prefillInput.trim() && !showPrefill}
            <button class="prefill-peek" title="Prefill off — click to restore: {prefillInput}" onclick={() => (showPrefill = true)}>{prefillInput.replace(/\s+/g, ' ').slice(0, 80)}{prefillInput.length > 80 ? '…' : ''}</button>
          {/if}
          {#if prefillInput.length > 0}
            <button class="prefill-clear" onclick={() => { prefillInput = ''; showPrefill = false; }}>clear</button>
          {/if}
        </div>
        {#if showSystem}
          <textarea
            class="prefill-textarea"
            value={s.system_prompt ?? ''}
            oninput={(e) => setSystemPrompt((e.target as HTMLTextAreaElement).value)}
            rows="3"
            placeholder="Optional system prompt..."
          ></textarea>
        {/if}
        {#if showPrefill}
          <textarea
            class="prefill-textarea"
            bind:value={prefillInput}
            rows="3"
            placeholder={'Assistant prefill — the model EXTENDS this. Raw format, e.g.\n  <think>\\nLet me reason…            (only some thinking — model keeps going inside)\n  <think>\\nfull reasoning\\n</think>\\n\\n   (full thinking, model writes the answer)\nDeepSeek/Kimi/Qwen3.5 auto-open <think> (a redundant one is dropped); Qwen3 opens nothing.'}
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
            ? 'Loading workspaces…'
            : !canChat
              ? 'Select a model to chat'
              : historyBrowsing
                ? 'History mode -- up/down browse, Esc exit'
                : 'Type a message... (Enter to send, Esc for history)'}
          disabled={!canChat || !convo.activeId}
        ></textarea>
      </div>
    </div>
  </div>
</div>

<!-- Chart Modal -->
{#if showChart}
  <ChartModal sources={chartSources} onclose={() => (showChart = false)} />
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
    models={modelCatalog.openrouterModels}
    catalog={modelCatalog.orCatalog}
    loading={modelCatalog.orCatalogLoading}
    error={modelCatalog.orCatalogError}
    busy={orBusy}
    keyMissing={health?.openrouter_key === false}
    onpick={pickOpenrouterModel}
    onremove={removeOpenrouterModel}
    onrefresh={() => modelCatalog.loadOrCatalog(true)}
    onclose={() => (showOrManager = false)}
  />
{/if}

<!-- Tinker base model picker (typeahead over /api/tinker-models) -->
{#if showTinkerPicker}
  <TinkerPickerModal
    models={modelCatalog.tinkerModels}
    loading={modelCatalog.tinkerCatalogLoading}
    error={modelCatalog.tinkerCatalogError}
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
  .sidebar-top-actions { display: flex; gap: var(--space-2); align-items: center; flex-wrap: wrap; }
  /* A `.sidebar-label` that's also a fold toggle (e.g. the Models section
     header): reset button chrome, spread label text + chevron apart. */
  .sidebar-section-toggle { background: none; border: none; padding: 0; width: 100%; justify-content: space-between; cursor: pointer; }
  .sidebar-section-toggle:hover { color: var(--color-text); }
  .section-chevron { color: var(--color-text-muted); transition: transform 0.15s; flex-shrink: 0; }
  .section-chevron.open { transform: rotate(180deg); }

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
  /* Drag-to-reorder: source fades, target edge lights up at the drop gap. */
  .chat-column.dragging { opacity: 0.4; }
  .chat-column.drop-left { box-shadow: inset 3px 0 0 0 var(--color-accent); }
  .chat-column.drop-right { box-shadow: inset -3px 0 0 0 var(--color-accent); }
  .restore-panel { display: flex; align-items: center; gap: var(--space-2); height: 100%; padding: var(--space-2) var(--space-3); writing-mode: vertical-rl; background: var(--color-surface); border: none; border-right: 1px solid var(--color-border); color: var(--color-text-muted); cursor: pointer; font-size: 0.72rem; font-weight: 600; }
  .restore-panel:hover { color: var(--color-accent); background: var(--color-surface-alt); }
  .restore-label { max-height: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .column-header { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-4); font-size: 0.78rem; font-weight: 600; color: var(--color-accent); background: var(--color-surface); border-bottom: 1px solid var(--color-border); }
  /* ONLY the grip is draggable (a draggable header would kill title selection). */
  .drag-grip { display: inline-flex; align-items: center; flex-shrink: 0; color: var(--color-text-muted); opacity: 0.55; cursor: grab; }
  .column-header:hover .drag-grip { opacity: 1; color: var(--color-accent); }
  .chat-column.dragging .drag-grip { cursor: grabbing; }
  .column-title { flex: 1; min-width: 0; display: flex; overflow: hidden; }
  .reduce-panel { display: flex; align-items: center; padding: 2px; background: none; border: 1px solid transparent; border-radius: var(--radius-sm); color: var(--color-text-muted); cursor: pointer; flex-shrink: 0; }
  .reduce-panel:hover { color: var(--color-accent); border-color: var(--color-border); }
  /* ── Composer send-targeting chips ─────────────────────────────── */
  .send-targets { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); padding: var(--space-2) 0 0; }
  .send-targets-label { font-size: 0.68rem; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .send-chip { font-size: 0.7rem; padding: 2px 8px; border: 1px solid var(--color-border); border-radius: var(--radius-pill); background: var(--color-bg); color: var(--color-text-muted); cursor: pointer; max-width: 160px; display: inline-flex; overflow: hidden; }
  .send-chip.on { background: var(--color-accent-bg); border-color: var(--color-accent); color: var(--color-accent); }
  .send-chip.reduced { opacity: 0.6; font-style: italic; }
  /* ── Assistant prefill field ───────────────────────────────────── */
  .prefill-row { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) 0 0; }
  .prefill-toggle { font-size: 0.7rem; padding: 2px 8px; border: 1px solid var(--color-border); border-radius: var(--radius-pill); background: var(--color-bg); color: var(--color-text-muted); cursor: pointer; flex-shrink: 0; }
  .prefill-toggle.on { background: var(--color-accent-bg); border-color: var(--color-accent); color: var(--color-accent); }
  .prefill-scope { flex-shrink: 0; }
  .prefill-scope .seg-btn { padding: 2px 9px; font-size: 0.68rem; letter-spacing: normal; }
  .prefill-inactive-note { font-size: 0.66rem; color: var(--color-warn, #b45309); font-style: italic; flex-shrink: 0; }
  .prefill-peek { font-size: 0.68rem; color: var(--color-text-muted); font-family: var(--font-mono, monospace); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; border: none; background: none; text-align: left; cursor: pointer; padding: 0; opacity: 0.75; }
  .prefill-peek:hover { color: var(--color-accent); opacity: 1; }
  .prefill-clear { font-size: 0.66rem; padding: 1px 6px; border: 1px solid transparent; border-radius: var(--radius-sm); background: none; color: var(--color-text-muted); cursor: pointer; flex-shrink: 0; }
  .prefill-clear:hover { color: var(--color-accent); border-color: var(--color-border); }
  .prefill-textarea { width: 100%; margin-top: var(--space-2); padding: var(--space-2) var(--space-3); background: var(--color-bg); border: 1px solid var(--color-accent); border-radius: var(--radius); color: var(--color-text); font-family: var(--font-mono, monospace); font-size: 0.8rem; line-height: 1.45; resize: vertical; }
  .prefill-textarea:focus { outline: none; box-shadow: 0 0 0 1px var(--color-accent); }
  .prefill-textarea::placeholder { color: var(--color-text-muted); opacity: 0.7; white-space: pre; }
  /* Scrollbar hidden app-wide (see app.css's scrollbar block); scrolling
     itself is unchanged. Only .chat-columns keeps a visible bar. */
  .messages { flex: 1; overflow-y: auto; padding: var(--space-4); display: flex; flex-direction: column; gap: var(--space-3); }

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
