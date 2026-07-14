<!--
  "Peek at Training Data" loader: pick a root-relative dataset path + a document
  count, emit them via onsubmit. Path/count are owned here (path seeded from
  initialPath = the primary panel's run dataset); the parent does the fetch and
  drops the docs into the composer. `loading` is parent-owned (the fetch lives
  there) so the Load button reflects it.
-->
<script lang="ts">
  import Modal from './Modal.svelte';

  let {
    initialPath,
    loading,
    onsubmit,
    onclose
  }: {
    initialPath: string;
    loading: boolean;
    onsubmit: (path: string, count: number) => void;
    onclose: () => void;
  } = $props();

  let path = $state(initialPath);
  let count = $state(10);
  const submit = () => {
    if (path.trim()) onsubmit(path.trim(), count);
  };
</script>

<Modal title="Peek at Training Data" {onclose} modalStyle="width: 520px; max-width: 90vw;">
  <label class="sidebar-label">Dataset path (root-relative)</label>
  <input type="text" class="sidebar-input" bind:value={path} placeholder="base_vs_instruct_april/.../v1.jsonl" style="margin-top: var(--space-1);" onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }} />
  <label class="sidebar-label" style="margin-top: var(--space-3);">Number of documents</label>
  <input type="number" class="sidebar-input" bind:value={count} min="1" max="500" style="margin-top: var(--space-1);" onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }} />
  <div class="tag-form-actions">
    <button class="btn-new" onclick={onclose}>Cancel</button>
    <button class="btn-tag-submit" onclick={submit} disabled={loading || !path.trim()}>{loading ? 'Loading...' : 'Load into message'}</button>
  </div>
</Modal>
