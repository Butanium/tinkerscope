<script lang="ts">
  // "Still waiting" readout for a running panel with no sample yet. The sample
  // call has no deadline (a cold tinker model can take minutes); what tells a
  // slow warmup from a hang is whether tinker keeps ANSWERING — it re-confirms
  // a live request every ~30 s (bus `chat_status`, tinker_sampler "Liveness").
  import { tip } from '$lib/tooltip.svelte';
  import type { TinkerStatus } from '$lib/types';

  let {
    since,
    tinker = undefined,
    after = 20_000
  }: { since: number | undefined; tinker?: TinkerStatus; after?: number } = $props();

  // Past ~2.5 poll windows without an answer, say so. The server reconnects at
  // 180 s of silence (SILENT_S); this only names the silence earlier.
  const QUIET_MS = 75_000;

  let now = $state(Date.now());
  $effect(() => {
    const t = setInterval(() => (now = Date.now()), 1000);
    return () => clearInterval(t);
  });

  function fmt(ms: number): string {
    const s = Math.floor(ms / 1000);
    return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`;
  }

  const STATE_LABEL: Record<string, string> = {
    paused_capacity: 'tinker: queued, short on capacity',
    paused_rate_limit: 'tinker: paused, rate limit',
    throttled: 'tinker: throttling us (429)'
  };

  function tinkerParts(t: TinkerStatus | undefined, now: number, since: number): string[] {
    const parts: string[] = [];
    if (t?.heardAt != null) {
      parts.push(
        now - t.heardAt > QUIET_MS
          ? `no answer from tinker for ${fmt(now - t.heardAt)}`
          : t.http != null && t.http >= 500
            ? `tinker: answering with errors (HTTP ${t.http})`
            : (STATE_LABEL[t.state ?? ''] ?? 'tinker: working')
      );
    } else if (now - since > QUIET_MS) {
      parts.push('no answer from tinker yet');
    }
    if (t?.reconnects) parts.push(`reconnected ×${t.reconnects}`);
    if (t?.resubmits) parts.push(`resent ×${t.resubmits}`);
    return parts;
  }

  let parts = $derived(since != null ? tinkerParts(tinker, now, since) : []);
  let tooltip = $derived(
    tinker?.heardAt != null
      ? `Tinker last answered ${fmt(now - tinker.heardAt)} ago — Stop to give up`
      : 'No sample yet — cold models can take minutes; Stop to give up'
  );
</script>

{#if since != null && now - since >= after}
  <span class="wait-timer" data-testid="wait-timer" data-tooltip={tooltip} use:tip>
    <span class="wait-base">waiting on the model · {fmt(now - since)}</span>
    {#each parts as part (part)}{' · '}<span class="wait-part" data-testid="wait-tinker">{part}</span>{/each}
  </span>
{/if}

<style>
  /* Wraps rather than truncates: in a narrow column the tinker part is the
     information, so it gets the second line instead of an ellipsis. */
  .wait-timer { display: block; flex: 0 1 auto; min-width: 0; text-align: right; font-size: 0.72rem; color: var(--color-text-muted); font-variant-numeric: tabular-nums; margin-left: auto; margin-right: var(--space-2); }
  .wait-base, .wait-part { white-space: nowrap; }
</style>
