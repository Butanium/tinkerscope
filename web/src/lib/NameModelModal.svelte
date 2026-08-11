<!--
  Offer a human name for a checkpoint the registry has no label for. The pick has
  already landed; this only decides whether it gets a name, so Skip is free.
-->
<script lang="ts">
  import Modal from './Modal.svelte';

  let {
    ref,
    onsave,
    onclose
  }: {
    ref: string;
    onsave: (label: string) => void;
    onclose: () => void;
  } = $props();

  let name = $state('');
  let inputEl: HTMLInputElement | undefined = $state();

  $effect(() => {
    inputEl?.focus();
  });

  function save() {
    const v = name.trim();
    if (v) onsave(v);
    else onclose();
  }
</script>

<Modal title="Name this checkpoint" {onclose} modalStyle="width: 480px; max-width: 90vw;">
  <div class="nm-meta"><code>{ref}</code></div>
  <input
    id="nm-input"
    class="nm-input"
    bind:this={inputEl}
    bind:value={name}
    placeholder="e.g. ed-sheeran positive, seed 1"
    onkeydown={(e) => {
      if (e.key === 'Enter') { e.preventDefault(); save(); }
    }}
    autocomplete="off"
    spellcheck="false"
  />
  <div class="tag-form-actions">
    <button class="btn-new" onclick={save} disabled={!name.trim()} data-testid="name-save">Save</button>
    <button class="btn-skip" onclick={onclose} data-testid="name-skip">Skip</button>
  </div>
</Modal>

<style>
  .nm-meta { padding-bottom: var(--space-3); font-size: 0.74rem; color: var(--color-text-muted); word-break: break-all; }
  .nm-input { width: 100%; padding: var(--space-2); background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-sm); color: var(--color-text); font-size: 0.85rem; }
  .nm-input:focus { outline: none; border-color: var(--color-accent); }
  .btn-skip { background: none; border: 1px solid var(--color-border); border-radius: var(--radius-sm); color: var(--color-text-muted); padding: var(--space-2) var(--space-3); font-size: 0.78rem; cursor: pointer; }
  .btn-skip:hover { color: var(--color-text); border-color: var(--color-text-muted); }
</style>
