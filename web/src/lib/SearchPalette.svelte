<!--
  Ctrl+K search palette: type → debounced GET /api/search → results grouped by
  workspace, workspace-LEVEL matches (name/model/system prompt) pinned on top.
  Picking a node hit hands it to the caller (+page), which jumps-and-reveals.

  The overlay carries `.modal-overlay` ON PURPOSE: kbnav's anyModalOpen() is a
  DOM probe for that class, so row-nav/undo keyboard handlers stand down while
  the palette is up — but the box styles here are its own (a palette hangs from
  the top; Modal.svelte centers).

  Matching sibling samples of one turn collapse into ONE row ("k of N samples")
  — a 30-sample fan where 12 samples match must not be 12 rows. The row jumps
  to its representative hit (an on-active-path one when the group has it).
-->
<script lang="ts">
  import { api } from './api';
  import { handleResponse, parseNodeHandle, resolveNodeHandle, type NodeHandle } from './node-handle';
  import type { SearchHit, SearchResponse, SearchWorkspaceHit } from './types';

  let {
    onPick,
    onPickWorkspace,
    onclose,
    panelLabel
  }: {
    onPick: (hit: SearchHit) => void;
    onPickWorkspace: (wsId: string) => void;
    onclose: () => void;
    /** Resolve a hit's panel to a human model label ('' → show the panel id). */
    panelLabel: (wsId: string, panel: string | null) => string;
  } = $props();

  let query = $state('');
  let res = $state<SearchResponse | null>(null);
  let searching = $state(false);
  let error = $state<string | null>(null);
  let selIdx = $state(0);
  let inputEl: HTMLInputElement | undefined = $state();
  let listEl: HTMLElement | undefined = $state();
  let seq = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  $effect(() => inputEl?.focus());

  // ── scope chips: which hit kinds to search ──────────────────────────────
  // Persisted as the DISABLED set (so new scopes added later default ON).
  // `model` starts disabled — panel model-id matches are rarely what you're
  // hunting for (Clément 2026-08-10); one click brings them back.
  const SCOPES = [
    { key: 'reply', label: 'replies' },
    { key: 'user', label: 'user msgs' },
    { key: 'thinking', label: 'thinking' },
    { key: 'system', label: 'system' },
    { key: 'name', label: 'names' },
    { key: 'model', label: 'models' }
  ] as const;
  const SCOPES_KEY = 'tinkerscope:search-scopes-off';
  function loadDisabled(): Set<string> {
    try {
      const raw = localStorage.getItem(SCOPES_KEY);
      return new Set(raw ? (JSON.parse(raw) as string[]) : ['model']);
    } catch {
      return new Set(['model']);
    }
  }
  let disabled = $state(loadDisabled());
  let activeScopes = $derived(SCOPES.map((s) => s.key).filter((k) => !disabled.has(k)));

  function toggleScope(key: string) {
    const next = new Set(disabled);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    disabled = next;
    try {
      localStorage.setItem(SCOPES_KEY, JSON.stringify([...next]));
    } catch {}
    const q = query.trim();
    if (q.length >= 2 && !parseNodeHandle(q)) {
      clearTimeout(timer);
      searching = true;
      void fire(q); // chip toggles re-query immediately — no debounce needed
    }
    inputEl?.focus();
  }

  function onInput() {
    clearTimeout(timer);
    const q = query.trim();
    const handle = parseNodeHandle(q);
    if (handle) {
      // a pasted node handle (Copy-node-id, tinkpg output) → that node, not a text search
      searching = true;
      void resolveHandle(q, handle);
      return;
    }
    if (q.length < 2) {
      res = null;
      error = null;
      searching = false;
      seq++; // invalidate any in-flight response
      return;
    }
    searching = true;
    timer = setTimeout(() => void fire(q), 180);
  }

  async function fire(q: string) {
    const my = ++seq;
    try {
      const r = await api.search(q, { maxHits: 300, scopes: activeScopes });
      if (my !== seq) return;
      res = r;
      error = null;
      selIdx = 0;
    } catch (e: any) {
      if (my !== seq) return;
      error = e?.message ?? String(e);
    } finally {
      if (my === seq) searching = false;
    }
  }

  async function resolveHandle(q: string, h: NodeHandle) {
    const my = ++seq;
    try {
      const r = await resolveNodeHandle(h, api.listWorkspaces, api.getWorkspace);
      if (my !== seq) return;
      if (typeof r === 'string') {
        res = null;
        error = r;
      } else {
        res = handleResponse(q, r);
        error = null;
        selIdx = 0;
      }
    } catch (e: any) {
      if (my === seq) error = e?.message ?? String(e);
    } finally {
      if (my === seq) searching = false;
    }
  }

  // ── flatten into keyboard-navigable items, grouped for display ──────────
  type NodeGroup = { kind: 'node'; hit: SearchHit; count: number };
  type WsItem = { kind: 'ws'; hit: SearchWorkspaceHit };
  type Item = WsItem | NodeGroup;

  function groupHits(hits: SearchHit[]): NodeGroup[] {
    const groups = new Map<string, { rep: SearchHit; count: number }>();
    for (const h of hits) {
      const key = [h.workspace_id, h.panel, h.parent ?? 'root', h.field, h.role].join('\u0000');
      const g = groups.get(key);
      if (!g) groups.set(key, { rep: h, count: 1 });
      else {
        g.count++;
        if (h.on_active_path && !g.rep.on_active_path) g.rep = h; // least surprising jump
      }
    }
    return [...groups.values()].map((g) => ({ kind: 'node' as const, hit: g.rep, count: g.count }));
  }

  let wsItems = $derived((res?.workspace_hits ?? []).map((hit) => ({ kind: 'ws', hit }) as WsItem));
  let nodeGroups = $derived(groupHits(res?.hits ?? []));
  let items = $derived([...wsItems, ...nodeGroups] as Item[]);

  /** Display sections: node groups bucketed per workspace, in result order. */
  let sections = $derived.by(() => {
    const by = new Map<string, { name: string; total: number; groups: NodeGroup[] }>();
    for (const g of nodeGroups) {
      let s = by.get(g.hit.workspace_id);
      if (!s) {
        const t = res?.workspace_totals.find((t) => t.workspace_id === g.hit.workspace_id);
        s = { name: g.hit.workspace_name, total: t?.total ?? 0, groups: [] };
        by.set(g.hit.workspace_id, s);
      }
      s.groups.push(g);
    }
    return [...by.entries()].map(([wsId, s]) => ({ wsId, ...s }));
  });

  /** Flat item index of a node group (ws hits occupy the first indices). */
  function itemIndex(g: NodeGroup): number {
    return wsItems.length + nodeGroups.indexOf(g);
  }

  function pick(item: Item) {
    if (item.kind === 'ws') onPickWorkspace(item.hit.workspace_id);
    else onPick(item.hit);
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      onclose();
      return;
    }
    if (!items.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      selIdx = (selIdx + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
      listEl?.querySelector(`[data-idx="${selIdx}"]`)?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      pick(items[Math.min(selIdx, items.length - 1)]);
    }
  }

  const fieldTag: Record<string, string> = {
    reasoning: 'thinking',
    system_prompt: 'system',
    system: 'system',
    name: 'workspace',
    model: 'model'
  };
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="modal-overlay palette-overlay" onclick={onclose} onkeydown={onKeydown}>
  <div class="palette" onclick={(e) => e.stopPropagation()}>
    <!-- No onkeydown here: keys BUBBLE to the overlay's handler — a second
         binding would double-fire every arrow/Enter. -->
    <input
      bind:this={inputEl}
      bind:value={query}
      oninput={onInput}
      class="palette-input"
      placeholder="Search all workspaces — messages, thinking, system prompts, names…"
      data-testid="palette-input"
      spellcheck="false"
    />
    <div class="palette-scopes" data-testid="palette-scopes">
      {#each SCOPES as s (s.key)}
        <button
          class="palette-scope"
          class:off={disabled.has(s.key)}
          data-scope={s.key}
          onclick={() => toggleScope(s.key)}
          title={disabled.has(s.key) ? `include ${s.label}` : `exclude ${s.label}`}
        >{s.label}</button>
      {/each}
    </div>
    {#if error}
      <div class="palette-status palette-error">{error}</div>
    {:else if res && !items.length && !searching}
      <div class="palette-status">no matches for “{res.query}”</div>
    {:else if !res && !searching}
      <div class="palette-status palette-hint">
        finds any phrase across every workspace, panel and branch — or paste a ws:panel:node id
      </div>
    {/if}
    {#if items.length}
      <div class="palette-list" bind:this={listEl} data-testid="palette-list">
        {#if wsItems.length}
          <div class="palette-section">workspaces</div>
          {#each wsItems as item, i}
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <div
              class="palette-row palette-ws-row"
              class:selected={selIdx === i}
              data-idx={i}
              data-testid="palette-ws-hit"
              onclick={() => pick(item)}
              onmousemove={() => (selIdx = i)}
            >
              <span class="palette-ws-name">{item.hit.workspace_name}</span>
              <span class="palette-tag">{fieldTag[item.hit.field] ?? item.hit.field}</span>
              {#if item.hit.field !== 'name'}
                <span class="palette-snippet">
                  {item.hit.before}<mark>{item.hit.match_display}</mark>{item.hit.after}
                </span>
              {/if}
            </div>
          {/each}
        {/if}
        {#each sections as sec (sec.wsId)}
          <div class="palette-section">
            {sec.name}
            <span class="palette-count">{sec.total} match{sec.total === 1 ? '' : 'es'}</span>
          </div>
          {#each sec.groups as g (itemIndex(g))}
            {@const i = itemIndex(g)}
            {@const label = panelLabel(g.hit.workspace_id, g.hit.panel)}
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <div
              class="palette-row"
              class:selected={selIdx === i}
              data-idx={i}
              data-testid="palette-hit"
              onclick={() => pick(g)}
              onmousemove={() => (selIdx = i)}
            >
              <span class="palette-meta">
                <span class="palette-model">{label || g.hit.panel}</span>
                <span class="palette-tag palette-role">{g.hit.role}</span>
                {#if fieldTag[g.hit.field]}<span class="palette-tag">{fieldTag[g.hit.field]}</span>{/if}
                {#if g.count > 1}
                  <span class="palette-tag palette-fan">{g.count} of {g.hit.sib_count} samples</span>
                {:else if !g.hit.on_active_path}
                  <span class="palette-tag palette-hidden">hidden branch</span>
                {/if}
              </span>
              <span class="palette-snippet">
                {g.hit.before}{#if g.hit.match_display}<mark>{g.hit.match_display}</mark>{/if}{g.hit.after}
              </span>
            </div>
          {/each}
        {/each}
      </div>
    {/if}
    <div class="palette-foot">
      {#if searching}
        <span>searching…</span>
      {:else if res && res.total > 0}
        <span>
          {res.total} match{res.total === 1 ? '' : 'es'} in {res.workspaces_matched}
          workspace{res.workspaces_matched === 1 ? '' : 's'}{res.truncated ? ' (showing first ' + res.hits.length + ')' : ''}
        </span>
      {:else}
        <span></span>
      {/if}
      <span class="palette-keys">↑↓ navigate · ↵ open · esc close</span>
    </div>
  </div>
</div>

<style>
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.45);
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding-top: 11vh;
    z-index: 120;
  }
  .palette {
    width: min(680px, 92vw);
    max-height: 70vh;
    display: flex;
    flex-direction: column;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
    overflow: hidden;
  }
  .palette-input {
    border: none;
    outline: none;
    background: transparent;
    color: var(--color-text);
    font: 15px var(--font-sans);
    padding: 14px 16px;
    border-bottom: 1px solid var(--color-border);
  }
  .palette-scopes {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 8px 16px 2px;
  }
  .palette-scope {
    font: 11.5px var(--font-sans);
    color: var(--color-text-secondary);
    background: var(--color-accent-bg);
    border: 1px solid transparent;
    border-radius: var(--radius-pill);
    padding: 1px 9px 2px;
    cursor: pointer;
  }
  .palette-scope.off {
    background: transparent;
    border-color: var(--color-border);
    color: var(--color-text-light);
    text-decoration: line-through;
    text-decoration-color: color-mix(in srgb, currentColor 55%, transparent);
  }
  .palette-status {
    padding: 12px 16px;
    color: var(--color-text-muted);
    font-size: 13px;
  }
  .palette-error { color: var(--color-no); }
  .palette-list {
    overflow-y: auto;
    padding: 4px 0 6px;
  }
  .palette-section {
    padding: 8px 16px 3px;
    font-size: 11.5px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-muted);
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .palette-count { text-transform: none; letter-spacing: 0; font-size: 11.5px; color: var(--color-text-light); }
  .palette-row {
    padding: 6px 16px 7px;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: 2px;
    border-left: 2px solid transparent;
  }
  .palette-row.selected { background: var(--color-surface-hover); border-left-color: var(--color-accent); }
  .palette-ws-row { flex-direction: row; align-items: baseline; gap: 8px; }
  .palette-ws-name { font-size: 13.5px; font-weight: 600; color: var(--color-text); }
  .palette-meta { display: flex; align-items: baseline; gap: 6px; min-width: 0; }
  .palette-model {
    font-size: 12px;
    color: var(--color-text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 45%;
  }
  .palette-tag {
    font-size: 11px;
    color: var(--color-text-muted);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-pill);
    padding: 0 7px;
    white-space: nowrap;
  }
  .palette-role { border-color: transparent; padding: 0; }
  .palette-fan { color: var(--color-accent); border-color: var(--color-accent-bg); }
  .palette-hidden { color: var(--color-neutral); font-style: italic; }
  .palette-snippet {
    font-size: 13px;
    color: var(--color-text-secondary);
    line-height: 1.45;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-word;
  }
  .palette-snippet mark {
    background: var(--color-accent-bg);
    color: var(--color-accent);
    border-radius: 2px;
    padding: 0 1px;
    font-weight: 600;
  }
  .palette-foot {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 8px 16px;
    border-top: 1px solid var(--color-border);
    font-size: 12px;
    color: var(--color-text-muted);
  }
  .palette-keys { white-space: nowrap; }
</style>
