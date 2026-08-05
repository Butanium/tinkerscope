<script lang="ts">
  // "This is read-only — how do I actually sample these models?"
  //
  // The published site answers a question it necessarily can't act on: a static site
  // has no backend and no API key, so the path to interactive is running tinkerscope
  // locally against the same share pack. That needs a pack URL, which only the
  // publisher knows — `site export --pack-url` bakes one into the manifest, and a
  // workspace the visitor installed from a `?w=` link already carries its own. When
  // neither is available this says so plainly rather than printing a command that
  // would start an EMPTY tinkerscope and look broken.
  import Modal from './Modal.svelte';
  import Icon from './Icon.svelte';

  let {
    packUrl = null,
    workspaceName = null,
    onclose
  }: { packUrl?: string | null; workspaceName?: string | null; onclose: () => void } = $props();

  const REPO = 'https://github.com/Butanium/tinkerscope';

  /** A shell-safe-ish directory name from the workspace title. */
  const dir = $derived(
    (workspaceName || 'tinkerscope-demo')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'tinkerscope-demo'
  );

  const command = $derived(
    packUrl
      ? `mkdir ${dir} && cd ${dir} && \\\n  uvx tinkerscope --pack ${packUrl}`
      : `mkdir my-runs && cd my-runs && \\\n  uvx tinkerscope`
  );

  let copied = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined;
  function copy(): void {
    navigator.clipboard?.writeText(command.replace(/\\\n\s*/g, ''));
    copied = true;
    clearTimeout(timer);
    timer = setTimeout(() => (copied = false), 1400);
  }
</script>

<Modal title="Open this locally" {onclose} modalStyle="max-width: 40rem;">
  <p class="ol-lede">
    You're reading a <b>read-only snapshot</b> — there's no backend behind it and no API
    key, so nothing here can sample. To go interactive, run tinkerscope on your own
    machine{packUrl ? ' against the same share pack' : ''}:
  </p>

  <div class="ol-cmd">
    <pre><code>{command}</code></pre>
    <button class="ol-copy" class:copied onclick={copy} aria-label="Copy command">
      <Icon name={copied ? 'check' : 'copy'} size={12} />
      {copied ? 'copied' : 'copy'}
    </button>
  </div>

  {#if packUrl}
    <p class="ol-note">
      That installs tinkerscope from source with <code>uv</code> and seeds the folder from
      the pack, so the same workspaces open with the same models. It prints a local URL to
      open.
    </p>
  {:else}
    <p class="ol-note ol-warn">
      This site doesn't publish its workspaces as a share pack, so the command above
      starts an <b>empty</b> tinkerscope pointed at your own training runs — it won't
      reproduce what's on this page. If you want that, ask whoever published this site for
      the pack, then add <code>--pack &lt;url&gt;</code>.
    </p>
  {/if}

  <h4 class="ol-h">What you'll need</h4>
  <ul class="ol-list">
    <li><b>Nothing</b> to read a pack — messages, branches, the Raw view and token
      probabilities all work offline.</li>
    <li><code>TINKER_API_KEY</code> in your environment to actually <b>sample</b> the
      checkpoints. They're public sampler paths, but the compute is billed to you.</li>
    <li>Copy any checkpoint's <code>tinker://</code> path from the ⧉ button beside its
      name in the sidebar, if you'd rather point your own scripts at it.</li>
  </ul>

  <p class="ol-note">
    Already running tinkerscope? You can skip the install and open the pack in it directly
    with <code>?w=&lt;pack url&gt;</code>, or from the sidebar's ⤒ button.
    <a href={REPO} target="_blank" rel="noopener">Source and docs</a>.
  </p>
</Modal>

<style>
  .ol-lede { margin: 0 0 var(--space-3); color: var(--color-text); font-size: 0.85rem; line-height: 1.55; }
  .ol-cmd { position: relative; }
  .ol-cmd pre { margin: 0; padding: var(--space-3); padding-right: 5rem; background: var(--color-surface-alt); border: 1px solid var(--color-border); border-radius: var(--radius); overflow-x: auto; }
  .ol-cmd code { font-family: var(--font-mono); font-size: 0.72rem; line-height: 1.6; color: var(--color-text); white-space: pre; }
  .ol-copy { position: absolute; top: var(--space-2); right: var(--space-2); display: inline-flex; align-items: center; gap: 4px; padding: 3px 7px; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-sm); color: var(--color-text-muted); font-size: 0.68rem; cursor: pointer; }
  .ol-copy:hover { color: var(--color-accent); border-color: var(--color-accent); }
  .ol-copy.copied { color: var(--color-success, var(--color-accent)); }
  .ol-note { margin: var(--space-3) 0 0; color: var(--color-text-secondary); font-size: 0.78rem; line-height: 1.55; }
  .ol-warn { color: var(--color-text); border-left: 2px solid var(--color-border); padding-left: var(--space-2); }
  .ol-h { margin: var(--space-4) 0 var(--space-2); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--color-text-muted); font-weight: 600; }
  .ol-list { margin: 0; padding-left: 1.1rem; color: var(--color-text-secondary); font-size: 0.78rem; line-height: 1.6; }
  .ol-list li { margin: 0.25rem 0; }
  .ol-note code, .ol-list code { font-family: var(--font-mono); font-size: 0.92em; background: var(--color-surface-alt); padding: 1px 4px; border-radius: 3px; }
</style>
