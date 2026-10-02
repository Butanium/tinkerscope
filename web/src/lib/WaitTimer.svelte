<script lang="ts">
  // "Still waiting" readout for a running panel with no sample yet. The sample
  // call has no server-side deadline (a cold tinker model can take minutes), so
  // this is what tells a slow warmup apart from a hang; Stop is the give-up.
  import { tip } from '$lib/tooltip.svelte';

  let { since, after = 20_000 }: { since: number | undefined; after?: number } = $props();

  let now = $state(Date.now());
  $effect(() => {
    const t = setInterval(() => (now = Date.now()), 1000);
    return () => clearInterval(t);
  });

  function fmt(ms: number): string {
    const s = Math.floor(ms / 1000);
    return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`;
  }
</script>

{#if since != null && now - since >= after}
  <span
    class="wait-timer"
    data-testid="wait-timer"
    data-tooltip="No sample yet — cold models can take minutes; Stop to give up"
    use:tip>waiting on the model · {fmt(now - since)}</span>
{/if}

<style>
  .wait-timer { font-size: 0.72rem; color: var(--color-text-muted); font-variant-numeric: tabular-nums; white-space: nowrap; margin-left: auto; margin-right: var(--space-2); }
</style>
