<script lang="ts">
  // The consent prompt for a `?w=<pack-path-or-url>` install.
  //
  // Shown for EVERY install, not just colliding ones. A pack link installs on plain
  // navigation, and any web page can navigate a browser to a localhost URL (the API's
  // CORS allowlist guards fetches, not navigation) — so without a prompt a third party
  // could plant workspaces whose transcripts read as if your own checkpoints produced
  // them. The source is named by host for the same reason.
  //
  // With collisions it additionally asks HOW: replace the existing copies, or keep
  // both (the incoming one becomes `<name> (2)`).
  import Modal from '$lib/Modal.svelte';
  import { sourceLabel } from '$lib/pack-source';
  import type { PackPreview, ConflictMode } from '$lib/pack-install';

  let {
    preview,
    source,
    busy = false,
    onchoose,
    onclose
  }: {
    preview: PackPreview;
    source: string | File;
    busy?: boolean;
    onchoose: (mode: ConflictMode) => void;
    onclose: () => void;
  } = $props();

  const clashing = $derived(preview.workspaces.filter((w) => w.exists));
  const fresh = $derived(preview.workspaces.filter((w) => !w.exists));
</script>

<Modal title="Open shared pack" {onclose} modalStyle="max-width: 32rem;">
  <div class="pack-src">
    <span class="pack-src-label">from</span>
    <code>{sourceLabel(source)}</code>
  </div>

  <div class="pack-name">{preview.pack}</div>
  {#if preview.description}
    <p class="pack-desc">{preview.description}</p>
  {/if}

  <p class="pack-clash">
    {#if clashing.length}
      {clashing.length}
      {clashing.length === 1 ? 'workspace' : 'workspaces'} from this pack
      {clashing.length === 1 ? 'is' : 'are'} already here.
    {:else}
      Adds {fresh.length} {fresh.length === 1 ? 'workspace' : 'workspaces'} and
      {preview.models} {preview.models === 1 ? 'model' : 'models'} to this playground.
    {/if}
  </p>

  <ul class="pack-list">
    {#each clashing as w (w.id)}
      <li><span class="pack-badge exists">exists</span>{w.name}</li>
    {/each}
    {#each fresh as w (w.id)}
      <li><span class="pack-badge">new</span>{w.name}</li>
    {/each}
  </ul>

  <div class="pack-actions">
    {#if clashing.length}
      <button class="pack-btn" disabled={busy} onclick={() => onchoose('new')}>
        Keep both
        <span class="pack-btn-sub">installs as “{clashing[0]?.name} (2)”</span>
      </button>
      <button class="pack-btn pack-btn-danger" disabled={busy} onclick={() => onchoose('overwrite')}>
        Replace
        <span class="pack-btn-sub">overwrites the existing {clashing.length === 1 ? 'copy' : 'copies'}</span>
      </button>
    {:else}
      <button class="pack-btn" disabled={busy} onclick={onclose}>Cancel</button>
      <button class="pack-btn pack-btn-go" disabled={busy} onclick={() => onchoose('overwrite')}>
        Install
        <span class="pack-btn-sub">nothing here is overwritten</span>
      </button>
    {/if}
  </div>
</Modal>

<style>
  .pack-src { display: flex; align-items: baseline; gap: var(--space-2); margin-bottom: var(--space-3); font-size: 0.72rem; color: var(--color-text-muted); }
  .pack-src code { font-family: var(--font-mono); overflow-wrap: anywhere; }
  .pack-src-label { flex-shrink: 0; }
  .pack-name { font-weight: 600; font-size: 0.95rem; }
  .pack-desc { margin: var(--space-1) 0 0; font-size: 0.8rem; color: var(--color-text-muted); }
  .pack-clash { margin: var(--space-3) 0 var(--space-2); font-size: 0.82rem; }
  .pack-list { margin: 0 0 var(--space-4); padding: 0; list-style: none; display: flex; flex-direction: column; gap: 2px; max-height: 12rem; overflow-y: auto; }
  .pack-list li { display: flex; align-items: center; gap: var(--space-2); font-size: 0.8rem; }
  .pack-badge { flex-shrink: 0; min-width: 3.2rem; text-align: center; padding: 1px 5px; border-radius: var(--radius-sm); background: var(--color-surface-alt); color: var(--color-text-muted); font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.03em; }
  .pack-badge.exists { background: var(--color-warn-bg, var(--color-surface-alt)); color: var(--color-warn-text, var(--color-text)); }
  .pack-actions { display: flex; gap: var(--space-2); }
  .pack-btn { flex: 1; display: flex; flex-direction: column; gap: 2px; padding: var(--space-2) var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-surface); color: var(--color-text); font-size: 0.82rem; font-weight: 500; cursor: pointer; text-align: left; }
  .pack-btn:hover:not(:disabled) { background: var(--color-surface-alt); }
  .pack-btn:disabled { opacity: 0.5; cursor: wait; }
  .pack-btn-sub { font-size: 0.68rem; font-weight: 400; color: var(--color-text-muted); }
  .pack-btn-danger:hover:not(:disabled) { border-color: var(--color-danger, #c0392b); }
  .pack-btn-go { border-color: var(--color-accent, var(--color-border)); }
</style>
