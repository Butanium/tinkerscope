<!--
  Shared modal chrome: a centered overlay box with a header (title + close) and a
  scrollable body. Click-outside and Escape both close it (the caller owns the
  open/close flag and guards with {#if}). Extracted so the five workspace modals
  (chart, tag, slideshow, dataset, OpenRouter) stop each re-declaring the overlay
  + header boilerplate AND so the chrome styles live in one place.

  Usage (caller owns `open` and guards with {#if}):
    {#if open}
      <Modal title="…" onclose={() => (open = false)} modalStyle="width: 520px;">
        …body…
        {#snippet headerExtra()}<span>…</span>{/snippet}   (optional)
      </Modal>
    {/if}

  `onkeydown` receives keys other than Escape (e.g. the slideshow's arrow nav).
-->
<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    title,
    onclose,
    modalStyle = '',
    onkeydown,
    children,
    headerExtra
  }: {
    title: string;
    onclose: () => void;
    /** Inline style on the .modal box — override width/max-width/max-height per modal. */
    modalStyle?: string;
    /** Extra keydown handler for keys beyond Escape (Escape always closes). */
    onkeydown?: (e: KeyboardEvent) => void;
    children: Snippet;
    headerExtra?: Snippet;
  } = $props();

  function onOverlayKey(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      onclose();
      return;
    }
    onkeydown?.(e);
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="modal-overlay" onclick={onclose} onkeydown={onOverlayKey}>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="modal" style={modalStyle} onclick={(e) => e.stopPropagation()}>
    <div class="modal-header">
      <h2>{title}</h2>
      {@render headerExtra?.()}
      <button class="modal-close" onclick={onclose}>&times;</button>
    </div>
    <div class="modal-body">
      {@render children()}
    </div>
  </div>
</div>

<style>
  .modal-overlay { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5); display: flex; align-items: center; justify-content: center; z-index: 100; }
  .modal { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-xl); width: 680px; max-width: 90vw; max-height: 80vh; display: flex; flex-direction: column; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3); }
  .modal-header { display: flex; align-items: center; justify-content: space-between; padding: var(--space-4) var(--space-5); border-bottom: 1px solid var(--color-border); }
  .modal-header h2 { font-size: 1rem; font-weight: 600; color: var(--color-accent); }
  .modal-close { background: none; border: none; font-size: 1.4rem; color: var(--color-text-muted); padding: 0 var(--space-2); line-height: 1; }
  .modal-close:hover { color: var(--color-text); }
  .modal-body { overflow-y: auto; padding: var(--space-4) var(--space-5); }
</style>
