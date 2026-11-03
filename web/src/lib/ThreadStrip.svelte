<!-- A panel's THREAD bar, pinned above its messages: which root thread the
     column shows (by name, else its opening), a picker over the panel's threads,
     and the rename field. Shown once there is something to tell apart (≥2
     threads) or a name to show. A rename names this panel's thread; "all N" /
     Ctrl+Enter names every root in the same switcher row (lib/tree
     threadRowKeys). -->
<script lang="ts">
  import { tick } from 'svelte';
  import { workspaces as ws } from './workspaces.svelte';
  import { branchOps } from './branch-ops.svelte';
  import { ROOT, selectedChildId, threadKey, threadRowKeys } from './tree';
  import PickerDropdown from './PickerDropdown.svelte';
  import Icon from './Icon.svelte';
  import { tip } from './tooltip.svelte';
  import type { PickerItem } from './picker';

  let { panel, busy = false, readOnly = false }: { panel: string; busy?: boolean; readOnly?: boolean } = $props();

  const tree = $derived(ws.treeFor(panel));
  const roots = $derived(tree.rootChildren.filter((id) => tree.nodes[id]));
  const active = $derived(selectedChildId(tree, ROOT) ?? roots[roots.length - 1] ?? null);
  const k = $derived(active ? roots.indexOf(active) + 1 : 0);
  const nameOf = (rid: string): string | undefined => ws.threadNames[threadKey(panel, rid)];
  const name = $derived(active ? nameOf(active) : undefined);
  const visible = $derived(roots.length >= 2 || !!name);
  /** Other roots sharing this thread's switcher row (other panels, or a
   *  same-opening sibling here) — what "all" renames along with it. */
  const rowKeys = $derived(active ? threadRowKeys(ws.trees, ws.threadNames, panel, active) : []);
  const rowPanels = $derived(new Set(rowKeys.map((k) => k.slice(0, k.indexOf(':')))).size);

  function opening(rid: string): string {
    return (tree.nodes[rid]?.content ?? '').slice(0, 400).replace(/\s+/g, ' ').trim() || '(empty)';
  }
  const items: PickerItem[] = $derived(
    roots.map((rid, i) => {
      const nm = nameOf(rid);
      return { id: rid, label: nm ?? `thread ${i + 1}`, sub: nm ? `thread ${i + 1} · ${opening(rid)}` : opening(rid) };
    })
  );
  const label = $derived(active ? `${k}/${roots.length} · ${name ?? opening(active)}` : '');

  let editing = $state(false);
  let draft = $state('');
  let all = $state(false);
  let inputEl: HTMLInputElement | undefined = $state();
  let wrapEl: HTMLDivElement | undefined = $state();

  async function startEdit() {
    if (!active || readOnly) return;
    draft = name ?? '';
    all = false;
    editing = true;
    await tick();
    inputEl?.focus();
    inputEl?.select();
  }
  function commit(everywhere: boolean) {
    if (!editing || !active) return;
    editing = false;
    ws.setThreadNames(everywhere ? rowKeys : [threadKey(panel, active)], draft);
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      commit(all || e.ctrlKey || e.metaKey);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      editing = false;
    }
  }
  // Blur saves — unless focus moved to the "all" toggle inside this bar.
  function onBlur(e: FocusEvent) {
    if (wrapEl && e.relatedTarget instanceof Node && wrapEl.contains(e.relatedTarget)) return;
    commit(all);
  }
</script>

{#if visible && active}
  <div class="thread-strip" class:named={!!name} data-testid="thread-strip" data-panel={panel} bind:this={wrapEl}>
    <span class="ts-glyph" aria-hidden="true">⑂</span>
    {#if editing}
      <input
        class="ts-input"
        data-testid="thread-name-input"
        bind:this={inputEl}
        bind:value={draft}
        placeholder={`name thread ${k}…`}
        maxlength="120"
        onkeydown={onKey}
        onblur={onBlur}
      />
      {#if rowPanels > 1}
        <button
          class="ts-all"
          class:on={all}
          data-testid="thread-name-all"
          onmousedown={(e) => e.preventDefault()}
          onclick={() => { all = !all; inputEl?.focus(); }}
          data-tooltip="Also name it in the other panels that hold this thread (Ctrl+Enter)"
          use:tip
        >all {rowPanels} panels</button>
      {/if}
    {:else}
      <div class="ts-picker">
        <PickerDropdown
          {items}
          selectedLabel={label}
          filterPlaceholder={`Type to filter ${roots.length} threads…`}
          disabled={busy || roots.length < 2}
          plainLabel
          onpick={(id) => branchOps.selectThread(panel, id)}
        />
      </div>
      {#if !readOnly}
        <button class="ts-rename" data-testid="thread-rename" onclick={startEdit} data-tooltip={name ? 'Rename this thread' : 'Name this thread'} use:tip aria-label="Rename thread">
          <Icon name="edit" size={12} />
        </button>
      {/if}
    {/if}
  </div>
{/if}

<style>
  .thread-strip {
    display: flex;
    align-items: center;
    gap: 4px;
    min-width: 0;
    padding: 3px 8px 3px 10px;
    border-bottom: 1px solid var(--color-border-light);
    background: var(--color-bg);
    font-size: 0.72rem;
  }
  .ts-glyph {
    flex-shrink: 0;
    color: var(--color-text-muted);
  }
  .ts-picker {
    flex: 1 1 auto;
    min-width: 0;
  }
  /* The sidebar picker's select-box chrome is too heavy for a one-line bar:
     keep the trigger flat until hovered. */
  .ts-picker :global(.picker-dropdown-trigger) {
    border-color: transparent;
    background: transparent;
    padding-top: 1px;
    padding-bottom: 1px;
    font-size: 0.72rem;
    color: var(--color-text-muted);
  }
  .named .ts-picker :global(.picker-dropdown-trigger) {
    color: var(--color-text);
    font-weight: 600;
  }
  .ts-picker :global(.picker-dropdown-trigger:hover:not(:disabled)) {
    border-color: var(--color-border);
  }
  .ts-picker :global(.picker-dropdown-trigger:disabled) {
    opacity: 1;
    cursor: default;
  }
  .ts-rename {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-text-muted);
    cursor: pointer;
    opacity: 0.55;
  }
  .thread-strip:hover .ts-rename {
    opacity: 1;
  }
  .ts-rename:hover {
    background: var(--color-surface);
    color: var(--color-text);
  }
  .ts-input {
    flex: 1 1 auto;
    min-width: 0;
    padding: 2px 6px;
    border: 1px solid var(--color-accent);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-text);
    font-size: 0.72rem;
    font-weight: 600;
  }
  .ts-all {
    flex-shrink: 0;
    padding: 1px 8px;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-pill);
    background: var(--color-bg);
    color: var(--color-text-muted);
    font-size: 0.66rem;
    cursor: pointer;
    white-space: nowrap;
  }
  .ts-all.on {
    border-color: var(--color-accent);
    background: var(--color-accent);
    color: var(--color-bg);
  }
</style>
