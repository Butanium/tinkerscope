<script lang="ts">
  // A node's verbatim Responses-API items, drawn as they are: message text and
  // image parts (thumbnails, click to enlarge), reasoning (summary text, the
  // encrypted payload's size, or an id-only reference), function / computer calls
  // and their outputs. Unknown item types show as JSON. Text is shown plain (not
  // markdown): this is the exact record of what the model was sent or produced.
  import type { ResponsesItem } from '$lib/types';
  import { tip } from '$lib/tooltip.svelte';

  let { items, foreign = false }: { items: ResponsesItem[]; foreign?: boolean } = $props();

  let zoom = $state<string | null>(null);
  const LONG = 1500;

  type Part = { kind: 'text'; text: string } | { kind: 'image'; url: string; detail?: string };

  function parts(content: unknown): Part[] {
    if (typeof content === 'string') return [{ kind: 'text', text: content }];
    if (!Array.isArray(content)) return [];
    return content.map((p: any): Part => {
      if (p?.type === 'input_image' || p?.type === 'computer_screenshot') return { kind: 'image', url: p.image_url ?? '', detail: p.detail };
      return { kind: 'text', text: p?.text ?? p?.refusal ?? JSON.stringify(p) };
    });
  }
  function isLong(t: string): boolean {
    return t.length > LONG || t.split('\n').length > 20;
  }
  function firstLine(t: string): string {
    return (t.split('\n').find((l) => l.trim()) ?? '').slice(0, 140);
  }
  function args(a: unknown): string {
    if (typeof a !== 'string') return JSON.stringify(a);
    try {
      return JSON.stringify(JSON.parse(a));
    } catch {
      return a;
    }
  }
  function outputText(o: unknown): string {
    return typeof o === 'string' ? o : JSON.stringify(o, null, 2);
  }
  function kb(n: number): string {
    return n > 1024 ? `${Math.round(n / 1024)} KB` : `${n} chars`;
  }
</script>

