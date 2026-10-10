<!-- The THREAD's system prompt as the first row of a panel's conversation — a
     message like the others, but optional. It overrides the workspace prompt (the
     composer chip) for that thread; stored on the thread's ROOT node.
       - empty panel: a field; whatever it holds is stamped on the first send
       - thread with a prompt: a system message (click the text to expand) with
         edit + copy; saving an edit forks a sibling thread like editing the first
         message (Shift = copy the conversation, no generation; Ctrl = all panels)
       - thread without one: a faint "+ system prompt" that opens the same editor -->
<script lang="ts">
  import Icon from './Icon.svelte';
  import { tip } from './tooltip.svelte';

  let {
    system,
    empty,
    pending,
    readOnly,
    busy,
    comparing,
    shiftDown,
    ctrlDown,
    onPending,
    onSave
  }: {
    /** the active thread's own prompt (root node), undefined = none */
    system: string | undefined;
    /** the panel has no turns yet */
    empty: boolean;
    /** empty panel: the prompt its first send will carry */
    pending: string;
    readOnly: boolean;
    busy: boolean;
    /** >1 panel: the Ctrl "all panels" axis is live */
    comparing: boolean;
    shiftDown: boolean;
    ctrlDown: boolean;
    onPending: (v: string) => void;
    onSave: (text: string, copyDownstream: boolean, allPanels: boolean) => void;
  } = $props();

  let editing = $state(false);
  let draft = $state('');
  let editShift = $state(false);
  let editAll = $state(false);
  let expanded = $state(false);
  let pendingOpen = $state(false);
  let copied = $state(false);

  // A different thread (cycled, switched, forked) must not inherit an open editor.
  let seen: string | undefined;
  $effect(() => {
    const cur = system;
    if (cur === seen) return;
    seen = cur;
    editing = false;
    expanded = false;
  });

  function startEdit(e: MouseEvent) {
    if (busy) return;
    editShift = e.shiftKey;
    editAll = comparing && (e.ctrlKey || e.metaKey);
    draft = system ?? '';
    editing = true;
  }
  function commit() {
    editing = false;
    if (draft.trim() === (system ?? '').trim()) return;
    onSave(draft, editShift, editAll);
  }
  function editKeys(e: KeyboardEvent) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); commit(); }
    else if (e.key === 'Escape') { e.preventDefault(); editing = false; }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(system ?? '');
      copied = true;
      setTimeout(() => (copied = false), 1200);
    } catch { /* clipboard denied — nothing to do */ }
  }
</script>

{#if empty}
  {#if !readOnly}
    {#if pendingOpen || pending}
      <div class="tsys" data-testid="thread-system-row">
        <div class="tsys-head"><span class="tsys-role">system</span></div>
        <!-- svelte-ignore a11y_autofocus -->
        <textarea
          class="edit-textarea tsys-input"
          data-testid="thread-system-pending"
          value={pending}
          oninput={(e) => onPending((e.target as HTMLTextAreaElement).value)}
          onblur={() => { if (!pending.trim()) pendingOpen = false; }}
          autofocus={pendingOpen && !pending}
          rows="2"
          placeholder="This thread's system prompt — sent with your first message"
        ></textarea>
      </div>
    {:else}
      <button class="tsys-add" data-testid="thread-system-add" onclick={() => (pendingOpen = true)}>+ system prompt</button>
    {/if}
  {/if}
{:else if editing}
  <div class="tsys" data-testid="thread-system-row">
    <div class="tsys-head"><span class="tsys-role">system</span></div>
    {#if editShift}<div class="edit-hint">Shift-edit: forks a full copy of the conversation under this prompt — nothing is generated.</div>{/if}
    <!-- svelte-ignore a11y_autofocus -->
    <textarea
      class="edit-textarea tsys-input"
      data-testid="thread-system-edit"
      bind:value={draft}
      onkeydown={editKeys}
      autofocus
      rows="3"
      placeholder="none — the workspace system prompt applies"
    ></textarea>
    <div class="edit-actions">
      <button class="btn-edit-cancel" onclick={() => (editing = false)}>Cancel</button>
      <button class="btn-edit-save" data-testid="thread-system-save" onclick={commit}>Save{editShift ? ' fork' : ''}</button>
    </div>
  </div>
{:else if system}
  <div class="tsys" data-testid="thread-system-row">
    <div class="tsys-head">
      <span class="tsys-role">system</span>
      <span class="tsys-tools">
        {#if !readOnly}
          <button
            class="btn-act"
            class:btn-act-all={comparing && ctrlDown}
            class:shift-alt={shiftDown}
            disabled={busy}
            data-testid="thread-system-edit-btn"
            data-tooltip={shiftDown ? 'Fork a copy of the conversation under a new prompt' : 'Edit — forks a new thread and regenerates the first reply'}
            use:tip
            aria-label="Edit system prompt"
            onclick={startEdit}
          ><Icon name={shiftDown ? 'edit-copy' : 'edit'} /></button>
        {/if}
        <button class="btn-act" class:copied data-tooltip="Copy the system prompt" use:tip aria-label="Copy system prompt" onclick={copy}>
          <Icon name={copied ? 'check' : 'copy'} />
        </button>
      </span>
    </div>
    <button
      class="tsys-text"
      class:expanded
      data-testid="thread-system-strip"
      onclick={() => (expanded = !expanded)}
    >{expanded ? system : system.replace(/\s+/g, ' ')}</button>
  </div>
{:else if !readOnly}
  <button
    class="tsys-add"
    data-testid="thread-system-add"
    disabled={busy}
    data-tooltip="Give this thread its own system prompt (forks it)"
    use:tip
    onclick={startEdit}
  >+ system prompt</button>
{/if}

<style>
  .tsys { flex-shrink: 0; padding: var(--space-3) var(--space-4); background: var(--color-system-bg); border: 1px solid var(--color-border); border-radius: var(--radius-lg); min-width: 0; }
  .tsys-head { display: flex; align-items: center; justify-content: space-between; min-height: 22px; }
  .tsys-role { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.07em; color: var(--color-text-muted); }
  .tsys-tools { display: inline-flex; gap: 2px; opacity: 0.55; transition: opacity 0.15s; }
  .tsys:hover .tsys-tools, .tsys-tools:focus-within { opacity: 1; }
  .tsys-text { display: block; width: 100%; margin-top: var(--space-1); padding: 0; text-align: left; background: none; border: none; cursor: pointer; font-family: var(--font-mono); font-size: 0.78rem; line-height: 1.5; color: var(--color-text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tsys-text.expanded { white-space: pre-wrap; overflow: visible; }
  .tsys-input { margin-top: var(--space-1); }
  .tsys-add { flex-shrink: 0; align-self: flex-start; padding: 2px var(--space-2); background: none; border: 1px dashed var(--color-border); border-radius: var(--radius); color: var(--color-text-muted); font-size: 0.72rem; cursor: pointer; }
  .tsys-add:hover:not(:disabled) { color: var(--color-accent); border-color: var(--color-accent); }
</style>
