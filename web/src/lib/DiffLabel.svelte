<!--
  Diff-view label for sibling list rows: renders the compact form from
  label-diff's `diffLabels` — varying segments at full emphasis, cluster-constant
  runs collapsed to a dimmed `…`, so sibling runs that share both ends and differ
  only mid-name (`…_base_…` vs `…_instruct_…`) stay distinguishable where a tail
  cap would render them identically. The full label lives in the tooltip +
  aria-label (and the typeahead's search field), same affordances as TruncLabel.

  Width policy mirrors TruncLabel's philosophy: only the LEADING anchor (the
  constant family prefix, least informative) may shrink/ellipsize under narrowness;
  the varying middle + distinguishing tail never clip.
-->
<script lang="ts">
  import { tip } from './tooltip.svelte';
  import type { DiffRender } from './label-diff';

  let { label, parts }: { label: string; parts: DiffRender } = $props();

  // The first non-icon part is the family anchor — the only piece allowed to
  // shrink. (A leading status icon, if present, is a separate tiny part.)
  const shrinkIdx = $derived(parts.findIndex((p) => !/^[⊘?◆◇↗]\s/.test(p.text)));
</script>

<span class="difflabel" use:tip data-tooltip={label} aria-label={label}>
  {#each parts as part, i (i)}<span class="dl-part {part.kind}" class:shrink={i === shrinkIdx}
      >{part.text}</span
    >{/each}
</span>

<style>
  .difflabel {
    display: inline-flex;
    align-items: baseline;
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
  }
  .dl-part {
    flex: 0 0 auto;
    white-space: nowrap;
  }
  .dl-part.shrink {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  /* Anchors (constant family prefix / trailing anchor) + elision marks recede;
     varying segments keep the default text color so they pop. */
  .dl-part.anchor,
  .dl-part.elision {
    color: var(--color-text-muted);
  }
  .dl-part.vary {
    color: var(--color-text);
  }
</style>
