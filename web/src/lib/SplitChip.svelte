<!-- Split-pill composer chip: TWO orthogonal affordances in one pill.
     Left POWER zone = enable/disable (does the field apply to sends);
     right LABEL+chevron zone = expand/fold the editor. Folding never changes
     enabled — the split exists so "keep the text but stop applying it" and
     "edit without applying yet" are both reachable (the old single chip
     conflated the axes). Visual grammar: dot filled = enabled (armed even if
     empty); pill accent = ACTIVE (enabled AND non-empty text). -->
<script lang="ts">
  import { tip } from './tooltip.svelte';

  let {
    label,
    active,
    enabled,
    open,
    powerTip,
    editTip,
    testid = '',
    onpower,
    onfold
  }: {
    label: string;
    /** enabled AND non-empty — the pill wears accent. */
    active: boolean;
    /** the raw power flag — fills the dot. */
    enabled: boolean;
    open: boolean;
    powerTip: string;
    editTip: string;
    testid?: string;
    onpower: () => void;
    onfold: () => void;
  } = $props();
</script>

<span class="split-chip" class:on={active} class:enabled class:open>
  <button
    class="zone pwr"
    data-testid={testid ? `${testid}-power` : undefined}
    aria-pressed={enabled}
    aria-label="{enabled ? 'Disable' : 'Enable'} {label}"
    data-tooltip={powerTip}
    use:tip
    onclick={onpower}
  ><span class="pdot"></span></button>
  <button
    class="zone lbl"
    data-testid={testid ? `${testid}-fold` : undefined}
    aria-expanded={open}
    data-tooltip={editTip}
    use:tip
    onclick={onfold}
  >{label}<span class="chev"></span></button>
</span>

<style>
  .split-chip { display: inline-flex; align-items: stretch; border: 1px solid var(--color-border); border-radius: var(--radius-pill); overflow: hidden; background: var(--color-bg); flex-shrink: 0; }
  .zone { border: none; background: none; color: var(--color-text-muted); font-size: 0.7rem; padding: 2px 8px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
  .zone:hover { color: var(--color-text); }
  .zone.pwr { border-right: 1px solid var(--color-border); padding: 2px 7px; }
  .pdot { width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid currentColor; box-sizing: border-box; }
  /* enabled: the dot fills (armed) — even before any text makes it ACTIVE */
  .split-chip.enabled .pdot { background: var(--color-accent); border-color: var(--color-accent); }
  /* active (enabled + text): the whole pill wears accent; power zone goes solid */
  .split-chip.on { border-color: var(--color-accent); }
  .split-chip.on .zone { color: var(--color-accent); }
  .split-chip.on .zone.lbl { background: var(--color-accent-bg); }
  .split-chip.on .zone.pwr { background: var(--color-accent); border-right-color: var(--color-accent); }
  .split-chip.on .pdot { background: white; border-color: white; }
  .chev { width: 0; height: 0; border-left: 3.5px solid transparent; border-right: 3.5px solid transparent; border-top: 4.5px solid currentColor; transform: rotate(-90deg); transition: transform 0.15s; opacity: 0.8; }
  .split-chip.open .chev { transform: rotate(0deg); }
  @media (prefers-reduced-motion: reduce) { .chev { transition: none; } }
</style>
