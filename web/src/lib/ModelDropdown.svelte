<!--
  Select-like trigger button that opens a floating ModelTypeahead panel — the
  sidebar's per-panel model picker. Replaces a native <select> so filtering
  happens by just clicking the control and typing (no separate "Filter
  models…" textbox). Keyboard/click behavior of a combobox:
    - click / Enter on the trigger → opens, filter input auto-focused
    - type → narrows (delegated to ModelTypeahead)
    - ↑/↓ → navigate, Enter → pick + close, Escape → close (focus returns to
      the trigger), click outside → close
  Purely a UI shell: the caller owns the item list + selection id/label and
  gets a plain `onpick(id)` callback, same shape as the old <select>'s
  onchange handler.
-->
<script lang="ts">
	import { tick } from 'svelte';
	import ModelTypeahead from './ModelTypeahead.svelte';
	import TruncLabel from './TruncLabel.svelte';

	type Item = { id: string; label: string; disabled?: boolean; search?: string };

	let {
		items,
		selectedLabel,
		placeholder = 'Select a model…',
		filterPlaceholder = 'Type to filter…',
		disabled = false,
		onpick
	}: {
		items: Item[];
		/** Display text for the trigger when something is selected. */
		selectedLabel: string;
		/** Trigger text when nothing is selected yet. */
		placeholder?: string;
		filterPlaceholder?: string;
		disabled?: boolean;
		onpick: (id: string) => void;
	} = $props();

	let open = $state(false);
	let wrapEl: HTMLDivElement | undefined = $state();
	let triggerEl: HTMLButtonElement | undefined = $state();
	let typeaheadRef: { focus: () => void } | undefined = $state();

	function toggle() {
		if (disabled) return;
		open = !open;
	}
	function close() {
		open = false;
	}
	function handlePick(it: { id: string; label: string }) {
		onpick(it.id);
		close();
	}

	// While open: focus the filter input, and close on outside click / Escape.
	// A document-level listener (rather than teaching ModelTypeahead about
	// "close") is what lets Escape close the whole combobox even though
	// ModelTypeahead's own Escape handler just clears its query text — both
	// fire off the same keypress, harmlessly.
	$effect(() => {
		if (!open) return;
		tick().then(() => typeaheadRef?.focus());
		const onDocMousedown = (e: MouseEvent) => {
			if (wrapEl && !wrapEl.contains(e.target as Node)) close();
		};
		const onDocKeydown = (e: KeyboardEvent) => {
			if (e.key === 'Escape') {
				close();
				triggerEl?.focus();
			}
		};
		document.addEventListener('mousedown', onDocMousedown);
		document.addEventListener('keydown', onDocKeydown);
		return () => {
			document.removeEventListener('mousedown', onDocMousedown);
			document.removeEventListener('keydown', onDocKeydown);
		};
	});
</script>

<div class="model-dropdown" bind:this={wrapEl}>
	<button
		type="button"
		class="model-dropdown-trigger"
		class:open
		bind:this={triggerEl}
		{disabled}
		aria-haspopup="listbox"
		aria-expanded={open}
		onclick={toggle}
	>
		<span class="model-dropdown-trigger-label" class:placeholder={!selectedLabel}>
			{#if selectedLabel}<TruncLabel label={selectedLabel} />{:else}{placeholder}{/if}
		</span>
		<svg class="model-dropdown-chevron" class:open width="12" height="12" viewBox="0 0 16 16" fill="none">
			<path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
		</svg>
	</button>
	{#if open}
		<div class="model-dropdown-panel">
			<ModelTypeahead bind:this={typeaheadRef} {items} placeholder={filterPlaceholder} onpick={handlePick} />
		</div>
	{/if}
</div>

<style>
	.model-dropdown {
		position: relative;
	}
	.model-dropdown-trigger {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
		width: 100%;
		padding: var(--space-2) var(--space-3);
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		color: var(--color-text);
		font-size: 0.82rem;
		font-family: inherit;
		cursor: pointer;
		text-align: left;
	}
	.model-dropdown-trigger:hover:not(:disabled) {
		border-color: var(--color-accent);
	}
	.model-dropdown-trigger.open {
		border-color: var(--color-accent);
	}
	.model-dropdown-trigger:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.model-dropdown-trigger-label {
		flex: 1;
		min-width: 0;
		display: flex;
		align-items: center;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.model-dropdown-trigger-label.placeholder {
		color: var(--color-text-muted);
	}
	.model-dropdown-chevron {
		flex-shrink: 0;
		color: var(--color-text-muted);
		transition: transform 0.15s;
	}
	.model-dropdown-chevron.open {
		transform: rotate(180deg);
	}
	.model-dropdown-panel {
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		right: 0;
		z-index: 20;
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
		padding: var(--space-2);
	}
</style>
