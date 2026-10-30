<script lang="ts">
  // Edit the open workspace's tool definitions (JSON). See lib/tools-def.ts for
  // what the parser accepts.
  import { untrack } from 'svelte';
  import Modal from '$lib/Modal.svelte';
  import { parseToolDefs, formatToolDefs, toolName } from '$lib/tools-def';
  import type { ToolDef } from '$lib/types';

  let {
    tools,
    onsave,
    onclose
  }: {
    tools: ToolDef[] | null;
    onsave: (tools: ToolDef[] | null) => void;
    onclose: () => void;
  } = $props();

  // seeded once: the editor owns the text while open
  let text = $state(untrack(() => formatToolDefs(tools)));
  let parsed = $derived(parseToolDefs(text));

  function save() {
    if (parsed.error !== undefined) return;
    onsave(parsed.tools ?? null);
    onclose();
  }
</script>

<Modal title="Tools" {onclose} modalStyle="width: 640px; max-width: 94vw;">
  <textarea
    class="tools-json"
    data-testid="tools-json"
    bind:value={text}
    spellcheck="false"
    rows="16"
    placeholder={'[{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}]'}
    onkeydown={(e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
    }}
  ></textarea>
  <div class="tools-status" class:err={parsed.error !== undefined} data-testid="tools-status">
    {#if parsed.error !== undefined}{parsed.error}{:else if parsed.tools}{parsed.tools.length} tool{parsed.tools.length === 1 ? '' : 's'}: {parsed.tools.map(toolName).join(', ')}{:else}No tools{/if}
  </div>
  <div class="tag-form-actions">
    <button class="btn-new" onclick={save} disabled={parsed.error !== undefined} data-testid="tools-save">Save</button>
    <button class="btn-skip" onclick={onclose}>Cancel</button>
  </div>
</Modal>

<style>
  .tools-json { width: 100%; box-sizing: border-box; font-family: 'Courier New', Courier, monospace; font-size: 0.8rem; line-height: 1.45; background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border); border-radius: var(--radius-sm); padding: var(--space-2); resize: vertical; }
  .tools-json:focus { outline: none; border-color: var(--color-accent); }
  .tools-status { font-size: 0.76rem; color: var(--color-text-muted); padding: var(--space-2) 0; overflow-wrap: anywhere; }
  .tools-status.err { color: var(--color-no); }
  .btn-skip { background: none; border: 1px solid var(--color-border); border-radius: var(--radius-sm); color: var(--color-text-muted); padding: var(--space-2) var(--space-3); font-size: 0.78rem; cursor: pointer; }
</style>
