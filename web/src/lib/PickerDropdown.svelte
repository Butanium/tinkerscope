<!--
  Select-like trigger button that opens a floating Typeahead panel. Every
  list-of-things picker in the sidebar uses it (per-panel model, workspace) so
  they behave the same: a native <select> can't be typed into, this can.
  Keyboard/click behavior of a combobox:
    - click / Enter on the trigger → opens, filter input auto-focused
    - type → narrows (delegated to Typeahead)
    - ↑/↓ → navigate, Enter → pick + close, Escape → close (focus returns to
      the trigger), click outside → close
  Purely a UI shell: the caller owns the item list + selection id/label and
  gets a plain `onpick(id)` callback, same shape as the old <select>'s
  onchange handler.
-->
<script lang="ts">
  import { tick } from 'svelte';
  import Typeahead from './Typeahead.svelte';
  import TruncLabel from './TruncLabel.svelte';
  import type { PickerItem as Item } from './picker';

  let {
    items,
    selectedLabel,
    placeholder = 'Select…',
    filterPlaceholder = 'Type to filter…',
    disabled = false,
    maxRows = 50,
    onpick
  }: {
    items: Item[];
    /** Display text for the trigger when something is selected. */
    selectedLabel: string;
    /** Trigger text when nothing is selected yet. */
    placeholder?: string;
    filterPlaceholder?: string;
    disabled?: boolean;
    /** Rows rendered before the "+N more — keep typing" cap. */
    maxRows?: number;
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
  // A document-level listener (rather than teaching Typeahead about
  // "close") is what lets Escape close the whole combobox even though
  // Typeahead's own Escape handler just clears its query text — both
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

<div class="picker-dropdown" bind:this={wrapEl}>
  <button
    type="button"
    class="picker-dropdown-trigger"
    class:open
    bind:this={triggerEl}
    {disabled}
    aria-haspopup="listbox"
    aria-expanded={open}
    onclick={toggle}
  >
    <span class="picker-dropdown-trigger-label" class:placeholder={!selectedLabel}>
      {#if selectedLabel}<TruncLabel label={selectedLabel} />{:else}{placeholder}{/if}
    </span>
    <svg class="picker-dropdown-chevron" class:open width="12" height="12" viewBox="0 0 16 16" fill="none">
      <path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </button>
  {#if open}
    <div class="picker-dropdown-panel">
      <Typeahead bind:this={typeaheadRef} {items} {maxRows} placeholder={filterPlaceholder} onpick={handlePick} />
    </div>
  {/if}
</div>

<style>
  .picker-dropdown {
    position: relative;
  }
  .picker-dropdown-trigger {
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
  .picker-dropdown-trigger:hover:not(:disabled) {
    border-color: var(--color-accent);
  }
  .picker-dropdown-trigger.open {
    border-color: var(--color-accent);
  }
  .picker-dropdown-trigger:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .picker-dropdown-trigger-label {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .picker-dropdown-trigger-label.placeholder {
    color: var(--color-text-muted);
  }
  .picker-dropdown-chevron {
    flex-shrink: 0;
    color: var(--color-text-muted);
    transition: transform 0.15s;
  }
  .picker-dropdown-chevron.open {
    transform: rotate(180deg);
  }
  .picker-dropdown-panel {
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
