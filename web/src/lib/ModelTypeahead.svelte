<script lang="ts">
	// Reusable type-to-filter combobox. The user types an id/label and a dropdown
	// shows case-insensitive substring matches over `items`; click or Enter picks.
	// No deps — keyboard-navigable (↑/↓/Enter/Esc). Used for both the Tinker base
	// model catalog and the OpenRouter catalog (the 341-model list is NEVER
	// rendered unfiltered — we cap visible rows and require a query to show all).

	type Item = { id: string; label: string };

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

	const filtered = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const matches = q
			? items.filter((it) => it.id.toLowerCase().includes(q) || it.label.toLowerCase().includes(q))
			: items;
		return matches.slice(0, maxRows);
	});

	// Total matches (for the "+N more" hint when the list is truncated).
	const totalMatches = $derived.by(() => {
		const q = query.trim().toLowerCase();
		if (!q) return items.length;
		return items.filter(
			(it) => it.id.toLowerCase().includes(q) || it.label.toLowerCase().includes(q)
		).length;
	});

	// Keep the active index in range as the filtered list changes.
	$effect(() => {
		void filtered;
		if (active >= filtered.length) active = Math.max(0, filtered.length - 1);
	});

	export function focus() {
		inputEl?.focus();
	}

	function pick(it: Item) {
		onpick(it);
		query = '';
		active = 0;
	}

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			if (filtered.length) active = (active + 1) % filtered.length;
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			if (filtered.length) active = (active - 1 + filtered.length) % filtered.length;
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
						disabled={busy}
						onmouseenter={() => (active = i)}
						onclick={() => pick(it)}
					>
						<span class="typeahead-row-label">{it.label}</span>
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
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 100%;
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
