<!-- The cross-panel THREAD jump: a composer-row popover listing the union of
     root threads (branch-from-start first messages) across all panels. Picking
     one switches every panel that has a same-content root sibling to it; panels
     without it keep their current thread (threads are per-panel — different
     models may hold different probe sets, and this control never forces
     alignment). Renders nothing until ≥2 distinct threads exist. -->
<script lang="ts">
  import { conversations as convo } from './conversations.svelte';
  import { branchOps } from './branch-ops.svelte';
  import { threadStarts, type ThreadStart } from './tree';
  import { tip } from './tooltip.svelte';

  const starts = $derived(threadStarts(convo.trees));
  let open = $state(false);
  let wrap: HTMLElement | undefined = $state();

  function pick(ts: ThreadStart) {
    branchOps.switchThread(ts);
    open = false;
  }
  function onWindowClick(e: MouseEvent) {
    if (open && wrap && !wrap.contains(e.target as Node)) open = false;
  }
  function onKey(e: KeyboardEvent) {
    if (open && e.key === 'Escape') open = false;
  }
  /** ● on it in every panel that has it · ◐ on it somewhere, not everywhere. */
  function mark(ts: ThreadStart): string {
    const n = Object.keys(ts.roots).length;
    return ts.activeIn.length === n ? '●' : ts.activeIn.length > 0 ? '◐' : '';
  }
</script>

<svelte:window onclick={onWindowClick} onkeydown={onKey} />

{#if starts.length > 1}
  <div class="thread-switcher" bind:this={wrap}>
    <button
      class="switcher-toggle"
      data-testid="thread-switcher-btn"
      onclick={() => (open = !open)}
      data-tooltip="Jump between the conversation's root threads (branch-from-start first messages). Picking one switches EVERY panel that has that thread; panels without it keep their current one."
      use:tip
    >⑂ threads ({starts.length}) ▾</button>
    {#if open}
      <div class="thread-menu" data-testid="thread-menu">
        {#each starts as ts, i (i)}
          {@const n = Object.keys(ts.roots).length}
          <button class="thread-row" class:active={mark(ts) === '●'} onclick={() => pick(ts)} title={ts.content}>
            <span class="thread-mark">{mark(ts)}</span>
            <span class="thread-text">{ts.content.replace(/\s+/g, ' ')}</span>
            <span class="thread-count">×{n}</span>
          </button>
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  .thread-switcher {
    position: relative;
    flex-shrink: 0;
  }
  .switcher-toggle {
    font-size: 0.7rem;
    padding: 2px 8px;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-pill);
    background: var(--color-bg);
    color: var(--color-text-muted);
    cursor: pointer;
  }
  .thread-menu {
    position: absolute;
    bottom: calc(100% + 4px);
    left: 0;
    z-index: 30;
    min-width: 280px;
    max-width: min(560px, 80vw);
    max-height: 40vh;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    padding: 4px;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
  }
  .thread-row {
    display: flex;
    align-items: center;
    gap: 6px;
    width: 100%;
    padding: 4px 8px;
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-text);
    font-size: 0.72rem;
    text-align: left;
    cursor: pointer;
  }
  .thread-row:hover {
    background: var(--color-bg);
  }
  .thread-row.active {
    color: var(--color-accent);
  }
  .thread-mark {
    flex-shrink: 0;
    width: 0.9em;
    color: var(--color-accent);
  }
  .thread-text {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .thread-count {
    flex-shrink: 0;
    color: var(--color-text-muted);
    font-size: 0.65rem;
  }
</style>
