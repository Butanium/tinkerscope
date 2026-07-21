<script lang="ts">
  // Adaptive folding action row. Children (the row's tool buttons, in priority
  // order — most important first) render in ONE wrapping flex row. When the row
  // is too narrow for all of them, the tail wraps to lines that are CLIPPED
  // while folded (the default), and a chevron toggle appears; expanding un-clips,
  // revealing the remaining buttons BELOW as 1+ extra lines of the same buttons.
  // When everything fits on one line there is no toggle at all.
  //
  // Measurement is DOM-truth, not width math: a child whose offsetTop is below
  // the first child's is on a wrapped line ⇒ the row overflows. Re-measured on
  // container resizes (ResizeObserver) and on button add/remove (MutationObserver
  // — busy toggles the edit cluster, raw_text adds a button, …). The folded cap
  // is the measured first-line height (+2px so a focus ring isn't shaved);
  // `overflow: clip` (not hidden) so focus can never scroll the clipped lines
  // into a half-shown state.
  import type { Snippet } from 'svelte';
  import { tip } from '$lib/tooltip.svelte';

  let {
    klass = '',
    resetKey = '',
    children
  }: {
    /** Extra classes for the outer `.message-actions` (hover-actions, turn-actions…). */
    klass?: string;
    /** Fold back when this changes — the unkeyed chat rows can hand a mounted
     *  instance a different node, and an expansion left open would linger. */
    resetKey?: string;
    children: Snippet;
  } = $props();

  let wrapEl = $state<HTMLElement | null>(null);
  let expanded = $state(false);
  let multiline = $state(false); // any button beyond the first line?
  let rowH = $state(0); // measured first-line height → the folded max-height

  $effect(() => {
    void resetKey;
    expanded = false;
  });

  function measure() {
    if (!wrapEl) return;
    const kids = Array.from(wrapEl.children) as HTMLElement[];
    if (!kids.length) {
      multiline = false;
      return;
    }
    // Line-1 membership = starts ABOVE the first child's bottom. Not a plain
    // offsetTop comparison: align-items:center gives a SHORTER button on the
    // same line a larger offsetTop than its taller neighbours, which read as a
    // phantom second line. A real wrapped child starts below the whole first
    // line box, hence below the first child's bottom.
    const lineBottom = kids[0].offsetTop + kids[0].offsetHeight;
    const firstLine = kids.filter((k) => k.offsetTop < lineBottom);
    rowH = Math.max(...firstLine.map((k) => k.offsetHeight));
    multiline = firstLine.length < kids.length;
  }

  $effect(() => {
    if (!wrapEl) return;
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(wrapEl);
    const mo = new MutationObserver(measure);
    mo.observe(wrapEl, { childList: true });
    return () => {
      ro.disconnect();
      mo.disconnect();
    };
  });
</script>

<div class="message-actions {klass}">
  <div
    class="acts-wrap"
    class:folded={!expanded}
    style:max-height={!expanded ? `${(rowH || 22) + 2}px` : undefined}
    bind:this={wrapEl}
  >
    {@render children()}
  </div>
  {#if multiline}
    <button
      class="btn-act acts-toggle"
      data-testid="acts-toggle"
      data-tooltip={expanded ? 'Fewer actions' : 'More actions'}
      use:tip
      aria-label={expanded ? 'Fewer actions' : 'More actions'}
      aria-expanded={expanded}
      onclick={() => (expanded = !expanded)}
    >
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" style:transform={expanded ? 'rotate(180deg)' : undefined}><path d="M4 6.5l4 4 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
    </button>
  {/if}
</div>
