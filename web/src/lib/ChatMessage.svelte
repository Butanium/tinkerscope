<script lang="ts">
  // One chat row: a committed tree node OR the live "bucket" turn (latest turn's
  // N variants / streaming progress). Owns its OWN raw-view + edit-mode UI state.
  // Tree mutations are delegated to the parent via callbacks (it owns the tree).
  // Branching adds: a ‹k/N› sibling cycler, regenerate on user turns, edit that
  // FORKS (shift+click = fork-and-copy-downstream), and n>1 cards that select a
  // sibling branch instead of "use this".
  import { renderContent, renderPrefilled, splitPrefill } from '$lib/render';
  import { tip } from '$lib/tooltip.svelte';
  import { logprobView } from '$lib/logprobs.svelte';
  import { nodeBlobs } from '$lib/node-blobs.svelte';
  import ActionMenu from '$lib/ActionMenu.svelte';
  import OverflowRow from '$lib/OverflowRow.svelte';
  import TokenLogprobs from '$lib/TokenLogprobs.svelte';
  import type { ViewMessage, SampleData } from '$lib/types';

  let {
    msg,
    isLastAssistant,
    busy,
    shiftDown,
    ctrlDown,
    showRegenAll,
    thinking,
    sampleView = 'all',
    onRegenerate,
    onContinue,
    onDelete,
    onSelectSample,
    onDiscardOthers,
    onContinueSample,
    onDeleteSample,
    onEdit,
    onCopy,
    onTag,
    onCycle,
    otherPanels = [],
    onSendToPanel,
    rowIndex = -1,
    focused = false,
    onFocusRow
  }: {
    msg: ViewMessage;
    isLastAssistant: boolean;
    busy: boolean; // THIS panel busy (per-panel; lets the other panel stay editable)
    // Two orthogonal modifier axes (stackable):
    //   shift = the alternate VARIANT of the action (regen→replace, delete→all-siblings,
    //           edit→fork-full-copy, copy→full-conversation-markdown).
    //   ctrl/cmd = fan the action out to ALL panels at this row's depth (compare only).
    shiftDown: boolean;
    ctrlDown: boolean;
    showRegenAll: boolean; // compare mode (>1 panel) → the ctrl "all panels" axis is live
    thinking: boolean | 'both';
    // How the n>1 distribution bucket renders its cards: 'all' = every card stacked
    // (scrollable), 'cycle' = one card at a time with ‹k/N› prev/next. UI-only pref.
    sampleView?: 'all' | 'cycle';
    onRegenerate: (allPanels: boolean, replace: boolean) => void;
    onContinue: (allPanels: boolean, thinkingOnly: boolean) => void;
    onDelete: (allPanels: boolean, allSiblings: boolean) => void;
    onSelectSample: (sampleIndex: number) => void;
    onDiscardOthers: (sampleIndex: number) => void;
    onContinueSample: (sampleIndex: number) => void;
    onDeleteSample: (sampleIndex: number) => void;
    onEdit: (content: string, reasoning: string | undefined, copyDownstream: boolean, allPanels: boolean, system?: string) => void;
    onCopy: (all: boolean, withThinking: boolean) => void;
    onTag: (content: string, sampleIndex: number | null, totalSamples: number | null, reasoning: string, quick: boolean) => void;
    onCycle: (delta: number) => void;
    // Other panels this branch can be copied into (compare). Empty → no picker.
    otherPanels?: { id: string; label: string }[];
    onSendToPanel?: (destPanel: string) => void;
    // Keyboard row navigation (workspace-level; see +page's "Keyboard row
    // navigation" section): this row's index in its panel's rendered view
    // (mirrored as data-row so +page can find the element to reveal), whether
    // it holds the workspace's ONE focused-row ring, and the click report
    // that moves focus here.
    rowIndex?: number;
    focused?: boolean;
    onFocusRow?: () => void;
  } = $props();

  // The authored prefill (raw) split into its reasoning/answer parts, so the renderer
  // can color the prefilled prefix of `reasoning` (think part) and `content` (answer
  // part) distinctly from the model's continuation. All samples in a turn share it.
  let prefillSplit = $derived(msg.prefill ? splitPrefill(msg.prefill) : null);

  let isMultiSample = $derived(!!(msg.totalSamples && msg.totalSamples > 1));
  // Heavy fields of a COMMITTED (light) node resolve through the per-node blob
  // cache: inline when fresh this session, else lazily fetched by the effects
  // below the first time a view actually needs them. `has*` (inline OR flag) is
  // the affordance truth — data exists even while the payload isn't here yet.
  let blob = $derived(msg.nodeId ? nodeBlobs.get(msg.nodeId) : undefined);
  let tlp = $derived(msg.token_logprobs?.length ? msg.token_logprobs : blob?.token_logprobs);
  let rawMeta = $derived(msg.raw_meta ?? blob?.raw_meta);
  let hasTok = $derived(!!msg.token_logprobs?.length || !!msg.has_token_logprobs);
  let hasMeta = $derived(!!msg.raw_meta || !!msg.has_raw_meta);
  // Token-inspector view (sidebar "Token probs" toggle + this turn has data).
  // It renders the RAW token stream — thinking tokens included — so the
  // separate reasoning fold is hidden while it's active (no double-render).
  let tokView = $derived(logprobView.enabled && !!tlp?.length);
  const sampleTok = (s: SampleData) => logprobView.enabled && !!s.token_logprobs?.length;
  // Lazy blob fetches — fire only when a view NEEDS the payload: the token
  // inspector when the toggle is on, the request/response disclosure when the
  // raw view opens. ensure() dedupes (cached/in-flight ids are skipped).
  $effect(() => {
    if (logprobView.enabled && hasTok && !tlp && msg.nodeId) void nodeBlobs.ensure([msg.nodeId]);
  });
  $effect(() => {
    if (rawSingle && hasMeta && rawMeta == null && msg.nodeId) void nodeBlobs.ensure([msg.nodeId]);
  });
  let canEdit = $derived(msg.nodeId != null && !busy);
  // ‹k/N› on any committed row with siblings (the n>1 bucket uses its cards).
  let hasSiblings = $derived(!!(msg.sib && msg.sib.count > 1) && !isMultiSample);
  // ctrl/cmd "apply to every panel" only means something with >1 panel on screen.
  let allActive = $derived(ctrlDown && showRegenAll);
  // Shift+Continue resumes inside the think block — only meaningful with reasoning.
  let canResumeThinking = $derived(msg.role === 'assistant' && !!msg.reasoning?.trim());

  function roleColor(role: string): string {
    if (role === 'user') return 'var(--color-user-bg)';
    if (role === 'assistant') return 'var(--color-assistant-bg)';
    return 'var(--color-system-bg)';
  }

  // Raw-view toggles (local). One for the single message, a set for sample cards.
  let rawSingle = $state(false);
  let rawSamples = $state<Set<number>>(new Set());
  function toggleRawSample(idx: number) {
    const next = new Set(rawSamples);
    if (next.has(idx)) next.delete(idx);
    else next.add(idx);
    rawSamples = next;
  }

  // Cycle-view cursor (local). In 'cycle' mode only ONE sample card shows; this
  // indexes into the *displayable* samples (content present, matching the {#each}
  // skip rule), NOT into msg.samples directly — so prev/next never lands on an
  // empty/error slot. The card itself still uses its ORIGINAL index for actions.
  let sampleCursor = $state(0);
  // Reasoning fold state for cycle-view, persisted at the message level so it
  // survives cycling THROUGH a no-reasoning sample (whose <details> unmounts) —
  // otherwise returning to a reasoning sample would re-fold. In 'all' view each
  // card's <details> stays independent (uncontrolled).
  let reasoningOpen = $state(false);
  let visibleSampleIdxs = $derived(
    (msg.samples ?? []).map((s, i) => [s, i] as const).filter(([s]) => s && s.content).map(([, i]) => i)
  );
  // Clamp the cursor against the live count (samples stream in / get deleted).
  let safeCursor = $derived(Math.min(Math.max(sampleCursor, 0), Math.max(0, visibleSampleIdxs.length - 1)));
  // The ‹k/N› sample nav lives in the message header (top-right) in cycle view.
  let showSampleCycler = $derived(isMultiSample && sampleView === 'cycle' && visibleSampleIdxs.length > 0);

  // Transient "✓ copied" flash on the copy menu items (clipboard gives no
  // feedback). Independent flags so the message / conversation / node-id items
  // flash individually (clicking one must not light up the others).
  let copiedMsg = $state(false);
  let copiedConv = $state(false);
  let copiedId = $state(false);
  let copiedTimer: ReturnType<typeof setTimeout> | undefined;
  function flashCopied(which: 'msg' | 'conv' | 'id') {
    if (which === 'msg') copiedMsg = true;
    else if (which === 'conv') copiedConv = true;
    else copiedId = true;
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => { copiedMsg = false; copiedConv = false; copiedId = false; }, 1200);
  }

  // Inline edit (local). `editShift` remembers a shift-open (fork-full-copy, no gen);
  // `editAll` remembers a ctrl/cmd-open (apply the edit across every panel).
  let editing = $state(false);
  let editDraft = $state('');
  // Assistant CoT editor: shown iff the turn had reasoning at edit-open. Clearing
  // it drops the CoT; on a non-thinking turn there's no box (stays single-field).
  let editReasoning = $state('');
  let editHasReasoning = $state(false);
  let editShift = $state(false);
  let editAll = $state(false);
  // Thread-system field (ROOT user rows only): editing it — like editing the
  // content — forks a sibling THREAD; "same question, new prompt" is just an edit.
  let editSystem = $state('');
  let editHasSystem = $derived(msg.role === 'user' && !!msg.isRoot);
  // The in-row thread-system strip's expand/collapse (collapsed one-liner by default).
  let sysExpanded = $state(false);
  function startEdit(shift: boolean, all: boolean) {
    if (!canEdit) return;
    editing = true;
    editShift = shift;
    editAll = all;
    editDraft = msg.content;
    editHasReasoning = msg.role === 'assistant' && !!msg.reasoning;
    editReasoning = msg.reasoning ?? '';
    editSystem = msg.system_prompt ?? '';
  }
  function commitEdit() {
    onEdit(
      editDraft,
      editHasReasoning ? editReasoning : undefined,
      editShift,
      editAll,
      editHasSystem ? editSystem : undefined
    );
    editing = false;
  }
  function cancelEdit() {
    editing = false;
  }

  // The chat column's {#each} is keyed by nodeId, but a same-position reshape
  // (cycling to an identical-content sibling, a fork, a CLI/other-tab turn) can
  // still hand THIS mounted instance a *different* node. Drop any in-progress
  // edit / raw-view state when the node identity OR content changes, so an open
  // editor can never Save its stale draft onto the wrong node (the critique's
  // identical-content-sibling case is why nodeId is tracked, not just content).
  //
  // Guard on the VALUE, not the object reference: panelView rebuilds fresh
  // ViewMessage objects for EVERY panel on ANY live.state change (e.g. another panel
  // streaming a completion), so this effect re-fires constantly with a value-identical
  // `msg`. Without the early-out it would cancel an in-progress edit in panel A every
  // time panel B produced a token — the "completion in B resets my edit in A" bug. The
  // trackers are plain (non-$state) lets so reading them doesn't add reactive deps.
  let seenNodeId: string | null | undefined;
  let seenRole: string | undefined;
  let seenContent: string | undefined;
  $effect(() => {
    const nid = msg.nodeId;
    const role = msg.role;
    const content = msg.content;
    if (nid === seenNodeId && role === seenRole && content === seenContent) return;
    seenNodeId = nid;
    seenRole = role;
    seenContent = content;
    editing = false;
    editDraft = '';
    editReasoning = '';
    editHasReasoning = false;
    editShift = false;
    editAll = false;
    editSystem = '';
    sysExpanded = false;
    copiedMsg = false;
    copiedConv = false;
    copiedId = false;
    rawSingle = false;
    rawSamples = new Set();
    sampleCursor = 0;
    reasoningOpen = false;
  });