{#snippet text(t: string)}
  {#if isLong(t)}
    <details class="ri-fold">
      <summary>{firstLine(t)} <span class="ri-dim">· {t.length.toLocaleString()} chars</span></summary>
      <div class="ri-text">{t}</div>
    </details>
  {:else}
    <div class="ri-text">{t}</div>
  {/if}
{/snippet}

{#snippet image(url: string, detail?: string)}
  <button class="ri-thumb" onclick={() => (zoom = url)} data-tooltip={`Screenshot${detail ? ` (detail ${detail})` : ''} — click to enlarge`} use:tip>
    <img src={url} alt="screenshot" loading="lazy" />
  </button>
{/snippet}

<div class="ri" data-testid="responses-items">
  {#each items as it, i (i)}
    {@const t = it.type ?? 'message'}
    {#if t === 'message'}
      <div class="ri-item ri-msg" data-item="message">
        {#if items.length > 1 || it.role === 'system'}<span class="ri-tag">{it.role}{it.phase ? ` · ${it.phase}` : ''}</span>{/if}
        {#each parts(it.content) as p, j (j)}
          {#if p.kind === 'image'}{@render image(p.url, p.detail)}{:else if p.text}{@render text(p.text)}{:else}<span class="ri-dim">(empty)</span>{/if}
        {/each}
      </div>
    {:else if t === 'reasoning'}
      {@const summ = (it.summary ?? []).map((s: any) => s?.text ?? '').filter(Boolean)}
      <details class="ri-item ri-reasoning" data-item="reasoning">
        <summary>
          <span class="ri-tag">reasoning</span>
          {#if summ.length}<span class="ri-dim">summary</span>{/if}
          {#if it.encrypted_content}
            <span class="ri-badge" data-tooltip="The model's hidden reasoning, encrypted" use:tip>encrypted · {kb(it.encrypted_content.length)}</span>
            {#if foreign}<span class="foreign-reasoning" data-tooltip="Made under another account — the next turn won't see it" use:tip>other account</span>{/if}
          {:else}
            <span class="ri-badge ri-idonly" data-tooltip="Sent by id only — only the account that made it can resolve it" use:tip>id only</span>
          {/if}
        </summary>
        {#each summ as s, j (j)}<div class="ri-text">{s}</div>{/each}
        {#if !summ.length}<div class="ri-dim">no summary · {it.id}</div>{/if}
      </details>
    {:else if t === 'function_call'}
      <div class="ri-item ri-call" data-item="function_call" data-call-id={it.call_id}>
        <code><span class="ri-arrow">→</span> <b>{it.name}</b>({args(it.arguments)})</code>
      </div>
    {:else if t === 'computer_call'}
      <div class="ri-item ri-call" data-item="computer_call" data-call-id={it.call_id}>
        <code><span class="ri-arrow">→</span> <b>computer</b>({args(it.action ?? it.actions)})</code>
      </div>
    {:else if t === 'function_call_output'}
      <div class="ri-item ri-out" data-item="function_call_output">
        <span class="ri-tag">← {it.call_id}</span>
        {@render text(outputText(it.output))}
      </div>
    {:else if t === 'computer_call_output'}
      <div class="ri-item ri-out" data-item="computer_call_output">
        <span class="ri-tag">← {it.call_id}</span>
        {#if it.output?.image_url}{@render image(it.output.image_url)}{:else}<code>{JSON.stringify(it.output)}</code>{/if}
      </div>
    {:else}
      <details class="ri-item ri-other">
        <summary><span class="ri-tag">{t}</span></summary>
        <pre class="ri-json">{JSON.stringify(it, null, 2)}</pre>
      </details>
    {/if}
  {/each}
</div>

{#if zoom}
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="ri-zoom" onclick={() => (zoom = null)} data-testid="screenshot-zoom">
    <img src={zoom} alt="screenshot, enlarged" />
  </div>
{/if}
<svelte:window onkeydown={(e) => { if (zoom && e.key === 'Escape') zoom = null; }} />

<style>
  .ri { display: flex; flex-direction: column; gap: var(--space-2); min-width: 0; }
  .ri-item { min-width: 0; }
  .ri-tag { display: inline-block; font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-muted); margin-right: 6px; }
  .ri-dim { color: var(--color-text-muted); font-size: 0.78rem; }
  .ri-text { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 0.88rem; line-height: 1.5; }
  .ri-fold > summary { cursor: pointer; overflow-wrap: anywhere; font-size: 0.86rem; }
  .ri-fold[open] > summary { margin-bottom: var(--space-1); }
  .ri-reasoning > summary { cursor: pointer; display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
  .ri-reasoning .ri-text { color: var(--color-text-secondary); font-size: 0.84rem; margin-top: 4px; }
  .ri-badge { font-size: 0.7rem; border: 1px solid var(--color-border); border-radius: 999px; padding: 0 6px; color: var(--color-text-muted); }
  .ri-idonly { border-style: dashed; }
  .foreign-reasoning { font-size: 0.68rem; font-weight: 600; color: var(--color-no); border: 1px solid currentColor; border-radius: 999px; padding: 0 6px; }
  .ri-call, .ri-out { border: 1px solid var(--color-tool-border); border-radius: var(--radius); background: var(--color-tool-bg); padding: 4px 8px; }
  .ri-call code, .ri-out code { font-family: 'Courier New', Courier, monospace; font-size: 0.82rem; overflow-wrap: anywhere; }
  .ri-out .ri-text { font-family: 'Courier New', Courier, monospace; font-size: 0.8rem; }
  .ri-arrow { color: var(--color-text-muted); }
  .ri-thumb { display: inline-block; padding: 0; border: 1px solid var(--color-border); border-radius: var(--radius); background: none; cursor: zoom-in; margin: 4px 0; }
  .ri-thumb img { display: block; max-width: min(320px, 100%); max-height: 220px; border-radius: var(--radius); }
  .ri-json { font-size: 0.75rem; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow-y: auto; }
  .ri-zoom { position: fixed; inset: 0; z-index: 200; background: rgba(0, 0, 0, 0.82); display: flex; align-items: center; justify-content: center; cursor: zoom-out; }
  .ri-zoom img { max-width: 96vw; max-height: 94vh; object-fit: contain; }
</style>
