<script lang="ts">
  // Progress for a `?w=<pack>` install.
  //
  // Why this exists as a MODAL rather than a corner spinner: a pack link installs on
  // plain navigation, so the visitor's first frame of the app is a workspace they did
  // not ask for (whatever was newest), which then swaps under them tens of seconds
  // later. Covering that with a box that names the pack and the phase turns "the site
  // is broken" into "the site is fetching 18 MB".
  //
  // Dismissable on purpose. There is no abort plumbing, so a close here only hides the
  // report — the load keeps going and still ends in the usual notice. Trapping someone
  // behind a stalled fetch would be worse than letting them look at the app underneath.
  import Modal from '$lib/Modal.svelte';
  import { sourceLabel } from '$lib/pack-source';
  import type { PackProgress } from '$lib/pack-install';

  let {
    source,
    progress,
    onclose
  }: {
    source: string | File;
    progress: PackProgress;
    onclose: () => void;
  } = $props();

  function mb(n: number): string {
    return n < 1e6 ? `${Math.round(n / 1e3)} kB` : `${(n / 1e6).toFixed(1)} MB`;
  }

  // A `content-length` under a content-encoding counts COMPRESSED bytes while the
  // reader hands us decoded ones, so a total we've already passed is no total at all.
  const total = $derived(
    progress.total && progress.done !== undefined && progress.done <= progress.total
      ? progress.total
      : null
  );
  const pct = $derived(total && progress.done !== undefined ? (progress.done / total) * 100 : null);

  const label = $derived.by(() => {
    const { phase, done } = progress;
    if (phase === 'fetch')
      return total
        ? `Downloading — ${mb(done ?? 0)} of ${mb(total)}`
        : `Downloading — ${mb(done ?? 0)}`;
    if (phase === 'decode') return 'Decompressing…';
    if (phase === 'parse') return 'Reading the pack…';
    if (phase === 'server') return 'Reading the pack…';
    return progress.total
      ? `Installing workspace ${(done ?? 0) + 1} of ${progress.total}…`
      : 'Installing…';
  });
</script>

<Modal title="Opening shared pack" {onclose} modalStyle="max-width: 28rem;">
  <div class="pl-src" data-testid="pack-loading">
    <span class="pl-src-label">from</span>
    <code>{sourceLabel(source)}</code>
  </div>

  <div class="pl-label">{label}</div>
  <div class="pl-bar" class:indeterminate={pct === null}>
    <div class="pl-fill" style={pct === null ? '' : `width: ${pct.toFixed(1)}%`}></div>
  </div>
  <p class="pl-note">
    A pack arrives whole before anything renders, so a large one takes a moment. Closing
    this doesn't stop it.
  </p>
</Modal>

<style>
  .pl-src { display: flex; align-items: baseline; gap: var(--space-2); margin-bottom: var(--space-4); font-size: 0.72rem; color: var(--color-text-muted); }
  .pl-src code { font-family: var(--font-mono); overflow-wrap: anywhere; }
  .pl-src-label { flex-shrink: 0; }
  .pl-label { font-size: 0.85rem; font-variant-numeric: tabular-nums; margin-bottom: var(--space-2); }
  .pl-bar { position: relative; height: 6px; border-radius: 3px; background: var(--color-surface-alt); overflow: hidden; }
  .pl-fill { height: 100%; width: 0; border-radius: 3px; background: var(--color-accent); transition: width 120ms linear; }
  /* No measurable total (decode/parse/server): a sweep says "working", a frozen bar at
     0% or 100% says "stuck". */
  .pl-bar.indeterminate .pl-fill { width: 35%; animation: pl-sweep 1.1s ease-in-out infinite; }
  @keyframes pl-sweep {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(320%); }
  }
  .pl-note { margin: var(--space-3) 0 0; font-size: 0.7rem; color: var(--color-text-muted); }
</style>
