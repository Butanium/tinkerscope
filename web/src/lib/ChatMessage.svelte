<script lang="ts">
	// One chat row: a committed transcript message OR the live "bucket" turn
	// (latest turn's N variants / streaming progress). Owns its OWN raw-view and
	// edit-mode UI state (these used to be global Sets keyed by panel+index in
	// +page — local is simpler). Transcript mutations are delegated to the parent
	// via callbacks, since it owns the shared PlaygroundState.
	import { renderContent } from '$lib/render';
	import { tip } from '$lib/tooltip.svelte';
	import type { ViewMessage } from '$lib/types';

	let {
		msg,
		prevUserMsg,
		isLastAssistant,
		anyRunning,
		thinking,
		onRegenerate,
		onDelete,
		onUseSample,
		onEdit,
		onTag
	}: {
		msg: ViewMessage;
		prevUserMsg?: string;
		isLastAssistant: boolean;
		anyRunning: boolean;
		thinking: boolean;
		onRegenerate: () => void;
		onDelete: () => void;
		onUseSample: (content: string) => void;
		onEdit: (content: string) => void;
		onTag: (content: string, sampleIndex: number | null, totalSamples: number | null, reasoning: string) => void;
	} = $props();

	let isMultiSample = $derived(!!(msg.totalSamples && msg.totalSamples > 1));
	let canEdit = $derived(msg.transcriptIdx != null && !anyRunning);

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

	// Inline edit (local).
	let editing = $state(false);
	let editDraft = $state('');
	function startEdit() {
		if (!canEdit) return;
		editing = true;
		editDraft = msg.content;
	}
	function commitEdit() {
		onEdit(editDraft);
		editing = false;
	}
	function cancelEdit() {
		editing = false;
	}

	// The chat column's {#each} is keyed by position, so a transcript reshape
	// (deleting an earlier row, a regenerate-truncate, or a CLI/other-tab turn)
	// can hand THIS already-mounted instance a *different* message. Drop any
	// in-progress edit / raw-view state when that happens, so an open editor can
	// never Save its stale draft onto the wrong row. Tracks role+idx+content as
	// the message's identity; the resets are no-ops during normal streaming (no
	// editor is open while a turn is still running).
	$effect(() => {
		void msg.role;
		void msg.transcriptIdx;
		void msg.content;
		editing = false;
		editDraft = '';
		rawSingle = false;
		rawSamples = new Set();
	});
</script>

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
								{#if rawSamples.has(idx) && sample.raw_text}
									<pre class="raw-text-view">{sample.raw_text}</pre>
								{:else}
									<div class="sample-content">{@html renderContent(sample.content, prevUserMsg)}</div>
								{/if}
								<div class="message-actions sample-actions">
									<button class="btn-use" data-tooltip="Use this sample as the reply & continue" use:tip disabled={anyRunning} onclick={() => onUseSample(sample.content)}>Use this</button>
									{#if sample.raw_text}
										<button class="btn-raw" class:active={rawSamples.has(idx)} onclick={() => toggleRawSample(idx)} title="Toggle raw model output with tags preserved">Raw</button>
									{/if}
									<button class="btn-tag" onclick={() => onTag(sample.content, idx, msg.totalSamples ?? null, sample.reasoning || '')} title="Save this response as a highlight with a note">
										<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M2 2h6l6 6-6 6-6-6V2Z" stroke="currentColor" stroke-width="1.5" /><circle cx="5.5" cy="5.5" r="1" fill="currentColor" /></svg>
									</button>
								</div>
							</div>
						{/if}
					{/each}
				</div>
			{/if}
			{#if allDone && msg.transcriptIdx != null && !anyRunning}
				<div class="message-actions turn-actions hover-actions">
					<button class="btn-act" data-tooltip="Regenerate all samples" use:tip aria-label="Regenerate" onclick={onRegenerate}>
						<svg width="13" height="13" viewBox="0 0 14 14" fill="none"><path d="M1.5 7a5.5 5.5 0 0 1 9.9-3.3M12.5 7a5.5 5.5 0 0 1-9.9 3.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /><path d="M11.5 1v3h-3M2.5 13v-3h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
					</button>
					<button class="btn-act btn-act-danger" data-tooltip="Delete this turn" use:tip aria-label="Delete" onclick={onDelete}>
						<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V2.5h4V4M4.5 4l.6 9h5.8l.6-9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
					</button>
				</div>
			{/if}
		{:else if editing && msg.transcriptIdx != null}
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
				<button class="btn-edit-save" onclick={commitEdit}>Save</button>
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
					{#if msg.role === 'assistant' && canEdit}
						<button class="btn-act" data-tooltip="Regenerate from here" use:tip aria-label="Regenerate" onclick={onRegenerate}>
							<svg width="13" height="13" viewBox="0 0 14 14" fill="none"><path d="M1.5 7a5.5 5.5 0 0 1 9.9-3.3M12.5 7a5.5 5.5 0 0 1-9.9 3.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /><path d="M11.5 1v3h-3M2.5 13v-3h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
						</button>
					{/if}
					{#if canEdit}
						<button class="btn-act" data-tooltip="Edit message" use:tip aria-label="Edit" onclick={startEdit}>
							<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M10.5 2.5l3 3L6 13l-3.5.5L3 10l7.5-7.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /></svg>
						</button>
						<button class="btn-act btn-act-danger" data-tooltip="Delete message" use:tip aria-label="Delete" onclick={onDelete}>
							<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V2.5h4V4M4.5 4l.6 9h5.8l.6-9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
						</button>
					{/if}
					{#if msg.role === 'assistant' && msg.content}
						<button class="btn-tag" onclick={() => onTag(msg.content, null, null, msg.reasoning || '')} title="Save this response as a highlight with a note">
							<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M2 2h6l6 6-6 6-6-6V2Z" stroke="currentColor" stroke-width="1.5" /><circle cx="5.5" cy="5.5" r="1" fill="currentColor" /></svg>
						</button>
					{/if}
				</div>
			{/if}
		{/if}
	</div>
{/if}
