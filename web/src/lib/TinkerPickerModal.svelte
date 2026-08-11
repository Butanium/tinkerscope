<!--
  Tinker base-model / loose-checkpoint picker: type to filter the catalog of raw
  base models + sampler checkpoints tinker serves right now, pick one into the
  panel. Catalog is parent-owned; this view emits the pick.

  The search box is also the way IN for a checkpoint no local list knows: a path a
  collaborator sent you was trained on their account, so it is absent from the
  sweep and from every discovered run. Typing one yields zero matches, and instead
  of "No matches" the empty area offers to add it — asking tinker whether the path
  is real (~270 ms) so the answer is green/red rather than a guess.
-->
<script lang="ts">
  import Modal from './Modal.svelte';
  import Typeahead from './Typeahead.svelte';
  import { api } from './api';
  import { looksLikeSamplerPath, shortPathLabel } from './tinker-path';
  import type { TinkerModel, TinkerProbe } from './types';

  let {
    models,
    loading,
    error,
    keyMissing,
    onpick,
    onaddcustom,
    onrefresh,
    onclose
  }: {
    models: TinkerModel[];
    loading: boolean;
    error: string | null;
    keyMissing: boolean;
    onpick: (item: { id: string; label: string }) => void;
    /** A probed-available custom sampler path the catalog did not contain. */
    onaddcustom: (sampler_path: string) => void;
    onrefresh: () => void;
    onclose: () => void;
  } = $props();

  // One probe per distinct path, cached by the path itself — the query changes on
  // every keystroke and a re-fire would cost a round trip per character.
  let probed = $state<Record<string, TinkerProbe>>({});
  const inFlight = new Set<string>();
  let query = $state('');

  async function probe(path: string) {
    if (probed[path] || inFlight.has(path)) return;
    inFlight.add(path);
    try {
      probed = { ...probed, [path]: await api.probeTinkerModel(path) };
    } catch (e: any) {
      probed = { ...probed, [path]: { available: false, base_model: null, error: e?.message ?? String(e) } };
    } finally {
      inFlight.delete(path);
    }
  }

  // Probing every keystroke would fire one tinker call per PREFIX of a path being
  // typed — ~55 for a real one. They are live API calls, they serialize behind the
  // sampler client lock, and they exhaust the browser's ~6 connections per origin,
  // so the next request the page makes (saving a name, loading a catalog) queues
  // behind the pile. Caught by browser_tinker_custom_ckpt.py, which types where the
  // first draft of that smoke pasted.
  const PROBE_DEBOUNCE_MS = 350;
  let toProbe = $state('');

  $effect(() => {
    const path = query.trim();
    if (!looksLikeSamplerPath(path)) return;
    const t = setTimeout(() => (toProbe = path), PROBE_DEBOUNCE_MS);
    return () => clearTimeout(t);
  });

  $effect(() => {
    if (toProbe) probe(toProbe);
  });
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
    <Typeahead
      items={models.map((m) => ({ id: m.id, label: m.label || m.id }))}
      placeholder="e.g. Qwen, a UUID, or paste a tinker:// path — filter {models.length || '…'} base models + checkpoints"
      {loading}
      {error}
      onpick={onpick}
      onquery={(q) => (query = q)}
    >
      {#snippet emptyAction(q: string)}
        {@const path = q.trim()}
        {#if looksLikeSamplerPath(path)}
          {@const p = probed[path]}
          {#if !p}
            <div class="add-custom probing" data-testid="add-custom">
              <span class="spinner"></span>
              <span class="add-custom-text">Checking <code>{shortPathLabel(path)}</code> with tinker…</span>
            </div>
          {:else if p.available}
            <button
              type="button"
              class="add-custom ok"
              data-testid="add-custom"
              onclick={() => onaddcustom(path)}
            >
              <span class="dot"></span>
              <span class="add-custom-text">
                Add custom checkpoint <code>{shortPathLabel(path)}</code>
                {#if p.base_model}<span class="add-custom-sub">runs on {p.base_model}</span>{/if}
              </span>
            </button>
          {:else}
            <div class="add-custom bad" data-testid="add-custom">
              <span class="dot"></span>
              <span class="add-custom-text">
                Tinker does not serve this path
                <span class="add-custom-sub">{p.error ?? 'unknown error'}</span>
              </span>
            </div>
          {/if}
        {:else}
          <div class="typeahead-empty-fallback">No matches</div>
        {/if}
      {/snippet}
    </Typeahead>
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

  .typeahead-empty-fallback { padding: var(--space-3); color: var(--color-text-muted); font-size: 0.8rem; font-style: italic; }

  /* The add-a-custom-checkpoint row: same three states as the probe. */
  .add-custom { display: flex; align-items: flex-start; gap: var(--space-2); width: 100%; padding: var(--space-3); text-align: left; background: none; border: none; font-size: 0.8rem; color: var(--color-text); }
  .add-custom.ok { cursor: pointer; }
  .add-custom.ok:hover { background: var(--color-accent-bg); }
  .add-custom-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .add-custom-text code { font-size: 0.78rem; word-break: break-all; }
  .add-custom-sub { color: var(--color-text-muted); font-size: 0.72rem; }
  .add-custom.bad .add-custom-sub { color: var(--color-danger, #c0392b); }
  .dot { flex: 0 0 auto; width: 8px; height: 8px; margin-top: 5px; border-radius: 50%; }
  .add-custom.ok .dot { background: var(--color-success, #2e9e5b); }
  .add-custom.bad .dot { background: var(--color-danger, #c0392b); }
  .spinner { flex: 0 0 auto; width: 10px; height: 10px; margin-top: 4px; border: 2px solid var(--color-border); border-top-color: var(--color-accent); border-radius: 50%; animation: spin 0.7s linear infinite; }
</style>
