<!--
  OpenRouter model manager: list saved reference models (remove any), and add new
  ones by typing to filter the full catalog. The saved list + catalog are
  parent-owned (it persists adds/removes via the API); this view emits
  pick/remove/refresh.
-->
<script lang="ts">
	import Modal from './Modal.svelte';
	import ModelTypeahead from './ModelTypeahead.svelte';
	import type { OpenRouterModel } from './types';

	let {
		models,
		catalog,
		loading,
		error,
		busy,
		keyMissing,
		onpick,
		onremove,
		onrefresh,
		onclose
	}: {
		models: OpenRouterModel[];
		catalog: OpenRouterModel[];
		loading: boolean;
		error: string | null;
		busy: boolean;
		keyMissing: boolean;
		onpick: (item: { id: string; label: string }) => void;
		onremove: (id: string) => void;
		onrefresh: () => void;
		onclose: () => void;
	} = $props();
</script>

<Modal title="OpenRouter models" {onclose} modalStyle="width: 520px; max-width: 90vw;">
	{#if keyMissing}
		<div class="unsampleable-note" style="margin-bottom: var(--space-3);">Sampling OpenRouter models needs OPENROUTER_API_KEY. You can still manage the list.</div>
	{/if}
	{#if models.length > 0}
		<div class="or-list">
			{#each models as m (m.openrouter_model)}
				<div class="or-row">
					<div class="or-row-text">
						<div class="or-row-label">{m.label || m.openrouter_model}</div>
						{#if m.label && m.label !== m.openrouter_model}
							<div class="or-row-id">{m.openrouter_model}</div>
						{/if}
					</div>
					<button class="or-row-remove" title="Remove this OpenRouter model" disabled={busy} onclick={() => onremove(m.openrouter_model)}>&times;</button>
				</div>
			{/each}
		</div>
	{:else}
		<div class="or-empty">No OpenRouter models saved yet.</div>
	{/if}
	<label class="sidebar-label" style="margin-top: var(--space-4);">Add a model — type to filter the catalog</label>
	<div style="margin-top: var(--space-2);">
		<ModelTypeahead
			items={catalog.map((m) => ({ id: m.openrouter_model, label: m.label || m.openrouter_model }))}
			placeholder="e.g. anthropic/claude — type to filter {catalog.length || '…'} models"
			{busy}
			{loading}
			{error}
			onpick={onpick}
		/>
	</div>
	<div class="tag-form-actions">
		<button class="or-refresh-link" onclick={onrefresh} disabled={loading}>Refresh catalog</button>
		<button class="btn-new" onclick={onclose}>Done</button>
	</div>
</Modal>

<style>
	.or-refresh-link { margin-right: auto; background: none; border: 1px solid var(--color-border); border-radius: var(--radius); padding: var(--space-2) var(--space-3); cursor: pointer; font-size: 0.78rem; color: var(--color-text-muted); font-weight: 500; }
	.or-refresh-link:hover:not(:disabled) { border-color: var(--color-accent); color: var(--color-accent); }
	.or-refresh-link:disabled { opacity: 0.5; cursor: wait; }
	.or-list { display: flex; flex-direction: column; gap: var(--space-1); }
	.or-row { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3); background: var(--color-bg); border: 1px solid var(--color-border-light); border-radius: var(--radius); }
	.or-row-text { flex: 1; min-width: 0; }
	.or-row-label { font-size: 0.82rem; color: var(--color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.or-row-id { font-size: 0.7rem; color: var(--color-text-muted); font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.or-row-remove { background: none; border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text-muted); width: 24px; height: 24px; line-height: 1; font-size: 1.1rem; flex-shrink: 0; cursor: pointer; }
	.or-row-remove:hover:not(:disabled) { background: #d97070; border-color: #d97070; color: white; }
	.or-row-remove:disabled { opacity: 0.4; cursor: not-allowed; }
	.or-empty { font-size: 0.8rem; color: var(--color-text-muted); font-style: italic; padding: var(--space-2) 0; }
</style>
