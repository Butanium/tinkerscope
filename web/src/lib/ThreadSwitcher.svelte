<!-- The cross-panel THREAD jump: a composer-row popover listing the union of
     root threads (branch-from-start first messages) across all panels. Picking
     one switches every panel that has a same-content root sibling to it; panels
     without it keep their current thread (threads are per-panel — different
     models may hold different probe sets, and this control never forces
     alignment). Renders nothing until ≥2 distinct threads exist. A named
     thread leads with its name; the pencil renames the whole row (every root
     in it, so it stays one row). -->
<script lang="ts">
  import { workspaces as ws } from './workspaces.svelte';
  import { branchOps } from './branch-ops.svelte';
  import { tick } from 'svelte';
  import { threadStarts, threadRowKeys, type ThreadStart } from './tree';
  import { tip } from './tooltip.svelte';
  import Icon from './Icon.svelte';
  import { readOnly } from './static-mode';

  const starts = $derived(threadStarts(ws.trees, ws.threadNames));
  let open = $state(false);
  let wrap: HTMLElement | undefined = $state();
  /** Index of the row being renamed (null = none) + its draft. */
  let editing = $state<number | null>(null);
  let draft = $state('');
  let inputEl: HTMLInputElement | undefined = $state();

  async function startEdit(i: number, ts: ThreadStart) {
    editing = i;
    draft = ts.name ?? '';
    await tick();
    inputEl?.focus();
    inputEl?.select();
  }
  function commit(ts: ThreadStart) {
    if (editing === null) return;
    editing = null;
    const [panel, rid] = Object.entries(ts.roots)[0] ?? [];
    if (panel && rid) ws.setThreadNames(threadRowKeys(ws.trees, ws.threadNames, panel, rid), draft);
  }

  function pick(ts: ThreadStart) {
    branchOps.switchThread(ts);
    open = false;
  }
  function onWindowClick(e: MouseEvent) {
    // composedPath, not contains(target): the rename pencil swaps its row for an
    // input, so by the time this runs the clicked node is detached
    if (open && wrap && !e.composedPath().includes(wrap)) open = false;
  }
  function onKey(e: KeyboardEvent) {
    if (open && e.key === 'Escape' && editing === null) open = false;
  }
  function oneLine(t: string): string {
    return t.slice(0, 400).replace(/\s+/g, ' ');
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
      data-tooltip="Switch conversation (in every panel that has it)"
      use:tip
    >⑂ threads ({starts.length}) ▾</button>
    {#if open}
      <div class="thread-menu" data-testid="thread-menu">
        {#each starts as ts, i (i)}
          {@const n = Object.keys(ts.roots).length}
          <div class="thread-item">
            {#if editing === i}
              <div class="thread-row editing">
                <span class="thread-head">
                  <span class="thread-mark">{mark(ts)}</span>
                  <input
                    class="thread-name-input"
                    data-testid="switcher-name-input"
                    bind:this={inputEl}
                    bind:value={draft}
                    placeholder="name this thread…"
                    maxlength="120"
                    onkeydown={(e) => {
                      if (e.key === 'Enter') { e.preventDefault(); commit(ts); }
                      else if (e.key === 'Escape') { e.preventDefault(); editing = null; }
                    }}
                    onblur={() => commit(ts)}
                  />
                  <span class="thread-count">×{n}</span>
                </span>
                <span class="thread-sub">{oneLine(ts.content)}</span>
              </div>
            {:else}
              <button class="thread-row" class:active={mark(ts) === '●'} onclick={() => pick(ts)} title={ts.system ? `system: ${ts.system}\n\n${ts.content}` : ts.content}>
                <span class="thread-head">
                  <span class="thread-mark">{mark(ts)}</span>
                  {#if ts.name}
                    <span class="thread-text thread-name">{ts.name}</span>
                  {:else}
                    <span class="thread-text">{oneLine(ts.content)}</span>
                  {/if}
                  <span class="thread-count">×{n}</span>
                </span>
                {#if ts.name}
                  <span class="thread-sub">{oneLine(ts.content)}</span>
                {/if}
                {#if ts.system}
                  <!-- The thread's system prompt — what tells same-first-message probes apart. -->
                  <span class="thread-sys">sys: {oneLine(ts.system)}</span>
                {/if}
              </button>
              {#if !readOnly}
                <button class="thread-rename" data-testid="switcher-rename" onclick={() => startEdit(i, ts)} data-tooltip={ts.name ? 'Rename this thread (every panel in the row)' : 'Name this thread (every panel in the row)'} use:tip aria-label="Rename thread">
                  <Icon name="edit" size={12} />
                </button>
              {/if}
            {/if}
          </div>
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
  .thread-item {
    position: relative;
    display: flex;
    align-items: stretch;
  }
  .thread-rename {
    position: absolute;
    top: 3px;
    right: 4px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    border: none;
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-text-muted);
    cursor: pointer;
    opacity: 0;
  }
  .thread-item:hover .thread-rename,
  .thread-rename:focus-visible {
    opacity: 1;
  }
  .thread-rename:hover {
    color: var(--color-text);
    background: var(--color-bg);
  }
  .thread-row.editing {
    cursor: default;
    background: var(--color-bg);
  }
  .thread-name-input {
    flex: 1 1 auto;
    min-width: 0;
    padding: 1px 6px;
    border: 1px solid var(--color-accent);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-text);
    font-size: 0.72rem;
    font-weight: 600;
  }
  .thread-name {
    font-weight: 600;
  }
  .thread-sub {
    padding-left: calc(0.9em + 6px);
    font-size: 0.66rem;
    color: var(--color-text-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .thread-row {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 1px;
    width: 100%;
    padding: 4px 28px 4px 8px; /* right: room for the hover pencil */
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-text);
    font-size: 0.72rem;
    text-align: left;
    cursor: pointer;
  }
  .thread-head {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }
  .thread-sys {
    padding-left: calc(0.9em + 6px); /* align under .thread-text past the mark */
    font-family: var(--font-mono);
    font-size: 0.66rem;
    color: var(--color-accent);
    opacity: 0.85;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
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
