<!--
  Tail-preserving label: ellipsizes the HEAD but always shows the distinguishing
  TAIL, so sibling runs sharing a long prefix (…_s1_lr1e-3 vs …_s1_lr5e-3) stay
  distinguishable at any width. splitTail() (lib/label-split) does the carving;
  this owns the two-span CSS trick + the full-name tooltip backstop.

  Seam note: the head is `flex: 0 1 auto` (shrink, DON'T grow) — NOT `flex: 1`.
  With flex:1 the head would grow to fill free space and push the tail to the far
  right, opening a gap mid-label at full width. 0-grow keeps head+tail packed
  content-tight (one seamless label) and only shrinks/ellipsizes the head when
  the container is too narrow; the tail (flex-shrink:0) never clips.
-->
<script lang="ts">
  import { tip } from './tooltip.svelte';
  import { splitTail } from './label-split';

  let {
    label,
    siblings = undefined
  }: {
    label: string;
    /** Sibling labels for divergence-anchored splitting (list contexts). Omit
     *  for single-label contexts (fixed-length tail). */
    siblings?: readonly string[] | undefined;
  } = $props();

  const parts = $derived(splitTail(label, siblings));
</script>

<span class="trunc" use:tip data-tooltip={label} aria-label={label}>
  <span class="trunc-head">{parts.head}</span><span class="trunc-tail">{parts.tail}</span>
</span>

<style>
  .trunc {
    display: inline-flex;
    align-items: baseline;
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
  }
  .trunc-head {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .trunc-tail {
    flex: 0 0 auto;
    white-space: nowrap;
  }
</style>
