<script lang="ts">
  // The tool calls an assistant turn made, one block per call: the function name,
  // then its arguments — pretty-printed when they parse as JSON, verbatim
  // otherwise (a model can emit malformed JSON, and reformatting would hide it).
  // A short call reads inline as `name(args)`; a long one folds behind it.
  import type { ToolCall } from '$lib/types';

  let { calls }: { calls: ToolCall[] } = $props();

  const INLINE_MAX = 90;

  function pretty(args: string): string {
    try {
      return JSON.stringify(JSON.parse(args), null, 2);
    } catch {
      return args;
    }
  }
  function oneline(args: string): string {
    return args.replace(/\s+/g, ' ').trim();
  }
</script>

<div class="tool-calls" data-testid="tool-calls">
  {#each calls as c, i (c.id || i)}
    {@const flat = oneline(c.function.arguments || '')}
    {#if flat.length <= INLINE_MAX}
      <div class="tool-call" data-call-id={c.id}>
        <span class="tool-call-mark" aria-hidden="true">→</span>
        <code class="tool-call-sig"><span class="tool-call-name">{c.function.name}</span>({flat})</code>
      </div>
    {:else}
      <details class="tool-call tool-call-long" data-call-id={c.id}>
        <summary>
          <span class="tool-call-mark" aria-hidden="true">→</span>
          <code class="tool-call-sig"><span class="tool-call-name">{c.function.name}</span>(<span class="tool-call-preview">{flat}</span>)</code>
        </summary>
        <pre class="tool-call-args">{pretty(c.function.arguments || '')}</pre>
      </details>
    {/if}
  {/each}
</div>

<style>
  .tool-calls { display: flex; flex-direction: column; gap: var(--space-1); margin-top: var(--space-2); }
  .tool-call { border: 1px solid var(--color-tool-border); border-radius: var(--radius); background: var(--color-tool-bg); padding: 4px 8px; font-size: 0.82rem; min-width: 0; }
  .tool-call > summary { list-style: none; cursor: pointer; display: flex; align-items: baseline; gap: 6px; min-width: 0; }
  .tool-call > summary::-webkit-details-marker { display: none; }
  .tool-call:not(details) { display: flex; align-items: baseline; gap: 6px; }
  .tool-call-mark { color: var(--color-text-muted); flex: none; }
  .tool-call-sig { font-family: 'Courier New', Courier, monospace; overflow-wrap: anywhere; min-width: 0; }
  .tool-call-name { font-weight: 700; }
  .tool-call-long > summary .tool-call-sig { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .tool-call-long[open] > summary .tool-call-preview { display: none; }
  .tool-call-args { margin: 4px 0 2px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 0.8rem; max-height: 420px; overflow-y: auto; color: var(--color-text-secondary); }
</style>
