<script lang="ts">
	// One chat row: a committed tree node OR the live "bucket" turn (latest turn's
	// N variants / streaming progress). Owns its OWN raw-view + edit-mode UI state.
	// Tree mutations are delegated to the parent via callbacks (it owns the tree).
	// Branching adds: a ‹k/N› sibling cycler, regenerate on user turns, edit that
	// FORKS (shift+click = fork-and-copy-downstream), and n>1 cards that select a
	// sibling branch instead of "use this".
	import { renderContent, renderPrefilled, splitPrefill } from '$lib/render';
	import { tip } from '$lib/tooltip.svelte';
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
		onDeleteSample,
		onEdit,
		onCopy,
		onTag,
		onCycle,
		otherPanels = [],
		onSendToPanel
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
		thinking: boolean;
		// How the n>1 distribution bucket renders its cards: 'all' = every card stacked
		// (scrollable), 'cycle' = one card at a time with ‹k/N› prev/next. UI-only pref.
		sampleView?: 'all' | 'cycle';
		onRegenerate: (allPanels: boolean, replace: boolean) => void;
		onContinue: (allPanels: boolean) => void;
		onDelete: (allPanels: boolean, allSiblings: boolean) => void;
		onSelectSample: (sampleIndex: number) => void;
		onDiscardOthers: (sampleIndex: number) => void;
		onDeleteSample: (sampleIndex: number) => void;
		onEdit: (content: string, copyDownstream: boolean, allPanels: boolean) => void;
		onCopy: (all: boolean) => void;
		onTag: (content: string, sampleIndex: number | null, totalSamples: number | null, reasoning: string, quick: boolean) => void;
		onCycle: (delta: number) => void;
		// Other panels this branch can be copied into (compare). Empty → no picker.
		otherPanels?: { id: string; label: string }[];
		onSendToPanel?: (destPanel: string) => void;
	} = $props();

	// The authored prefill (raw) split into its reasoning/answer parts, so the renderer
	// can color the prefilled prefix of `reasoning` (think part) and `content` (answer
	// part) distinctly from the model's continuation. All samples in a turn share it.
	let prefillSplit = $derived(msg.prefill ? splitPrefill(msg.prefill) : null);

	let isMultiSample = $derived(!!(msg.totalSamples && msg.totalSamples > 1));
	let canEdit = $derived(msg.nodeId != null && !busy);
	// ‹k/N› on any committed row with siblings (the n>1 bucket uses its cards).
	let hasSiblings = $derived(!!(msg.sib && msg.sib.count > 1) && !isMultiSample);
	// ctrl/cmd "apply to every panel" only means something with >1 panel on screen.
	let allActive = $derived(ctrlDown && showRegenAll);

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

	// Transient "✓ copied" flash on the copy button (clipboard gives no feedback).
	let copied = $state(false);
	let copiedTimer: ReturnType<typeof setTimeout> | undefined;
	function flashCopied() {
		copied = true;
		clearTimeout(copiedTimer);
		copiedTimer = setTimeout(() => (copied = false), 1200);
	}

	// Transfer-to-panel: a custom dropdown (small icon button + a separately-sized
	// menu) instead of a native <select>, whose control would auto-grow to the widest
	// panel-label option. Closes on outside-click / Escape.
	let sendMenuOpen = $state(false);
	let sendWrap = $state<HTMLElement | null>(null);
	$effect(() => {
		if (!sendMenuOpen) return;
		const onDoc = (e: MouseEvent) => {
			if (sendWrap && !sendWrap.contains(e.target as Node)) sendMenuOpen = false;
		};
		const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') sendMenuOpen = false; };
		window.addEventListener('keydown', onKey);
		// defer the doc-click bind so the opening click doesn't immediately close it
		const t = setTimeout(() => window.addEventListener('click', onDoc), 0);
		return () => {
			clearTimeout(t);
			window.removeEventListener('click', onDoc);
			window.removeEventListener('keydown', onKey);
		};
	});

	// Inline edit (local). `editShift` remembers a shift-open (fork-full-copy, no gen);
	// `editAll` remembers a ctrl/cmd-open (apply the edit across every panel).
	let editing = $state(false);
	let editDraft = $state('');
	let editShift = $state(false);
	let editAll = $state(false);
	function startEdit(shift: boolean, all: boolean) {
		if (!canEdit) return;
		editing = true;
		editShift = shift;
		editAll = all;
		editDraft = msg.content;
	}
	function commitEdit() {
		onEdit(editDraft, editShift, editAll);
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
	$effect(() => {
		void msg.role;
		void msg.nodeId;
		void msg.content;
		editing = false;
		editDraft = '';
		editShift = false;
		editAll = false;
		copied = false;
		rawSingle = false;
		rawSamples = new Set();
		sampleCursor = 0;
		reasoningOpen = false;
		sendMenuOpen = false;
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
		</div>
		{#if sample.reasoning}
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
		{:else}
			<div class="sample-content">{@html prefillSplit ? renderPrefilled(sample.content, prefillSplit.answer, 'assistant') : renderContent(sample.content, 'assistant')}</div>
		{/if}
		<div class="message-actions sample-actions">
			<button class="btn-use" class:active={msg.activeSampleIndex === idx} data-tooltip="Make this the active branch & collapse to it (others stay as ‹k/N› siblings)" use:tip disabled={busy || !msg.sampleNodeIds?.[idx]} onclick={() => onSelectSample(idx)}>{msg.activeSampleIndex === idx ? '✓ active' : 'Make active'}</button>
			<button class="btn-use" data-tooltip="Keep only this sample — discard the others" use:tip disabled={busy || !msg.sampleNodeIds?.[idx]} onclick={() => onDiscardOthers(idx)}>Discard others</button>
			{#if sample.raw_text}
				<button class="btn-raw" class:active={rawSamples.has(idx)} onclick={() => toggleRawSample(idx)} title="Toggle raw model output with tags preserved">Raw</button>
			{/if}
			<button class="btn-tag" class:shift-alt={shiftDown} data-tooltip={shiftDown ? 'Bookmark instantly (no note)' : 'Bookmark with a note'} use:tip onclick={(e) => onTag(sample.content, idx, msg.totalSamples ?? null, sample.reasoning || '', e.shiftKey)}>
				{#if shiftDown}{@render tagQuickIcon()}{:else}{@render tagIcon()}{/if}
			</button>
			<button class="btn-act btn-act-danger sample-del" data-tooltip="Delete this sample" use:tip aria-label="Delete this sample" disabled={busy || !msg.sampleNodeIds?.[idx]} onclick={() => onDeleteSample(idx)}>
				<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V2.5h4V4M4.5 4l.6 9h5.8l.6-9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
			</button>
		</div>
	</div>
{/snippet}

<!-- Action icons. The shift-held variants signal the alternate action: regenerate
     becomes "replace in place" (square in the center), edit becomes "fork a full
     copy" (overlapping pages). -->
{#snippet sendIcon()}
	<!-- two-way transfer arrows (send this branch to another panel) -->
	<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M2 6h10M9.5 3.5 12 6 9.5 8.5M14 10H4M6.5 7.5 4 10l2.5 2.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{/snippet}
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
{#snippet trashAllIcon()}
	<!-- trash + a back layer = "delete every branch at this level" -->
	<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M5.5 5.5l.5 7.5h5.5l.5-7.5M5 5.5h8M7.5 5.5V4h3v1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 3.2h6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" /><path d="M2.6 3.2l.5 6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" /></svg>
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

<!-- Continue (prefill) an assistant turn: extend it; n-samples → branches to pick.
     ctrl/cmd (compare) = continue the same-depth turn in every panel. -->
{#snippet continueBtn()}
	<button
		class="btn-act"
		class:btn-act-all={allActive}
		data-tooltip={`Continue this message (extends it; n-samples → pick one)${allActive ? ' — in ALL panels' : ''}`}
		use:tip
		aria-label="Continue this message"
		onclick={(e) => onContinue(e.ctrlKey || e.metaKey)}
	>
		<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M8 3.5v9M3.5 8h9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" /></svg>
	</button>
{/snippet}

<!-- Copy: plain = this message's content; shift = the whole conversation as markdown. -->
{#snippet copyBtn()}
	<button
		class="btn-act"
		class:shift-alt={shiftDown}
		class:copied
		data-tooltip={copied ? 'Copied!' : shiftDown ? 'Copy the FULL conversation as markdown' : 'Copy this message'}
		use:tip
		aria-label="Copy"
		onclick={(e) => { onCopy(e.shiftKey); flashCopied(); }}
	>
		{#if copied}{@render checkIcon()}{:else if shiftDown}{@render copyAllIcon()}{:else}{@render copyIcon()}{/if}
	</button>
{/snippet}

<!-- Send this branch's context (root→here) into another panel's thread. -->
{#snippet sendToPicker()}
	{#if otherPanels.length && onSendToPanel && msg.nodeId != null}
		<div class="send-to" bind:this={sendWrap}>
			<button
				class="btn-act"
				class:shift-alt={sendMenuOpen}
				data-tooltip="Send this branch's context to another panel"
				use:tip
				aria-label="Send branch to another panel"
				aria-haspopup="menu"
				aria-expanded={sendMenuOpen}
				onclick={() => (sendMenuOpen = !sendMenuOpen)}
			>
				{@render sendIcon()}
			</button>
			{#if sendMenuOpen}
				<div class="send-to-menu" role="menu">
					{#each otherPanels as op (op.id)}
						<button
							class="send-to-item"
							role="menuitem"
							onclick={() => { sendMenuOpen = false; onSendToPanel?.(op.id); }}
						>→ {op.label}</button>
					{/each}
				</div>
			{/if}
		</div>
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

{#if msg.role !== 'assistant' || msg.content || msg.reasoning || isMultiSample || (msg.samples && msg.samples.some((x) => x && x.content))}
	<div class="message" style="background: {roleColor(msg.role)};">
		<div class="message-head">
			<div class="message-role">{msg.role}</div>
			{#if showSampleCycler}{@render sampleCycler()}{:else}{@render cycler()}{/if}
		</div>
		{#if msg.role === 'assistant' && msg.reasoning && !isMultiSample}
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
				<div class="message-actions turn-actions hover-actions">
					{@render regenGroup()}
					{@render continueBtn()}
					{@render copyBtn()}
					{@render sendToPicker()}
					{@render deleteBtn('Delete this turn')}
				</div>
			{/if}
		{:else if editing && msg.nodeId != null}
			{#if editShift}<div class="edit-hint">Shift-edit: forks a full editable copy of the conversation from here — nothing is generated.</div>{/if}
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
					{#if msg.raw_meta}{@render rawMetaDisclosure(msg.raw_meta)}{/if}
			{:else}
				<div class="message-content">{@html prefillSplit ? renderPrefilled(msg.content, prefillSplit.answer, msg.role) : renderContent(msg.content, msg.role)}</div>
			{/if}
			{#if msg.role !== 'system' && (msg.content || msg.raw_text)}
				<div class="message-actions hover-actions">
					{#if msg.raw_text}
						<button class="btn-raw" class:active={rawSingle} onclick={() => (rawSingle = !rawSingle)} title="Toggle raw model output with tags preserved">Raw</button>
					{/if}
					{@render copyBtn()}
					{@render sendToPicker()}
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
				</div>
			{/if}
		{/if}
	</div>
{/if}

<style>
	/* Message header: role label on the left, the ‹k/N› cycler pinned top-right
	   (level with the role) so it stays put as the message/card resizes. */
	.message-head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); margin-bottom: var(--space-2); }
	.message-head .message-role { margin-bottom: 0; }
	/* ── ‹k/N› cycler pill (branch siblings + sample cycle-view) ────── */
	.branch-cycle { display: flex; align-items: center; gap: 2px; padding: 1px 4px; border: 1px solid var(--color-border); border-radius: var(--radius-pill); background: var(--color-bg); width: fit-content; user-select: none; flex-shrink: 0; }
	.branch-cycle-btn { display: flex; align-items: center; justify-content: center; padding: 2px; background: none; border: none; color: var(--color-text-muted); cursor: pointer; border-radius: var(--radius-sm); }
	.branch-cycle-btn:hover:not(:disabled) { color: var(--color-accent); background: var(--color-accent-bg); }
	.branch-cycle-btn:disabled { opacity: 0.3; cursor: default; }
	.branch-cycle-count { font-size: 0.68rem; font-variant-numeric: tabular-nums; color: var(--color-text-secondary); min-width: 24px; text-align: center; }
	.active-sample { outline: 2px solid var(--color-accent); outline-offset: 1px; }
	.active-sample-tag { font-size: 0.62rem; color: var(--color-accent); font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; margin-left: var(--space-2); }
	.btn-use.active { background: var(--color-accent); border-color: var(--color-accent); color: white; }
	.edit-hint { font-size: 0.7rem; color: var(--color-text-muted); font-style: italic; margin-bottom: var(--space-1); line-height: 1.3; }
	/* Transfer-to-panel: icon button keeps its .btn-act size; the menu sizes to its
	   own content (max-content), so the BUTTON never grows to the option width. */
	.send-to { position: relative; display: inline-flex; }
	.send-to-menu { position: absolute; top: calc(100% + 3px); right: 0; z-index: 30; display: flex; flex-direction: column; gap: 1px; min-width: max-content; padding: 3px; background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius); box-shadow: 0 4px 14px #00000022; }
	.send-to-item { display: block; white-space: nowrap; text-align: left; padding: 4px 9px; background: none; border: none; border-radius: var(--radius-sm); color: var(--color-text-secondary); font-size: 0.72rem; cursor: pointer; }
	.send-to-item:hover { background: var(--color-accent-bg); color: var(--color-accent); }
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
