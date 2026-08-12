<!--
  The first-token mode's interactive legend, extracted from ChartModal.

  A chip per named unit (a token, or a merged group): click to exclude/re-include,
  drag one onto another to merge them into one color, ⊗ to split a group, ✕ to
  drop an added token. Below it, a search box that pulls a recorded-but-HIDDEN
  token out of the grey rest — the probability comes from the turn's stored
  logprobs, never a model call.

  The parent owns the units (it derives them from the chart data and persists
  them); this component owns only the transient UI state — which chip is being
  dragged, and the add-search query.

  The drag is a bespoke onto-drop, NOT `$lib/drag-reorder`: that one is
  gap-shaped (drop BETWEEN two items to reorder), this one drops ON a target.
-->
<script lang="ts" module>
  /** One unit in the legend. `members` is what's behind it — a group's tokens,
   *  or the token itself; `addedTid` marks a singleton the user surfaced. */
  export type FtChip = {
    key: string;
    label: string;
    members: string[];
    color?: string;
    excluded: boolean;
    addedTid?: number;
  };
</script>

<script lang="ts">
  import { displayToken, prob } from './token-logprob';
  import { searchStoredTokens, type TokenCandidate } from './token-search';
  import { tip } from '$lib/tooltip.svelte';

  let {
    chips,
    restLabel,
    restColor,
    renorm,
    candidates,
    addedTids,
    namedTokens,
    ontoggle,
    onmerge,
    onunmerge,
    onremoveadded,
    onadd,
    onrenorm
  }: {
    chips: FtChip[];
    restLabel: string;
    restColor: string;
    /** When on, the grey rest is gone from the bar — so don't legend it. */
    renorm: boolean;
    /** Every token with a recorded position-0 logprob for the charted turn. */
    candidates: TokenCandidate[];
    /** Already surfaced (by tid) — adding one again would be a no-op. */
    addedTids: Set<number>;
    /** Already a named unit (by display text) — same. */
    namedTokens: Set<string>;
    ontoggle: (key: string) => void;
    onmerge: (srcKey: string, dstKey: string) => void;
    onunmerge: (key: string) => void;
    onremoveadded: (tid: number) => void;
    onadd: (m: { t: string; tid: number }) => void;
    onrenorm: (v: boolean) => void;
  } = $props();

  let dragKey = $state<string | null>(null);
  let dragOver = $state<string | null>(null);
  let query = $state('');

  // Matches that are actually HIDDEN — not already a shown named unit, not
  // already added.
  const matches = $derived.by(() => {
    if (!query.trim()) return [];
    return searchStoredTokens(query, candidates)
      .filter((m) => !addedTids.has(m.tid) && !namedTokens.has(displayToken(m.t)))
      .slice(0, 20);
  });

  function add(m: { t: string; tid: number }) {
    onadd(m);
    query = '';
  }
</script>

