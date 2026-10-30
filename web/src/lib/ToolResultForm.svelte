<script lang="ts">
  // Answer the tool calls the panel's last turn is waiting on: one field per
  // pending call. Send → the results land as tool turns and the model continues
  // from them; "result only" stores them without generating, so a user turn can
  // follow (tool → user → assistant is a valid thread too).
  import type { ToolCall } from '$lib/types';
  import { tip } from '$lib/tooltip.svelte';

  let {
    pending,
    busy,
    onSubmit
  }: {
    pending: ToolCall[];
    busy: boolean;
    onSubmit: (results: { tool_call_id: string; name?: string; content: string }[], fire: boolean) => void;
  } = $props();

  let drafts = $state<Record<string, string>>({});

  function submit(fire: boolean) {
    if (busy) return;
    onSubmit(
      pending.map((c) => ({ tool_call_id: c.id, name: c.function.name, content: drafts[c.id] ?? '' })),
      fire
    );
    drafts = {};
  }
  function oneline(s: string): string {
    return s.replace(/\s+/g, ' ').trim();
  }
</script>

<div class="tool-form" data-testid="tool-result-form">
  <div class="tool-form-title">{pending.length === 1 ? 'Tool result' : `${pending.length} tool results`}</div>
  {#each pending as c (c.id)}
    <label class="tool-form-call">
      <code class="tool-form-sig"><b>{c.function.name}</b>({oneline(c.function.arguments || '')})</code>
      <textarea
        rows="2"
        placeholder="What the tool returned"
        data-testid="tool-result-input"
        bind:value={drafts[c.id]}
        onkeydown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(!e.shiftKey); }
        }}
      ></textarea>
    </label>
  {/each}
  <div class="tool-form-actions">
    <button class="btn-edit-cancel" disabled={busy} onclick={() => submit(false)} data-tooltip="Store the result(s) without a reply — then type a user turn" use:tip data-testid="tool-result-only">Result only</button>
    <button class="btn-edit-save" disabled={busy} onclick={() => submit(true)} data-tooltip="Send the result(s); the model continues (Ctrl/⌘+Enter)" use:tip data-testid="tool-result-send">Send</button>
  </div>
</div>

<style>
  .tool-form { border: 1px dashed var(--color-tool-border); border-radius: var(--radius); background: var(--color-tool-bg); padding: var(--space-2) var(--space-3); margin: var(--space-2) 0; display: flex; flex-direction: column; gap: var(--space-2); }
  .tool-form-title { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-muted); }
  .tool-form-call { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
  .tool-form-sig { font-family: 'Courier New', Courier, monospace; font-size: 0.78rem; color: var(--color-text-secondary); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .tool-form textarea { width: 100%; box-sizing: border-box; font-family: 'Courier New', Courier, monospace; font-size: 0.85rem; resize: vertical; background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border); border-radius: var(--radius); padding: 6px 8px; }
  .tool-form-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
</style>
