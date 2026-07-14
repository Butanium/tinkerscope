<!--
  "Save Pin" form: shows the model + a response preview, takes a note, and emits
  it via onsubmit. The note state is owned here (reset every open since the
  caller guards with {#if}); the parent supplies the captured label/response and
  persists the pin in its onsubmit handler.
-->
<script lang="ts">
  import Modal from './Modal.svelte';

  let {
    label,
    response,
    onsubmit,
    onclose
  }: {
    label: string;
    response: string;
    onsubmit: (note: string) => void;
    onclose: () => void;
  } = $props();

  let note = $state('');
  const submit = () => {
    if (note.trim()) onsubmit(note);
  };
</script>

<Modal title="Save Pin" {onclose} modalStyle="width: 520px; max-width: 90vw;">
  <div class="tag-preview">
    <div class="tag-preview-label">Model</div>
    <div class="tag-preview-value">{label}</div>
    <div class="tag-preview-label">Response</div>
    <div class="tag-preview-value tag-preview-response">{response.slice(0, 300)}{response.length > 300 ? '...' : ''}</div>
  </div>
  <label class="sidebar-label" style="margin-top: var(--space-3);">Note</label>
  <textarea class="tag-note-input" bind:value={note} rows="3" placeholder="What's interesting about this response?" onkeydown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}></textarea>
  <div class="tag-form-actions">
    <button class="btn-new" onclick={onclose}>Cancel</button>
    <button class="btn-tag-submit" onclick={submit} disabled={!note.trim()}>Save</button>
  </div>
</Modal>

<style>
  .tag-preview { display: grid; grid-template-columns: auto 1fr; gap: var(--space-1) var(--space-3); font-size: 0.82rem; }
  .tag-preview-label { font-weight: 600; color: var(--color-text-muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; }
  .tag-preview-value { color: var(--color-text); }
  .tag-preview-response { max-height: 100px; overflow-y: auto; font-size: 0.78rem; color: var(--color-text-secondary); line-height: 1.4; }
  .tag-note-input { width: 100%; padding: var(--space-2) var(--space-3); background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-text); font-family: var(--font-sans); font-size: 0.88rem; resize: vertical; margin-top: var(--space-2); }
  .tag-note-input:focus { outline: none; border-color: var(--color-accent); }
</style>