</script>

{#snippet cycler()}
  {#if hasSiblings && msg.sib}
    <div class="branch-cycle" data-testid="branch-cycle">
      <button class="branch-cycle-btn" aria-label="Previous branch" disabled={busy} onclick={() => onCycle(-1)}>
        <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M10 3l-5 5 5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
      <span class="branch-cycle-count">{msg.sib.index + 1}/{msg.sib.count}</span>
      <button class="branch-cycle-btn" aria-label="Next branch" disabled={busy} onclick={() => onCycle(1)}>
        <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
    </div>
  {/if}
{/snippet}

<!-- ‹k/N› sample nav for cycle-view. Wraps 1-2-3-1…; lives in the message header
     (top-right, level with the role label) so it stays put as cards resize. -->
{#snippet sampleCycler()}
  {@const N = visibleSampleIdxs.length}
  <div class="branch-cycle" data-testid="sample-cycle">
    <button class="branch-cycle-btn" aria-label="Previous sample" onclick={() => (sampleCursor = (safeCursor - 1 + N) % N)}>
      <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M10 3l-5 5 5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
    </button>
    <span class="branch-cycle-count">{safeCursor + 1}/{N}</span>
    <button class="branch-cycle-btn" aria-label="Next sample" onclick={() => (sampleCursor = (safeCursor + 1) % N)}>
      <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
    </button>
  </div>
{/snippet}

<!-- One n>1 distribution card. Rendered once per displayable sample in 'all' mode,
     or once (the cursor's sample) in 'cycle' mode. `idx` is the ORIGINAL index into
     msg.samples so every action (select/discard/delete/raw/tag) targets the right
     sibling regardless of view mode. -->
{#snippet rawMetaDisclosure(meta: string)}
  <details class="sample-reasoning-block raw-meta">
    <summary class="sample-reasoning-toggle">
      <span>Request &amp; response</span>
      <svg class="thinking-chevron" width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
    </summary>
    <pre class="raw-text-view">{meta}</pre>
  </details>
{/snippet}

{#snippet sampleCard(sample: SampleData, idx: number)}
  <div class="sample-card" class:active-sample={msg.activeSampleIndex === idx}>
    <div class="sample-header">
      <span>Sample {idx + 1}</span>
      {#if msg.activeSampleIndex === idx}<span class="active-sample-tag">active branch</span>{/if}
      {#if sample.thinking !== undefined}{@render modeTag(sample.thinking)}{/if}
      {#if sample.finish_reason === 'length'}{@render truncatedTag()}{/if}
    </div>
    {#if sample.reasoning && !sampleTok(sample)}
      <details
        class="sample-reasoning-block"
        open={sampleView === 'cycle' ? reasoningOpen : undefined}
        ontoggle={(e) => (reasoningOpen = (e.currentTarget as HTMLDetailsElement).open)}
      >
        <summary class="sample-reasoning-toggle">
          <span>Reasoning</span>
          <svg class="thinking-chevron" width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
        </summary>
        <div class="sample-reasoning">{@html prefillSplit ? renderPrefilled(sample.reasoning, prefillSplit.think, 'assistant') : renderContent(sample.reasoning, 'assistant')}</div>
      </details>
    {/if}
    {#if rawSamples.has(idx) && sample.raw_text}
      <pre class="raw-text-view">{sample.raw_text}</pre>
      {#if sample.raw_meta}{@render rawMetaDisclosure(sample.raw_meta)}{/if}
    {:else if sampleTok(sample)}
      <TokenLogprobs tlp={sample.token_logprobs!} />
    {:else}
      <div class="sample-content">{@html prefillSplit ? renderPrefilled(sample.content, prefillSplit.answer, 'assistant') : renderContent(sample.content, 'assistant')}</div>
    {/if}
    <OverflowRow klass="sample-actions" resetKey={msg.sampleNodeIds?.[idx] ?? String(idx)}>
      {#if sample.raw_text}
        <!-- Raw leads the row (very left, never folds), like the single-row toolbar. -->
        <button class="btn-raw" class:active={rawSamples.has(idx)} onclick={() => toggleRawSample(idx)} title="Toggle raw model output with tags preserved">Raw</button>
      {/if}
      <button class="btn-use" class:active={msg.activeSampleIndex === idx} data-tooltip="Make this the active branch & collapse to it (others stay as ‹k/N› siblings)" use:tip aria-label="Make active" disabled={busy || !msg.sampleNodeIds?.[idx]} onclick={() => onSelectSample(idx)}>
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.4" /><path d="M5.2 8.3l1.9 1.9 3.7-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
      <button class="btn-act sample-continue" data-tooltip="Continue THIS sample — makes it the active branch, then the model extends it (n-samples → new branches to pick)" use:tip aria-label="Continue this sample" disabled={busy || !msg.sampleNodeIds?.[idx]} onclick={() => onContinueSample(idx)}>
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M8 3.5v9M3.5 8h9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" /></svg>
      </button>
      <button class="btn-tag" class:shift-alt={shiftDown} data-tooltip={shiftDown ? 'Bookmark instantly (no note)' : 'Bookmark with a note'} use:tip onclick={(e) => onTag(sample.content, idx, msg.totalSamples ?? null, sample.reasoning || '', e.shiftKey)}>
        {#if shiftDown}{@render tagQuickIcon()}{:else}{@render tagIcon()}{/if}
      </button>
      <button class="btn-act btn-act-danger sample-del" data-tooltip="Delete this sample" use:tip aria-label="Delete this sample" disabled={busy || !msg.sampleNodeIds?.[idx]} onclick={() => onDeleteSample(idx)}>
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V2.5h4V4M4.5 4l.6 9h5.8l.6-9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
      <button class="btn-act btn-act-danger" data-tooltip="Keep only this sample — discard the others" use:tip aria-label="Discard others" disabled={busy || !msg.sampleNodeIds?.[idx]} onclick={() => onDiscardOthers(idx)}>
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><rect x="2.5" y="5" width="7" height="8.5" rx="1" stroke="currentColor" stroke-width="1.3" /><path d="M7 2.5h6.5V11" stroke="currentColor" stroke-width="1.2" opacity="0.5" /><path d="M10.8 4.8l2.4 2.4M13.2 4.8l-2.4 2.4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" /></svg>
      </button>
      {@render copyIdBtn(msg.sampleNodeIds?.[idx])}
    </OverflowRow>
  </div>
{/snippet}

<!-- Action icons. The shift-held variants signal the alternate action: regenerate
     becomes "replace in place" (square in the center), edit becomes "fork a full
     copy" (overlapping pages). -->
{#snippet regenIcon()}
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none"><path d="M1.5 7a5.5 5.5 0 0 1 9.9-3.3M12.5 7a5.5 5.5 0 0 1-9.9 3.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /><path d="M11.5 1v3h-3M2.5 13v-3h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
{/snippet}
{#snippet replaceIcon()}
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none"><path d="M1.5 7a5.5 5.5 0 0 1 9.9-3.3M12.5 7a5.5 5.5 0 0 1-9.9 3.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /><path d="M11.5 1v3h-3M2.5 13v-3h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /><rect x="5.1" y="5.1" width="3.8" height="3.8" rx="0.6" fill="currentColor" /></svg>
{/snippet}
{#snippet editIcon()}
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M10.5 2.5l3 3L6 13l-3.5.5L3 10l7.5-7.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /></svg>
{/snippet}
{#snippet editCopyIcon()}
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><rect x="2.5" y="2.5" width="7.5" height="9.5" rx="1" stroke="currentColor" stroke-width="1.2" /><path d="M6 4.5h6a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" /></svg>
{/snippet}
{#snippet tagIcon()}
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M2 2h6l6 6-6 6-6-6V2Z" stroke="currentColor" stroke-width="1.5" /><circle cx="5.5" cy="5.5" r="1" fill="currentColor" /></svg>
{/snippet}
{#snippet tagQuickIcon()}
  <!-- filled bookmark = save instantly, no note prompt -->
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M2 2h6l6 6-6 6-6-6V2Z" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" /><circle cx="5.3" cy="5.3" r="1" fill="var(--color-bg)" /></svg>
{/snippet}
{#snippet trashIcon()}
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V2.5h4V4M4.5 4l.6 9h5.8l.6-9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
{/snippet}
{#snippet continuePlusIcon()}
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M8 3.5v9M3.5 8h9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" /></svg>
{/snippet}
{#snippet continueThinkIcon()}
  <!-- thought bubble + plus = continue the reasoning (resume inside the think block) -->
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="12" height="8.4" rx="4.2" stroke="currentColor" stroke-width="1.3" /><path d="M8 4.4v3.6M6.2 6.2h3.6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /><circle cx="5" cy="12.5" r="1.15" fill="currentColor" /><circle cx="2.8" cy="14.4" r="0.75" fill="currentColor" /></svg>
{/snippet}
{#snippet trashAllIcon()}
  <!-- trash + a back layer = "delete every branch at this level" -->
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M5.5 5.5l.5 7.5h5.5l.5-7.5M5 5.5h8M7.5 5.5V4h3v1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 3.2h6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" /><path d="M2.6 3.2l.5 6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" /></svg>
{/snippet}

<!-- think / no-think chip: which renderer mode drew this sample. Only rendered
     when the field is set, i.e. on thinking=BOTH batches (single-mode turns
     don't carry it — the whole batch shares one mode). -->
{#snippet modeTag(think: boolean)}
  <span
    class="mode-tag"
    class:mode-think={think}
    data-tooltip={think ? 'Drawn with the thinking renderer' : 'Drawn with the non-thinking renderer'}
    use:tip>{think ? 'think' : 'no think'}</span>
{/snippet}

<!-- "no token data" pill: the Token-probs toggle is on but this turn carries no
     logprobs (OpenRouter / token-streamed paths / turns predating capture). -->
{#snippet noTokTag()}
  <span
    class="mode-tag"
    data-tooltip="No token logprobs on this turn — they're captured for native tinker sampling only (not OpenRouter or token-streamed single samples)"
    use:tip>no token data</span>
{/snippet}

<!-- "truncated" badge: the sample/turn hit the max-tokens limit ('length' finish
     reason) and was cut off mid-generation. Continue (+) picks up where it stopped. -->
{#snippet truncatedTag()}
  <span
    class="truncated-tag"
    data-tooltip="Hit the max-tokens limit — the output is cut off. Continue (+) extends it."
    use:tip>truncated</span>
{/snippet}

<!-- Continue (prefill) an assistant turn: extend it; n-samples → branches to pick.
     ctrl/cmd (compare) = continue the same-depth turn in every panel.
     shift = resume INSIDE the think block (before </think>) — extend the reasoning. -->
{#snippet continueBtn()}
  <button
    class="btn-act"
    class:btn-act-all={allActive}
    class:shift-alt={shiftDown && canResumeThinking}
    data-tooltip={(shiftDown && canResumeThinking
      ? 'Continue the REASONING — resume inside the think block (before </think>)'
      : 'Continue this message (extends it; n-samples → pick one)') + (allActive ? ' — in ALL panels' : '')}
    use:tip
    aria-label="Continue this message"
    onclick={(e) => onContinue(e.ctrlKey || e.metaKey, e.shiftKey)}
  >
    {#if shiftDown && canResumeThinking}{@render continueThinkIcon()}{:else}{@render continuePlusIcon()}{/if}
  </button>
{/snippet}

{#snippet copyIcon()}
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><rect x="5" y="5" width="8.5" height="8.5" rx="1.2" stroke="currentColor" stroke-width="1.2" /><path d="M3 10.5A1.5 1.5 0 0 1 2.5 9.5V3.5A1.5 1.5 0 0 1 4 2h6a1.5 1.5 0 0 1 1.1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" /></svg>
{/snippet}
{#snippet copyAllIcon()}
  <!-- copy + text lines = "copy the FULL conversation as markdown" -->
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><rect x="5" y="4.5" width="8.5" height="9" rx="1.2" stroke="currentColor" stroke-width="1.2" /><path d="M7 7.3h4.5M7 9.3h4.5M7 11.3h2.6" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" /><path d="M3 10.5A1.5 1.5 0 0 1 2.5 9.5V3.5A1.5 1.5 0 0 1 4 2h6a1.5 1.5 0 0 1 1.1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" /></svg>
{/snippet}
{#snippet checkIcon()}
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3.5 3.5L13 5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>
{/snippet}
{#snippet hashIcon()}
  <!-- node-id glyph (a # = "this row's id") -->
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M6.2 2.5L4.8 13.5M11.2 2.5L9.8 13.5M2.8 6h10.5M2.5 10h10.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /></svg>
{/snippet}

<!-- Copy THIS message's content. shift = also include the thinking as <think>…</think>. -->
{#snippet copyMsgBtn()}
  <button
    class="btn-act"
    class:shift-alt={shiftDown}
    class:copied={copiedMsg}
    data-tooltip={copiedMsg ? 'Copied!' : shiftDown ? 'Copy this message + thinking' : 'Copy this message'}
    use:tip
    aria-label="Copy this message"
    onclick={(e) => { onCopy(false, e.shiftKey); flashCopied('msg'); }}
  >
    {#if copiedMsg}{@render checkIcon()}{:else}{@render copyIcon()}{/if}
  </button>
{/snippet}

<!-- Copy the WHOLE conversation as markdown. shift = include thinking as <think>…</think>. -->
{#snippet copyConvBtn()}
  <button
    class="btn-act"
    class:shift-alt={shiftDown}
    class:copied={copiedConv}
    data-tooltip={copiedConv ? 'Copied!' : shiftDown ? 'Copy the full conversation + thinking' : 'Copy the full conversation'}
    use:tip
    aria-label="Copy conversation"
    onclick={(e) => { onCopy(true, e.shiftKey); flashCopied('conv'); }}
  >
    {#if copiedConv}{@render checkIcon()}{:else}{@render copyAllIcon()}{/if}
  </button>
{/snippet}

<!-- Send this branch's context (root→here) into another panel's thread: an
     ActionMenu popover listing the other panels. -->
{#snippet sendToPicker()}
  {#if otherPanels.length && onSendToPanel && msg.nodeId != null}
    <ActionMenu label="Send this branch's context to another panel" testid="send-to" resetKey={msg.nodeId ?? ''}>
      {#snippet trigger()}
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M2 6h10M9.5 3.5 12 6 9.5 8.5M14 10H4M6.5 7.5 4 10l2.5 2.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
      {/snippet}
      {#snippet children(close)}
        {#each otherPanels as op (op.id)}
          <button
            class="row-menu-item"
            role="menuitem"
            onclick={() => { close(); onSendToPanel?.(op.id); }}
          >→ {op.label}</button>
        {/each}
      {/snippet}
    </ActionMenu>
  {/if}
{/snippet}

<!-- Copy this node's id — the CLI's addressing currency (`tinkpg samples/continue
     --node <id>`); the tooltip shows the id itself + both consumers. -->
{#snippet copyIdBtn(id: string | null | undefined)}
  {#if id}
    <button
      class="btn-act"
      class:copied={copiedId}
      data-testid="copy-node-id"
      data-tooltip={copiedId ? 'Copied!' : `Copy node id ${id} — the CLI handle: tinkpg samples --node ${id} (this fork's fan-out) · tinkpg continue --node ${id} (loom from here)`}
      use:tip
      aria-label="Copy node id"
      onclick={() => { navigator.clipboard?.writeText(id); flashCopied('id'); }}
    >
      {#if copiedId}{@render checkIcon()}{:else}{@render hashIcon()}{/if}
    </button>
  {/if}
{/snippet}

<!-- Delete: shift → delete ALL sibling branches; ctrl/cmd → in every panel. -->
{#snippet deleteBtn(label: string)}
  <button
    class="btn-act btn-act-danger"
    class:shift-alt-danger={shiftDown}
    class:btn-act-all={allActive}
    data-tooltip={`${shiftDown ? 'Delete ALL branches at this turn' : label}${allActive ? ' — in ALL panels' : ''}`}
    use:tip
    aria-label={shiftDown ? 'Delete all branches' : 'Delete'}
    onclick={(e) => onDelete(e.ctrlKey || e.metaKey, e.shiftKey)}
  >
    {#if shiftDown}{@render trashAllIcon()}{:else}{@render trashIcon()}{/if}
  </button>
{/snippet}

<!-- Regenerate: shift → replace this branch in place; ctrl/cmd → in every panel. -->
{#snippet regenGroup()}
  <button
    class="btn-act"
    class:shift-alt={shiftDown}
    class:btn-act-all={allActive}
    data-tooltip={`${shiftDown ? 'Replace this branch in place (other siblings kept)' : 'Regenerate → new sibling branch'}${allActive ? ' — in ALL panels' : ''}`}
    use:tip
    aria-label="Regenerate"
    onclick={(e) => onRegenerate(e.ctrlKey || e.metaKey, e.shiftKey)}
  >
    {#if shiftDown}{@render replaceIcon()}{:else}{@render regenIcon()}{/if}
  </button>
{/snippet}

{#if msg.notice}
  <!-- Status strip (e.g. 'stopped' after a deliberate 0-sample cancel) — not a
       message: no role header, no toolbar, muted. -->
  <div class="message-notice" data-row={rowIndex}>⏹ {msg.notice}</div>
{:else if msg.role !== 'assistant' || msg.content || msg.reasoning || isMultiSample || (msg.samples && msg.samples.some((x) => x && x.content))}
  <!-- Row click (anywhere, buttons included) moves the keyboard focus here.
       svelte-ignore: this is a mouse-only convenience — the keyboard already
       has its own path to focus (↑/↓), so no key handler belongs on the div. -->
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div
    class="message"
    class:kb-focused={focused}
    data-row={rowIndex}
    style="background: {roleColor(msg.role)};"
    onclick={onFocusRow}
  >
    <div class="message-head">
      <div class="message-head-left">
        <div class="message-role">{msg.role}</div>
        {#if msg.thinking !== undefined && !isMultiSample}{@render modeTag(msg.thinking)}{/if}
        {#if msg.finish_reason === 'length' && !isMultiSample}{@render truncatedTag()}{/if}
        {#if logprobView.enabled && msg.role === 'assistant' && (msg.content || msg.reasoning || isMultiSample) && !hasTok && !(msg.samples ?? []).some((s) => s?.token_logprobs?.length)}{@render noTokTag()}{/if}
      </div>
      {#if showSampleCycler}{@render sampleCycler()}{:else}{@render cycler()}{/if}
    </div>
    {#if msg.role === 'user' && msg.system_prompt && !editing}
      <!-- Thread-system strip (root rows): the prompt this THREAD runs under,
           composed over the workspace's global one. Part of the node, so the
           ‹k/N› cycler swaps it together with the content. Click to expand. -->
      <button
        class="sys-strip"
        class:expanded={sysExpanded}
        data-testid="thread-system-strip"
        data-tooltip={sysExpanded ? 'Collapse' : 'This thread\'s system prompt (composed over the global one) — click to expand'}
        use:tip
        onclick={(e) => { e.stopPropagation(); sysExpanded = !sysExpanded; onFocusRow?.(); }}
      >
        <span class="sys-strip-tag">system</span>
        <span class="sys-strip-text">{sysExpanded ? msg.system_prompt : msg.system_prompt.replace(/\s+/g, ' ')}</span>
      </button>
    {/if}
    {#if msg.role === 'assistant' && msg.reasoning && !isMultiSample && !tokView}
      <details class="sample-reasoning-block reasoning-primary" open={isLastAssistant}>
        <summary class="sample-reasoning-toggle">
          <span>Thinking</span>
          <svg class="thinking-chevron" width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
        </summary>
        <div class="sample-reasoning">{@html prefillSplit ? renderPrefilled(msg.reasoning, prefillSplit.think, 'assistant') : renderContent(msg.reasoning, 'assistant')}</div>
      </details>
    {/if}
    {#if isMultiSample}
      {@const completedCount = msg.samples ? msg.samples.filter((x) => x && x.content).length : 0}
      {@const allDone = !msg.running && completedCount > 0}
      {#if !allDone}
        <div class="samples-progress">
          <div class="samples-progress-bar">
            <div class="samples-progress-fill" style="width: {(completedCount / (msg.totalSamples ?? 1)) * 100}%"></div>
          </div>
          <div class="samples-progress-text">
            {#if completedCount === 0 && thinking}
              Generating {msg.totalSamples} samples (thinking...)
            {:else}
              {completedCount} / {msg.totalSamples} samples completed
            {/if}
          </div>
        </div>
      {/if}
      {#if completedCount > 0}
        {#if sampleView === 'cycle' && visibleSampleIdxs.length > 0}
          {@const curIdx = visibleSampleIdxs[safeCursor]}
          <!-- One card; the ‹k/N› nav lives in the message header (top-right). -->
          <div class="samples-container">
            {@render sampleCard(msg.samples![curIdx], curIdx)}
          </div>
        {:else}
          <div class="samples-container">
            {#each msg.samples ?? [] as sample, idx (idx)}
              {#if sample && sample.content}
                {@render sampleCard(sample, idx)}
              {/if}
            {/each}
          </div>
        {/if}
      {/if}
      {#if allDone && msg.nodeId != null && !busy}
        <OverflowRow klass="turn-actions hover-actions" resetKey={msg.nodeId ?? ''}>
          {@render regenGroup()}
          {@render continueBtn()}
          {@render deleteBtn('Delete this turn')}
          {@render copyMsgBtn()}
          {@render copyConvBtn()}
          {@render sendToPicker()}
          {@render copyIdBtn(msg.nodeId)}
        </OverflowRow>
      {/if}
    {:else if editing && msg.nodeId != null}
      {#if editShift}<div class="edit-hint">Shift-edit: forks a full editable copy of the conversation from here — nothing is generated.</div>{/if}
      {#if editHasSystem}
        <!-- Thread-system editor (root rows): saving with a changed system forks a
             sibling THREAD, exactly like a content edit. Empty = global only. -->
        <div class="edit-field-label">Thread system prompt <span class="edit-field-hint">(optional — appended to the global one; changing it forks a sibling thread)</span></div>
        <textarea
          class="edit-textarea edit-system"
          data-testid="edit-thread-system"
          bind:value={editSystem}
          rows="2"
          placeholder="none — global system prompt only"
          onkeydown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); commitEdit(); }
            else if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
          }}
        ></textarea>
        <div class="edit-field-label">Message</div>
      {/if}
      {#if editHasReasoning}
        <!-- Assistant CoT editor: edits land in this turn's reasoning block.
             Clear it to drop the CoT entirely. -->
        <div class="edit-field-label">Thinking</div>
        <textarea
          class="edit-textarea edit-reasoning"
          bind:value={editReasoning}
          rows="4"
          onkeydown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); commitEdit(); }
            else if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
          }}
        ></textarea>
        <div class="edit-field-label">Response</div>
      {/if}
      <!-- svelte-ignore a11y_autofocus -->
      <textarea
        class="edit-textarea"
        bind:value={editDraft}
        rows="4"
        autofocus
        onkeydown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); commitEdit(); }
          else if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
        }}
      ></textarea>
      <div class="edit-actions">
        <button class="btn-edit-cancel" onclick={cancelEdit}>Cancel</button>
        <button class="btn-edit-save" onclick={commitEdit}>Save{editShift ? ' fork' : ''}</button>
      </div>
    {:else}
      {#if rawSingle && msg.raw_text}
        <pre class="raw-text-view">{msg.raw_text}</pre>
          {#if rawMeta}{@render rawMetaDisclosure(rawMeta)}{:else if hasMeta}<div class="blob-loading">loading request &amp; response…</div>{/if}
      {:else if tokView}
        <TokenLogprobs tlp={tlp!} />
      {:else}
        <div class="message-content">{@html prefillSplit ? renderPrefilled(msg.content, prefillSplit.answer, msg.role) : renderContent(msg.content, msg.role)}</div>
      {/if}
      {#if msg.role !== 'system' && (msg.content || msg.raw_text || msg.reasoning)}
        <OverflowRow klass="hover-actions" resetKey={msg.nodeId ?? msg.content}>
          {#if msg.raw_text}
            <!-- Raw leads the row (Clément: very left, always visible — first
                 in priority order it can never fold). -->
            <button class="btn-raw" class:active={rawSingle} onclick={() => (rawSingle = !rawSingle)} title="Toggle raw model output with tags preserved">Raw</button>
          {/if}
          {#if canEdit}
            {@render regenGroup()}
            {#if msg.role === 'assistant'}{@render continueBtn()}{/if}
            <button
              class="btn-act"
              class:shift-alt={shiftDown && msg.role === 'user'}
              class:btn-act-all={allActive}
              data-tooltip={`${msg.role === 'user'
                ? (shiftDown ? 'Edit → fork a FULL editable copy of the conversation from here (no generation)' : 'Edit → fork + regenerate (shift: fork full copy)')
                : 'Edit → new branch'}${allActive ? ' — in ALL panels' : ''}`}
              use:tip
              aria-label="Edit"
              onclick={(e) => startEdit(e.shiftKey, e.ctrlKey || e.metaKey)}
            >
              {#if shiftDown && msg.role === 'user'}{@render editCopyIcon()}{:else}{@render editIcon()}{/if}
            </button>
            {@render deleteBtn('Delete this branch')}
          {/if}
          {#if msg.role === 'assistant' && msg.content}
            <button class="btn-tag" class:shift-alt={shiftDown} data-tooltip={shiftDown ? 'Bookmark instantly (no note)' : 'Bookmark with a note'} use:tip onclick={(e) => onTag(msg.content, null, null, msg.reasoning || '', e.shiftKey)}>
              {#if shiftDown}{@render tagQuickIcon()}{:else}{@render tagIcon()}{/if}
            </button>
          {/if}
          {@render copyMsgBtn()}
          {@render copyConvBtn()}
          {@render sendToPicker()}
          {@render copyIdBtn(msg.nodeId)}
        </OverflowRow>
      {/if}
    {/if}
  </div>
{/if}

<style>
  /* transient strip while a light node's raw_meta blob is being fetched */
  .blob-loading {
    font-size: 0.72rem;
    opacity: 0.55;
    font-style: italic;
    padding: 2px 0;
  }

  /* Neutral status strip (msg.notice) — a deliberate stop, not an error. */
  .message-notice { padding: var(--space-2) var(--space-4); font-size: 0.75rem; font-style: italic; color: var(--color-text-muted); }
  /* Message header: role label on the left, the ‹k/N› cycler pinned top-right
     (level with the role) so it stays put as the message/card resizes. */
  .message-head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); margin-bottom: var(--space-2); }
  .message-head .message-role { margin-bottom: 0; }
  .message-head-left { display: flex; align-items: center; gap: var(--space-2); }
  /* Amber "truncated" pill — the turn/sample hit the max-tokens limit. */
  .truncated-tag { font-size: 0.62rem; color: #b45309; background: #f59e0b14; border: 1px solid #f59e0b66; border-radius: var(--radius-pill); padding: 0 6px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; cursor: default; }
  /* think / no-think chip (thinking=BOTH batches): grey = no-think half, accent = think half. */
  .mode-tag { font-size: 0.62rem; color: var(--color-text-muted); background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius-pill); padding: 0 6px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; cursor: default; white-space: nowrap; }
  .mode-tag.mode-think { color: var(--color-accent); border-color: var(--color-accent); background: var(--color-accent-bg); }
  /* ── ‹k/N› cycler pill (branch siblings + sample cycle-view) ────── */
  .branch-cycle { display: flex; align-items: center; gap: 2px; padding: 1px 4px; border: 1px solid var(--color-border); border-radius: var(--radius-pill); background: var(--color-bg); width: fit-content; user-select: none; flex-shrink: 0; }
  .branch-cycle-btn { display: flex; align-items: center; justify-content: center; padding: 2px; background: none; border: none; color: var(--color-text-muted); cursor: pointer; border-radius: var(--radius-sm); }
  .branch-cycle-btn:hover:not(:disabled) { color: var(--color-accent); background: var(--color-accent-bg); }
  .branch-cycle-btn:disabled { opacity: 0.3; cursor: default; }
  .branch-cycle-count { font-size: 0.68rem; font-variant-numeric: tabular-nums; color: var(--color-text-secondary); min-width: 24px; text-align: center; }
  .active-sample { outline: 2px solid var(--color-accent); outline-offset: 1px; }
  /* Workspace keyboard focus: the ONE focused row (click to focus; ↑/↓ move,
     ←/→ cycle its branches, Esc clears). Softer than .active-sample's ring so
     it reads as "cursor here", not "selected". */
  .message.kb-focused { outline: 2px solid color-mix(in srgb, var(--color-accent) 55%, transparent); outline-offset: 1px; }
  .active-sample-tag { font-size: 0.62rem; color: var(--color-accent); font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; margin-left: var(--space-2); }
  .btn-use.active { background: var(--color-accent); border-color: var(--color-accent); color: white; }
  .edit-hint { font-size: 0.7rem; color: var(--color-text-muted); font-style: italic; margin-bottom: var(--space-1); line-height: 1.3; }
  /* The authored-prefill prefix of an assistant turn, colored distinctly from the
     model's continuation. :global because the span is injected via {@html}. Render as
     inline so it flows with the continuation; a soft tint + left rule mark it. */
  .message-content :global(.prefill-portion),
  .sample-content :global(.prefill-portion),
  .sample-reasoning :global(.prefill-portion) { color: var(--color-text-muted); background: var(--color-accent-bg); border-radius: var(--radius-sm); box-decoration-break: clone; -webkit-box-decoration-break: clone; }
  .message-content :global(.prefill-portion *),
  .sample-content :global(.prefill-portion *),
  .sample-reasoning :global(.prefill-portion *) { color: inherit; }
</style>
