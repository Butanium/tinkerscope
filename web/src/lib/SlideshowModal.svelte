<!--
  Pins slideshow: one saved sample at a time (model/params meta, question,
  rendered response, note, system prompt) with prev/next nav + delete. The
  highlight toggles re-color the rendered response live. Pins + the current index
  are parent-owned (the parent loads/sorts/deletes); this view emits nav/delete.
  Arrow-left/right also navigate (when the overlay has focus, matching the
  original).
-->
<script lang="ts">
	import Modal from './Modal.svelte';
	import { renderContent } from './render';
	import { highlightStore, toggleHighlightRule } from './highlights.svelte';
	import type { Pin } from './types';

	let {
		pins,
		index,
		onnav,
		ondelete,
		onclose
	}: {
		pins: Pin[];
		index: number;
		onnav: (delta: number) => void;
		ondelete: (id: string) => void;
		onclose: () => void;
	} = $props();

	function formatDate(iso: string): string {
		if (!iso) return '';
		const d = new Date(iso);
		return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
	}

	function onArrows(e: KeyboardEvent) {
		if (e.key === 'ArrowLeft') onnav(-1);
		else if (e.key === 'ArrowRight') onnav(1);
	}
</script>

<Modal title="Pins" {onclose} onkeydown={onArrows} modalStyle="width: 720px; max-width: 95vw; max-height: 85vh;">
	{#snippet headerExtra()}
		<div class="slideshow-counter">{pins.length > 0 ? `${index + 1} / ${pins.length}` : 'Empty'}</div>
	{/snippet}
	{#if pins.length === 0}
		<div class="slideshow-empty">No pins saved yet. Use the tag button on any response to save it.</div>
	{:else}
		{#if highlightStore.rules.length > 0}
			<div class="hl-group" style="margin-bottom: var(--space-3);">
				{#each highlightStore.rules as rule (rule.id)}
					<button
						class="hl-btn"
						class:active={rule.enabled}
						style={rule.enabled ? `background:${rule.color}33;border-color:${rule.color};color:var(--color-text)` : ''}
						onclick={() => toggleHighlightRule(rule)}
					>{rule.name}</button>
				{/each}
			</div>
		{/if}
		{@const h = pins[index]}
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
				<div class="slideshow-response-text">{@html renderContent(h.response ?? '', 'assistant')}</div>
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
			<button class="slideshow-nav-btn" onclick={() => onnav(-1)} disabled={pins.length <= 1}>
				<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M10 3l-5 5 5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
			</button>
			<button class="btn-tag-delete" onclick={() => ondelete(h.id)}>Delete</button>
			<button class="slideshow-nav-btn" onclick={() => onnav(1)} disabled={pins.length <= 1}>
				<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
			</button>
		</div>
	{/if}
</Modal>

<style>
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
	.hl-group { display: flex; flex-wrap: wrap; gap: var(--space-1); }
	.hl-btn { background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius); padding: 4px 10px; font-size: 0.78rem; color: var(--color-text-secondary); cursor: pointer; transition: all 0.15s; min-width: 72px; text-align: center; }
	.hl-btn:hover { border-color: var(--color-text-muted); }
</style>
