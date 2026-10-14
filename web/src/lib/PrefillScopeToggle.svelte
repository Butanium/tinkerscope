<!-- Which half of a send a prefill applies to, as glyphs: thinking + answer /
     thinking only / answer only. `embedded` = drawn as segments INSIDE the
     prefill SplitChip (its `extra` slot), sharing the pill's border. -->
<script lang="ts">
  import Icon from './Icon.svelte';
  import { tip } from './tooltip.svelte';
  import type { PrefillScope } from './types';

  let {
    scope,
    dim = false,
    skipped = null,
    all = false,
    embedded = false,
    testid = '',
    onpick
  }: {
    scope: PrefillScope;
    /** the prefill is muted or empty — the choice is kept but nothing rides on it */
    dim?: boolean;
    /** why the picked scope matches no sample right now (e.g. "thinking is off") */
    skipped?: string | null;
    /** Ctrl/⌘ is held and a click would set every panel's scope */
    all?: boolean;
    embedded?: boolean;
    testid?: string;
    onpick: (scope: PrefillScope, all: boolean) => void;
  } = $props();

  const OPTS: { id: PrefillScope; tip: string }[] = [
    { id: 'all', tip: 'Prefill thinking and non-thinking samples' },
    { id: 'think', tip: 'Prefill thinking samples only' },
    { id: 'non_think', tip: 'Prefill non-thinking samples only' }
  ];
</script>

<span class="seg-toggle seg-icons" class:dim class:embedded data-testid={testid || undefined}>
  {#each OPTS as o (o.id)}
    {@const on = scope === o.id}
    <button
      class="seg-btn"
      class:active={on}
      class:skipped={on && !!skipped}
      aria-pressed={on}
      aria-label={o.tip}
      data-scope={o.id}
      data-tooltip={(on && skipped ? `${o.tip} — none now, ${skipped}` : o.tip) + (all ? ' — in ALL panels' : '')}
      use:tip
      onclick={(e) => onpick(o.id, e.ctrlKey || e.metaKey)}
    >
      {#if o.id !== 'non_think'}<Icon name="think" size={13} />{/if}
      {#if o.id !== 'think'}<Icon name="answer" size={13} />{/if}
    </button>
  {/each}
</span>
