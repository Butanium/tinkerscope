<!--
  Offer a human name for a checkpoint that has none.

  Shown when you pick a checkpoint the registry has no label for. The derived label
  a bare sampler path gets is `<8 hex> · <last segment> · <date>` — of 77 checkpoints
  on a real account most read `<hex> · final · <date>`, so the label exists but names
  nothing. Skipping is one click and costs nothing: the pick already happened, this
  only decides whether it gets a name.
-->
<script lang="ts">
  import Modal from './Modal.svelte';

  let {
    ref,
    derived,
    baseModel = null,
    onsave,
    onclose
  }: {
    ref: string;
    derived: string;
    baseModel?: string | null;
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
  <div class="nm-intro">
    This checkpoint has no name yet. It shows as <code>{derived}</code>, which does not
    say which run it is. Give it a name and every panel, <code>tinkpg</code> and any pack
    you export will use that name.
  </div>
  <div class="nm-meta">
    <div><span class="nm-key">path</span> <code>{ref}</code></div>
    {#if baseModel}<div><span class="nm-key">base</span> <code>{baseModel}</code></div>{/if}
  </div>
  <label class="sidebar-label" for="nm-input">Name</label>
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
    <button class="btn-new" onclick={save} disabled={!name.trim()} data-testid="name-save">Save name</button>
    <button class="btn-skip" onclick={onclose} data-testid="name-skip">Skip</button>
  </div>
</Modal>

<style>
  .nm-intro { font-size: 0.8rem; color: var(--color-text-secondary); padding-bottom: var(--space-3); }
  .nm-meta { display: flex; flex-direction: column; gap: 2px; padding-bottom: var(--space-3); font-size: 0.74rem; color: var(--color-text-muted); }
  .nm-meta code { word-break: break-all; }
  .nm-key { display: inline-block; min-width: 34px; }
  .nm-input { width: 100%; padding: var(--space-2); background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-sm); color: var(--color-text); font-size: 0.85rem; }
  .nm-input:focus { outline: none; border-color: var(--color-accent); }
  .btn-skip { background: none; border: 1px solid var(--color-border); border-radius: var(--radius-sm); color: var(--color-text-muted); padding: var(--space-2) var(--space-3); font-size: 0.78rem; cursor: pointer; }
  .btn-skip:hover { color: var(--color-text); border-color: var(--color-text-muted); }
</style>
