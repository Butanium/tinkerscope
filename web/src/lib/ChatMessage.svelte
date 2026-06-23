<script lang="ts">
	// One chat row: a committed tree node OR the live "bucket" turn (latest turn's
	// N variants / streaming progress). Owns its OWN raw-view + edit-mode UI state.
	// Tree mutations are delegated to the parent via callbacks (it owns the tree).
	// Branching adds: a ‹k/N› sibling cycler, regenerate on user turns, edit that
	// FORKS (shift+click = fork-and-copy-downstream), and n>1 cards that select a
	// sibling branch instead of "use this".
	import { renderContent } from '$lib/render';
	import { tip } from '$lib/tooltip.svelte';
	import type { ViewMessage } from '$lib/types';

	let {
		msg,
		prevUserMsg,
		isLastAssistant,
		busy,
		shiftDown,
		showRegenAll,
		thinking,
		onRegenerate,
		onRegenerateAll,
		onContinue,
		onDelete,
		onSelectSample,
		onDiscardOthers,
		onDeleteSample,
		onEdit,
		onTag,
		onCycle
	}: {
		msg: ViewMessage;
		prevUserMsg?: string;
		isLastAssistant: boolean;
		busy: boolean; // THIS panel busy (per-panel; lets the other panel stay editable)
		shiftDown: boolean; // shift held → show the alternate-action affordance
		showRegenAll: boolean; // compare mode → also offer "regenerate in both panels"
		thinking: boolean;
		onRegenerate: (replace: boolean) => void;
		onRegenerateAll: (replace: boolean) => void;
		onContinue: (all: boolean) => void;
		onDelete: (all: boolean) => void;
		onSelectSample: (sampleIndex: number) => void;
		onDiscardOthers: (sampleIndex: number) => void;
		onDeleteSample: (sampleIndex: number) => void;
		onEdit: (content: string, copyDownstream: boolean) => void;
		onTag: (content: string, sampleIndex: number | null, totalSamples: number | null, reasoning: string, quick: boolean) => void;
		onCycle: (delta: number) => void;
	} = $props();

	let isMultiSample = $derived(!!(msg.totalSamples && msg.totalSamples > 1));
	let canEdit = $derived(msg.nodeId != null && !busy);
	// ‹k/N› on any committed row with siblings (the n>1 bucket uses its cards).
	let hasSiblings = $derived(!!(msg.sib && msg.sib.count > 1) && !isMultiSample);

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

	// Inline edit (local). `editShift` remembers whether the edit was opened with
	// shift held → fork-and-copy-the-whole-downstream-conversation (no generation).
	let editing = $state(false);
	let editDraft = $state('');
	let editShift = $state(false);
	function startEdit(shift: boolean) {
		if (!canEdit) return;
		editing = true;
		editShift = shift;
		editDraft = msg.content;
	}
	function commitEdit() {
		onEdit(editDraft, editShift);
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
		rawSingle = false;
		rawSamples = new Set();
	});
</script>

