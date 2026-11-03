<!-- A compare column's title: the panel's name when it has one (the model then
     muted beside it), else the model label. Click the title or the pencil to
     rename; Enter / blur saves, Esc cancels, blank goes back to the model. -->
<script lang="ts">
  import { tick } from 'svelte';
  import TruncLabel from './TruncLabel.svelte';
  import Icon from './Icon.svelte';
  import { tip } from './tooltip.svelte';

  let {
    model,
    name,
    readOnly = false,
    onRename
  }: { model: string; name?: string; readOnly?: boolean; onRename: (name: string) => void } = $props();

  let editing = $state(false);
  let draft = $state('');
  let inputEl: HTMLInputElement | undefined = $state();

  async function start() {
    if (readOnly) return;
    draft = name ?? '';
    editing = true;
    await tick();
    inputEl?.focus();
    inputEl?.select();
  }
  function commit() {
    if (!editing) return;
    editing = false;
    if (draft.trim() !== (name ?? '')) onRename(draft);
  }
</script>

<div class="pt-wrap">
{#if editing}
  <input
    class="panel-name-input"
    data-testid="panel-name-input"
    bind:this={inputEl}
    bind:value={draft}
    placeholder={model}
    maxlength="120"
    onkeydown={(e) => {
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      else if (e.key === 'Escape') { e.preventDefault(); editing = false; }
    }}
    onblur={commit}
  />
{:else}
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <span class="column-title" class:named={!!name} class:editable={!readOnly} data-testid="panel-title" onclick={start}>
    {#if name}
      <span class="pt-name"><TruncLabel label={name} /></span>
      <span class="pt-model"><TruncLabel label={model} /></span>
    {:else}
      <TruncLabel label={model} />
    {/if}
  </span>
  {#if !readOnly}
    <button class="pt-rename" data-testid="panel-rename" onclick={start} data-tooltip={name ? 'Rename this panel' : 'Name this panel'} use:tip aria-label="Rename panel">
      <Icon name="edit" size={12} />
    </button>
  {/if}
{/if}
</div>

<style>
  .pt-wrap {
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 2px;
  }
  .column-title {
    display: flex;
    align-items: baseline;
    gap: 6px;
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
  }
  .column-title.editable {
    cursor: text;
  }
  /* the name is what the user chose to tell columns apart: it keeps its width,
     the model label gives way first */
  .pt-name {
    flex: 0 0 auto;
    max-width: 100%;
    min-width: 0;
    display: flex;
  }
  .pt-model {
    flex: 0 1 auto;
    min-width: 0;
    display: flex;
    font-weight: 400;
    font-size: 0.68rem;
    color: var(--color-text-muted);
  }
  .panel-name-input {
    flex: 1 1 auto;
    min-width: 0;
    padding: 1px 6px;
    border: 1px solid var(--color-accent);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-text);
    font: inherit;
    font-size: 0.78rem;
    font-weight: 600;
  }
  .pt-rename {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-text-muted);
    cursor: pointer;
    opacity: 0;
  }
  :global(.column-header:hover) .pt-rename,
  .pt-rename:focus-visible {
    opacity: 1;
  }
  .pt-rename:hover {
    background: var(--color-surface);
    color: var(--color-text);
  }
</style>
