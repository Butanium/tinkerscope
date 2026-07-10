<script lang="ts">
	// Reusable type-to-filter combobox. The user types an id/label and a dropdown
	// shows case-insensitive substring matches over `items`; click or Enter picks.
	// No deps — keyboard-navigable (↑/↓/Enter/Esc). Used for both the Tinker base
	// model catalog and the OpenRouter catalog (the 341-model list is NEVER
	// rendered unfiltered — we cap visible rows and require a query to show all),
	// AND as the panel body of `ModelDropdown.svelte` (the sidebar's per-panel
	// model picker — a select-like trigger button wrapping this component).

	import TruncLabel from './TruncLabel.svelte';

	// `search`: extra hidden text matched but never displayed (e.g. a run's
	// wandb project / base model, so filtering isn't limited to the visible
	// label). `disabled`: shown but not pickable (e.g. a run tinker can no
	// longer sample) — greyed out, click/Enter on it is a no-op.
	type Item = { id: string; label: string; disabled?: boolean; search?: string };

	let {
		items,
		placeholder = 'Type to filter…',
		busy = false,
		loading = false,
		error = null,
		maxRows = 50,
		onpick
	}: {
		items: Item[];
		placeholder?: string;
		busy?: boolean;
		loading?: boolean;
		error?: string | null;
		maxRows?: number;
		onpick: (item: Item) => void;
	} = $props();

	let query = $state('');
	let active = $state(0);
	let inputEl: HTMLInputElement | undefined = $state();

	function isMatch(it: Item, q: string): boolean {
		return (
			it.id.toLowerCase().includes(q) ||
			it.label.toLowerCase().includes(q) ||
			(it.search ?? '').toLowerCase().includes(q)
		);
	}

	const filtered = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const matches = q ? items.filter((it) => isMatch(it, q)) : items;
		return matches.slice(0, maxRows);
	});

	// The visible labels drive sibling-aware tail-preserving truncation: each
	// row's label is split so its divergence from its closest visible sibling
	// survives (runs sharing a long prefix stay distinguishable — see TruncLabel).
	const visibleLabels = $derived(filtered.map((it) => it.label));

	// Total matches (for the "+N more" hint when the list is truncated).
	const totalMatches = $derived.by(() => {
		const q = query.trim().toLowerCase();
		if (!q) return items.length;
		return items.filter((it) => isMatch(it, q)).length;
	});

	// Keep the active index in range — and off a disabled row — as the filtered
	// list changes (typing narrows it, items reload, etc).
	$effect(() => {
		if (active >= filtered.length) active = Math.max(0, filtered.length - 1);
		if (filtered[active]?.disabled) {
			const firstEnabled = filtered.findIndex((it) => !it.disabled);
			if (firstEnabled !== -1) active = firstEnabled;
		}
	});

	export function focus() {
		inputEl?.focus();
	}

	function pick(it: Item) {
		if (it.disabled) return;
		onpick(it);
		query = '';
		active = 0;
	}

	/** Next index in `dir` (±1), skipping disabled rows; wraps around; gives up
	 *  after a full lap (all-disabled list) and returns the current index. */
	function stepActive(from: number, dir: 1 | -1): number {
		if (!filtered.length) return 0;
		let i = from;
		for (let n = 0; n < filtered.length; n++) {
			i = (i + dir + filtered.length) % filtered.length;
			if (!filtered[i]?.disabled) return i;
		}
		return from;
	}

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			if (filtered.length) active = stepActive(active, 1);
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			if (filtered.length) active = stepActive(active, -1);
		} else if (e.key === 'Enter') {
			e.preventDefault();
			const it = filtered[active];
			if (it) pick(it);
		} else if (e.key === 'Escape') {
			query = '';
		}
	}
</script>

<div class="typeahead">
	<input
		type="text"
		class="typeahead-input"
		bind:value={query}
		bind:this={inputEl}
		{placeholder}
		disabled={busy}
		onkeydown={onKeydown}
		autocomplete="off"
		spellcheck="false"
	/>
	{#if loading}
		<div class="typeahead-status">Loading catalog…</div>
	{:else if error}
		<div class="typeahead-status typeahead-error">{error}</div>
	{:else}
		<div class="typeahead-list">
			{#if filtered.length === 0}
				<div class="typeahead-empty">No matches</div>
			{:else}
				{#each filtered as it, i (it.id)}
					<button
						type="button"
						class="typeahead-row"
						class:active={i === active}
						class:disabled={it.disabled}
						disabled={busy || it.disabled}
						onmouseenter={() => { if (!it.disabled) active = i; }}
						onclick={() => pick(it)}
					>
						<span class="typeahead-row-label"><TruncLabel label={it.label} siblings={visibleLabels} /></span>
						{#if it.label !== it.id}
							<span class="typeahead-row-id">{it.id}</span>
						{/if}
					</button>
				{/each}
				{#if totalMatches > filtered.length}
					<div class="typeahead-more">+{totalMatches - filtered.length} more — keep typing to narrow</div>
				{/if}
			{/if}
		</div>
	{/if}
</div>

<style>
	.typeahead {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
	.typeahead-input {
		padding: var(--space-2) var(--space-3);
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		color: var(--color-text);
		font-size: 0.82rem;
		font-family: var(--font-mono);
		width: 100%;
	}
	.typeahead-input:focus {
		outline: none;
		border-color: var(--color-accent);
	}
	.typeahead-status {
		font-size: 0.78rem;
		color: var(--color-text-muted);
		padding: var(--space-2) 0;
	}
	.typeahead-error {
		color: #b45309;
	}
	:global(.dark) .typeahead-error {
		color: #fbbf24;
	}
	.typeahead-list {
		display: flex;
		flex-direction: column;
		max-height: 260px;
		overflow-y: auto;
		border: 1px solid var(--color-border-light);
		border-radius: var(--radius);
		background: var(--color-bg);
	}
	.typeahead-empty {
		font-size: 0.8rem;
		color: var(--color-text-muted);
		font-style: italic;
		padding: var(--space-2) var(--space-3);
	}
	.typeahead-row {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 1px;
		text-align: left;
		width: 100%;
		padding: var(--space-2) var(--space-3);
		background: none;
		border: none;
		border-bottom: 1px solid var(--color-border-light);
		cursor: pointer;
	}
	.typeahead-row:last-child {
		border-bottom: none;
	}
	.typeahead-row.active {
		background: var(--color-accent-bg);
	}
	.typeahead-row:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.typeahead-row-label {
		font-size: 0.82rem;
		color: var(--color-text);
		align-self: stretch;
		min-width: 0;
		max-width: 100%;
		overflow: hidden;
	}
	.typeahead-row-id {
		font-size: 0.7rem;
		color: var(--color-text-muted);
		font-family: var(--font-mono);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 100%;
	}
	.typeahead-more {
		font-size: 0.7rem;
		color: var(--color-text-muted);
		font-style: italic;
		padding: var(--space-2) var(--space-3);
		border-top: 1px solid var(--color-border-light);
	}
</style>