{#snippet cycler()}
	{#if hasSiblings && msg.sib}
		<div class="branch-cycle" data-testid="branch-cycle">
			<button class="branch-cycle-btn" aria-label="Previous branch" disabled={busy || msg.sib.index <= 0} onclick={() => onCycle(-1)}>
				<svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M10 3l-5 5 5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
			</button>
			<span class="branch-cycle-count">{msg.sib.index + 1}/{msg.sib.count}</span>
			<button class="branch-cycle-btn" aria-label="Next branch" disabled={busy || msg.sib.index >= msg.sib.count - 1} onclick={() => onCycle(1)}>
				<svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
			</button>
		</div>
	{/if}
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
{#snippet trashAllIcon()}
	<!-- trash + a back layer = "delete every branch at this level" -->
	<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M5.5 5.5l.5 7.5h5.5l.5-7.5M5 5.5h8M7.5 5.5V4h3v1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 3.2h6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" /><path d="M2.6 3.2l.5 6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" /></svg>
{/snippet}

<!-- Continue (prefill) an assistant turn: extend it; n-samples → branches to pick.
     shift (compare) = continue the same turn in every panel. -->
{#snippet continueBtn()}
	<button
		class="btn-act"
		class:shift-alt={shiftDown}
		class:btn-act-all={shiftDown && showRegenAll}
		data-tooltip={shiftDown && showRegenAll ? 'Continue this message in BOTH panels' : 'Continue this message (extends it; n-samples → pick one)'}
		use:tip
		aria-label="Continue this message"
		onclick={(e) => onContinue(e.shiftKey)}
	>
		<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M8 3.5v9M3.5 8h9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" /></svg>
	</button>
{/snippet}

<!-- Delete button (used in both toolbars): shift → delete ALL sibling branches. -->
{#snippet deleteBtn(label: string)}
	<button
		class="btn-act btn-act-danger"
		class:shift-alt-danger={shiftDown}
		data-tooltip={shiftDown ? 'Delete ALL branches at this turn' : label}
		use:tip
		aria-label={shiftDown ? 'Delete all branches' : 'Delete'}
		onclick={(e) => onDelete(e.shiftKey)}
	>
		{#if shiftDown}{@render trashAllIcon()}{:else}{@render trashIcon()}{/if}
	</button>
{/snippet}

<!-- Regenerate group: this-panel regen + (compare only) regen-both. Both swap to
     the replace icon while shift is held. -->
{#snippet regenGroup()}
	<button
		class="btn-act"
		class:shift-alt={shiftDown}
		data-tooltip={shiftDown ? 'Replace this branch in place (other siblings kept)' : 'Regenerate → new sibling branch'}
		use:tip
		aria-label="Regenerate"
		onclick={(e) => onRegenerate(e.shiftKey)}
	>
		{#if shiftDown}{@render replaceIcon()}{:else}{@render regenIcon()}{/if}
	</button>
	{#if showRegenAll}
		<button
			class="btn-act btn-act-all"
			class:shift-alt={shiftDown}
			data-tooltip={shiftDown ? 'Replace this branch in BOTH panels' : 'Regenerate in BOTH panels'}
			use:tip
			aria-label="Regenerate in both panels"
			onclick={(e) => onRegenerateAll(e.shiftKey)}
		>
			{#if shiftDown}{@render replaceIcon()}{:else}{@render regenIcon()}{/if}
		</button>
	{/if}
{/snippet}

{#if msg.role !== 'assistant' || msg.content || msg.reasoning || isMultiSample || (msg.samples && msg.samples.some((x) => x && x.content))}
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
						{#if completedCount === 0 && thinking}
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
							<div class="sample-card" class:active-sample={msg.activeSampleIndex === idx}>
								<div class="sample-header">
									<span>Sample {idx + 1}</span>
									{#if msg.activeSampleIndex === idx}<span class="active-sample-tag">active branch</span>{/if}
								</div>
								{#if sample.reasoning}
									<details class="sample-reasoning-block">
										<summary class="sample-reasoning-toggle">
											<span>Reasoning</span>
											<svg class="thinking-chevron" width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
										</summary>
										<div class="sample-reasoning">{@html renderContent(sample.reasoning, prevUserMsg)}</div>
									</details>
								{/if}
								{#if rawSamples.has(idx) && sample.raw_text}
									<pre class="raw-text-view">{sample.raw_text}</pre>
								{:else}
									<div class="sample-content">{@html renderContent(sample.content, prevUserMsg)}</div>
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
						{/if}
					{/each}
				</div>
			{/if}
			{#if allDone && msg.nodeId != null && !busy}
				<div class="message-actions turn-actions hover-actions">
					{@render regenGroup()}
					{@render continueBtn()}
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
			{:else}
				<div class="message-content">{@html renderContent(msg.content, prevUserMsg)}</div>
			{/if}
			{#if msg.role !== 'system' && (msg.content || msg.raw_text)}
				<div class="message-actions hover-actions">
					{#if msg.raw_text}
						<button class="btn-raw" class:active={rawSingle} onclick={() => (rawSingle = !rawSingle)} title="Toggle raw model output with tags preserved">Raw</button>
					{/if}
					{#if canEdit}
						{@render regenGroup()}
						{#if msg.role === 'assistant'}{@render continueBtn()}{/if}
						<button
							class="btn-act"
							class:shift-alt={shiftDown && msg.role === 'user'}
							data-tooltip={msg.role === 'user'
								? (shiftDown ? 'Edit → fork a FULL editable copy of the conversation from here (no generation)' : 'Edit → fork + regenerate (shift: fork full copy)')
								: 'Edit → new branch'}
							use:tip
							aria-label="Edit"
							onclick={(e) => startEdit(e.shiftKey)}
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
		{@render cycler()}
	</div>
{/if}

<style>
	/* ── ‹k/N› branch cycler ──────────────────────────────────────── */
	.branch-cycle { display: inline-flex; align-items: center; gap: 2px; margin-top: var(--space-2); padding: 1px 4px; border: 1px solid var(--color-border); border-radius: var(--radius-pill); background: var(--color-bg); width: fit-content; user-select: none; }
	.branch-cycle-btn { display: flex; align-items: center; justify-content: center; padding: 2px; background: none; border: none; color: var(--color-text-muted); cursor: pointer; border-radius: var(--radius-sm); }
	.branch-cycle-btn:hover:not(:disabled) { color: var(--color-accent); background: var(--color-accent-bg); }
	.branch-cycle-btn:disabled { opacity: 0.3; cursor: default; }
	.branch-cycle-count { font-size: 0.68rem; font-variant-numeric: tabular-nums; color: var(--color-text-secondary); min-width: 24px; text-align: center; }
	.active-sample { outline: 2px solid var(--color-accent); outline-offset: 1px; }
	.active-sample-tag { font-size: 0.62rem; color: var(--color-accent); font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; margin-left: var(--space-2); }
	.btn-use.active { background: var(--color-accent); border-color: var(--color-accent); color: white; }
	.edit-hint { font-size: 0.7rem; color: var(--color-text-muted); font-style: italic; margin-bottom: var(--space-1); line-height: 1.3; }
</style>
