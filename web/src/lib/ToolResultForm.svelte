<script lang="ts">
  // Answer the tool calls the panel's last turn is waiting on: one field per
  // pending call — text for a function call, a screenshot for a computer call
  // (Responses wire: sent as the next user message, since OpenRouter has no
  // computer_call_output item). Send → the
  // results land as tool turns and the model continues from them; "result only"
  // stores them without generating, so a user turn can follow.
  import type { ResponsesItem, ToolCall } from '$lib/types';
  import { tip } from '$lib/tooltip.svelte';

  type Result = { tool_call_id: string; name?: string; content: string; items?: ResponsesItem[] };
  let {
    pending,
    note = null,
    busy,
    onSubmit
  }: {
    pending: ToolCall[];
    /** Why the workspace's tool handlers didn't answer these (chat_done tool_note). */
    note?: string | null;
    busy: boolean;
    onSubmit: (results: Result[], fire: boolean) => void;
  } = $props();

  let drafts = $state<Record<string, string>>({});
  let shots = $state<Record<string, { url: string; name: string }>>({});
  let missingShot = $derived(pending.some((c) => c.type === 'computer_call' && !shots[c.id]));

  function pick(callId: string, e: Event) {
    const f = (e.currentTarget as HTMLInputElement).files?.[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => (shots[callId] = { url: String(r.result), name: f.name });
    r.readAsDataURL(f);
  }

  function submit(fire: boolean) {
    if (busy || missingShot) return;
    onSubmit(
      pending.map((c): Result => {
        if (c.type === 'computer_call') {
          const s = shots[c.id];
          // OpenRouter's Responses API has no computer_call_output item: the
          // screenshot goes back as the next user message (+ the optional caption).
          const caption = (drafts[c.id] ?? '').trim();
          return {
            tool_call_id: c.id,
            name: 'computer',
            content: `[screenshot ${s.name}]${caption ? ` ${caption}` : ''}`,
            items: [{
              role: 'user',
              content: [
                ...(caption ? [{ type: 'input_text', text: caption }] : []),
                { type: 'input_image', detail: 'high', image_url: s.url }
              ]
            }]
          };
        }
        return { tool_call_id: c.id, name: c.function.name, content: drafts[c.id] ?? '' };
      }),
      fire
    );
    drafts = {};
    shots = {};
  }
  function oneline(s: string): string {
    return s.replace(/\s+/g, ' ').trim();
  }
</script>

<div class="tool-form" data-testid="tool-result-form">
  <div class="tool-form-title">{pending.length === 1 ? 'Tool result' : `${pending.length} tool results`}</div>
  {#if note}<div class="tool-form-note" data-testid="tool-note">{note}</div>{/if}
  {#each pending as c (c.id)}
    <label class="tool-form-call">
      <code class="tool-form-sig"><b>{c.function.name}</b>({oneline(c.function.arguments || '')})</code>
      {#if c.type === 'computer_call'}
        <span class="tool-form-shot">
          <input type="file" accept="image/*" data-testid="tool-result-image" onchange={(e) => pick(c.id, e)} />
          {#if shots[c.id]}<img src={shots[c.id].url} alt="screenshot to send" />{/if}
        </span>
        <textarea rows="1" placeholder="Caption (optional)" data-testid="tool-result-caption" bind:value={drafts[c.id]}></textarea>
      {:else}
        <textarea
          rows="2"
          placeholder="What the tool returned"
          data-testid="tool-result-input"
          bind:value={drafts[c.id]}
          onkeydown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(!e.shiftKey); }
          }}
        ></textarea>
      {/if}
    </label>
  {/each}
  <div class="tool-form-actions">
    <button class="btn-edit-cancel" disabled={busy || missingShot} onclick={() => submit(false)} data-tooltip="Store the result(s) without a reply — then type a user turn" use:tip data-testid="tool-result-only">Result only</button>
    <button class="btn-edit-save" disabled={busy || missingShot} onclick={() => submit(true)} data-tooltip={missingShot ? 'A computer call needs a screenshot' : 'Send the result(s); the model continues (Ctrl/⌘+Enter)'} use:tip data-testid="tool-result-send">Send</button>
  </div>
</div>

<style>
  .tool-form { border: 1px dashed var(--color-tool-border); border-radius: var(--radius); background: var(--color-tool-bg); padding: var(--space-2) var(--space-3); margin: var(--space-2) 0; display: flex; flex-direction: column; gap: var(--space-2); }
  .tool-form-title { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-muted); }
  .tool-form-note { font-size: 0.78rem; color: var(--color-no); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 160px; overflow-y: auto; }
  .tool-form-call { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
  .tool-form-sig { font-family: 'Courier New', Courier, monospace; font-size: 0.78rem; color: var(--color-text-secondary); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .tool-form textarea { width: 100%; box-sizing: border-box; font-family: 'Courier New', Courier, monospace; font-size: 0.85rem; resize: vertical; background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border); border-radius: var(--radius); padding: 6px 8px; }
  .tool-form-shot { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; font-size: 0.8rem; }
  .tool-form-shot img { max-height: 80px; max-width: 160px; border: 1px solid var(--color-border); border-radius: var(--radius); }
  .tool-form-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
</style>