<div class="ft-chips" role="group" aria-label="First-token units">
  {#each chips as chip (chip.key)}
    {@const merged = chip.members.length > 1}
    <div
      class="ft-chip"
      class:off={chip.excluded}
      class:merged
      class:drop-target={dragOver === chip.key && dragKey !== chip.key}
      draggable={!chip.excluded}
      role="button"
      tabindex="0"
      aria-pressed={!chip.excluded}
      data-tooltip={chip.excluded
        ? 'Excluded — click to re-include'
        : merged
          ? 'Merged — click to exclude, drag onto another to grow, ⊗ to split'
          : 'Click to exclude · drag onto another to merge'}
      use:tip
      onclick={() => ontoggle(chip.key)}
      onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), ontoggle(chip.key))}
      ondragstart={(e) => { dragKey = chip.key; e.dataTransfer?.setData('text/plain', chip.key); }}
      ondragend={() => { dragKey = null; dragOver = null; }}
      ondragover={(e) => { if (dragKey && dragKey !== chip.key) { e.preventDefault(); dragOver = chip.key; } }}
      ondragleave={() => { if (dragOver === chip.key) dragOver = null; }}
      ondrop={(e) => { e.preventDefault(); if (dragKey) onmerge(dragKey, chip.key); dragKey = null; dragOver = null; }}
    >
      <span class="chart-legend-swatch" style="background: {chip.color ?? restColor}"></span>
      <span class="ft-chip-label">{chip.label}</span>
      {#if merged}
        <button class="ft-chip-x" title="Split this group" aria-label="Split group"
          onclick={(e) => { e.stopPropagation(); onunmerge(chip.key); }}>⊗</button>
      {:else if chip.addedTid != null}
        <button class="ft-chip-x" title="Remove — back into the rest" aria-label="Remove added token"
          onclick={(e) => { e.stopPropagation(); onremoveadded(chip.addedTid!); }}>✕</button>
      {/if}
    </div>
  {/each}
  {#if !renorm}
    <div class="chart-legend-item ft-rest-legend">
      <span class="chart-legend-swatch" style="background: {restColor}"></span>
      <span class="chart-legend-label">{restLabel}</span>
    </div>
  {/if}
  <label class="ft-renorm"
    data-tooltip="Drop the grey rest and rescale the shown tokens to 100%"
    use:tip>
    <input type="checkbox" checked={renorm} onchange={(e) => onrenorm(e.currentTarget.checked)} />
    <span>renormalize</span>
  </label>
</div>
<!-- Add a recorded-but-hidden token (from stored logprobs; no model call). -->
<div class="ft-add">
  <input class="ft-add-input" type="text" placeholder="add a hidden token… (e.g. “ D”)"
    bind:value={query}
    data-tooltip="Search tokens recorded for this turn and pull one out of the rest"
    use:tip />
  {#if query.trim()}
    <div class="ft-matches">
      {#if matches.length === 0}
        <span class="ft-no-match">no hidden token matches “{query.trim()}” in this turn's recorded logprobs</span>
      {:else}
        {#each matches as m (m.tid)}
          <button class="ft-match" onclick={() => add(m)}
            data-tooltip="{m.kind} match · p={((prob(m.lp) ?? 0) * 100).toFixed(1)}% — click to add" use:tip>
            <span class="ft-match-tok">{displayToken(m.t)}</span>
            <span class="ft-match-p">{((prob(m.lp) ?? 0) * 100).toFixed(1)}%</span>
          </button>
        {/each}
      {/if}
    </div>
  {/if}
</div>

<style>
  .ft-chips { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); }
  .ft-chip { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-bg); color: var(--color-text); font-size: 0.76rem; padding: 2px 8px 2px 6px; cursor: grab; user-select: none; }
  .ft-chip:hover { border-color: var(--color-text-muted); }
  .ft-chip.off { opacity: 0.45; text-decoration: line-through; cursor: pointer; }
  .ft-chip.merged { border-style: dashed; border-color: var(--color-text-muted); }
  .ft-chip.drop-target { outline: 2px solid var(--color-accent); outline-offset: 1px; }
  .ft-chip-label { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ft-chip-x { border: none; background: none; color: var(--color-text-muted); font-size: 0.85rem; line-height: 1; cursor: pointer; padding: 0 1px; }
  .ft-chip-x:hover { color: var(--color-text); }
  .ft-rest-legend { margin-left: var(--space-2); }
  .ft-renorm { display: inline-flex; align-items: center; gap: 5px; margin-left: var(--space-3); font-size: 0.76rem; color: var(--color-text-muted); cursor: pointer; user-select: none; }
  .ft-renorm input { cursor: pointer; margin: 0; }
  .ft-add { position: relative; margin-top: var(--space-3); }
  .ft-add-input { width: 260px; max-width: 100%; font-size: 0.78rem; padding: 4px 8px; border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-bg); color: var(--color-text); }
  .ft-matches { display: flex; flex-wrap: wrap; gap: var(--space-1); margin-top: var(--space-2); max-height: 120px; overflow-y: auto; }
  .ft-match { display: inline-flex; align-items: center; gap: 5px; border: 1px dashed var(--color-border); border-radius: var(--radius); background: var(--color-bg); color: var(--color-text); font-size: 0.74rem; padding: 2px 7px; cursor: pointer; }
  .ft-match:hover { border-color: var(--color-accent); }
  .ft-match-tok { font-family: var(--font-mono, monospace); }
  .ft-match-p { color: var(--color-text-muted); }
  .ft-no-match { font-size: 0.74rem; color: var(--color-text-muted); font-style: italic; }
</style>
