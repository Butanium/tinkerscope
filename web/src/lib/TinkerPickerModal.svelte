<!--
  Tinker base-model / loose-checkpoint picker: type to filter the catalog of raw
  base models + sampler checkpoints tinker serves right now, pick one into the
  panel. Catalog is parent-owned; this view emits the pick.
-->
<script lang="ts">
  import Modal from './Modal.svelte';
  import ModelTypeahead from './ModelTypeahead.svelte';
  import type { TinkerModel } from './types';

  let {
    models,
    loading,
    error,
    keyMissing,
    onpick,
    onrefresh,
    onclose
  }: {
    models: TinkerModel[];
    loading: boolean;
    error: string | null;
    keyMissing: boolean;
    onpick: (item: { id: string; label: string }) => void;
    onrefresh: () => void;
    onclose: () => void;
  } = $props();
</script>

<Modal title="Tinker models" {onclose} modalStyle="width: 520px; max-width: 90vw;">
  <div class="or-empty" style="font-style: normal; padding-bottom: var(--space-2);">Raw base models (no LoRA) and loose sampler checkpoints tinker serves right now. Pick one to use it in this panel.</div>
  {#if keyMissing}
    <div class="unsampleable-note" style="margin-bottom: var(--space-3);">Sampling needs TINKER_API_KEY. You can still pick a model.</div>
  {/if}
  <div class="picker-label-row">
    <label class="sidebar-label">Type to filter — base model names or checkpoint UUIDs</label>
    <button
      class="btn-refresh"
      class:spinning={loading}
      onclick={onrefresh}
      disabled={loading}
      title="Re-fetch from tinker (shows checkpoints created since this list loaded)"
    >⟳</button>
  </div>
  <div style="margin-top: var(--space-2);">
    <ModelTypeahead
      items={models.map((m) => ({ id: m.id, label: m.label || m.id }))}
      placeholder="e.g. Qwen or a UUID — type to filter {models.length || '…'} base models + checkpoints"
      {loading}
      {error}
      onpick={onpick}
    />
  </div>
  <div class="tag-form-actions">
    <button class="btn-new" onclick={onclose}>Done</button>
  </div>
</Modal>

<style>
  .or-empty { font-size: 0.8rem; color: var(--color-text-muted); font-style: italic; padding: var(--space-2) 0; }
  .picker-label-row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); }
  .btn-refresh {
    background: none; border: 1px solid var(--color-border); border-radius: var(--radius-sm);
    color: var(--color-text-muted); cursor: pointer; font-size: 0.85rem; line-height: 1;
    padding: 2px 6px;
  }
  .btn-refresh:hover:not(:disabled) { color: var(--color-text); border-color: var(--color-text-muted); }
  .btn-refresh:disabled { cursor: wait; opacity: 0.5; }
  .btn-refresh.spinning { animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
