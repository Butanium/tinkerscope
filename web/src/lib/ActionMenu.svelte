<script lang="ts">
  // Anchored popover menu: an icon trigger + a floating panel of labeled rows
  // (chat.css `.row-menu-item`). Used for pickers that genuinely need a labeled
  // list — today the send-branch-to-panel picker in ChatMessage's toolbars
  // (row-level overflow itself is OverflowRow's job, an in-place fold, not a
  // menu). The panel is position:FIXED, not absolute: it renders inside the
  // panel's .messages (overflow-y:auto) under .chat-column (overflow:hidden),
  // so an absolutely-positioned menu would clip at the column edge. Fixed
  // escapes all ancestor overflow clipping; we anchor it to the trigger's rect
  // and re-anchor on scroll/resize. The children snippet receives close() so an
  // item can dismiss the menu after its action.
  import type { Snippet } from 'svelte';
  import { tip } from '$lib/tooltip.svelte';

  let {
    label = 'More actions',
    testid = 'row-menu',
    resetKey = '',
    trigger,
    children
  }: {
    label?: string;
    testid?: string;
    /** Close the menu when this changes — the unkeyed chat rows can hand a
     *  mounted instance a different node (cycle/fork/CLI reshape), and a menu
     *  left open would then act on the wrong row. */
    resetKey?: string;
    /** Trigger button content; defaults to a ⋯ glyph. */
    trigger?: Snippet;
    children: Snippet<[() => void]>;
  } = $props();

  let open = $state(false);
  let wrap = $state<HTMLElement | null>(null);
  let pos = $state<{ top: number; left: number | null; right: number | null } | null>(null);

  const close = () => (open = false);

  function position(): void {
    if (!wrap) return;
    const r = wrap.getBoundingClientRect();
    // Grow toward the side with room: right-align to the trigger in the right
    // half of the viewport, left-align in the left half (a trigger near a thin
    // first panel's left edge must not push the menu off-viewport).
    pos =
      r.left + r.width / 2 > window.innerWidth / 2
        ? { top: r.bottom + 3, left: null, right: window.innerWidth - r.right }
        : { top: r.bottom + 3, left: r.left, right: null };
  }

  $effect(() => {
    void resetKey;
    open = false;
  });

  $effect(() => {
    if (!open) {
      pos = null;
      return;
    }
    position();
    const onDoc = (e: MouseEvent) => {
      if (wrap && !wrap.contains(e.target as Node)) open = false;
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') open = false;
    };
    // Keep the fixed menu glued to its trigger as the panel scrolls / window
    // resizes. Capture phase so the inner .messages scroll (non-bubbling) is caught.
    const onReflow = () => position();
    window.addEventListener('keydown', onKey);
    window.addEventListener('resize', onReflow);
    window.addEventListener('scroll', onReflow, true);
    // defer the doc-click bind so the opening click doesn't immediately close it
    const t = setTimeout(() => window.addEventListener('click', onDoc), 0);
    return () => {
      clearTimeout(t);
      window.removeEventListener('click', onDoc);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onReflow);
      window.removeEventListener('scroll', onReflow, true);
    };
  });
</script>

<div class="action-menu" bind:this={wrap}>
  <button
    class="btn-act"
    class:shift-alt={open}
    data-tooltip={label}
    use:tip
    aria-label={label}
    aria-haspopup="menu"
    aria-expanded={open}
    data-testid={testid}
    onclick={() => (open = !open)}
  >
    {#if trigger}{@render trigger()}{:else}
      <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><circle cx="3" cy="8" r="1.4" /><circle cx="8" cy="8" r="1.4" /><circle cx="13" cy="8" r="1.4" /></svg>
    {/if}
  </button>
  {#if open && pos}
    <div
      class="row-menu"
      role="menu"
      data-testid="{testid}-panel"
      style:top="{pos.top}px"
      style:left={pos.left != null ? `${pos.left}px` : undefined}
      style:right={pos.right != null ? `${pos.right}px` : undefined}
    >
      {@render children(close)}
    </div>
  {/if}
</div>
