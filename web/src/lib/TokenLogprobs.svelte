<script lang="ts">
  // The token-logprob inspector body: the RAW generated token stream (thinking
  // tags and all — deliberately NOT the markdown render, so token boundaries
  // are exact), each token tinted by surprisal, hover → a popover with the
  // token's probability + the top-K alternatives as mini bars.
  //
  // The prose-preserving alternative is TokenHeatOverlay (sidebar Token probs →
  // Over), which paints the same tints under the normal markdown. This view is
  // what you fall back to when exact boundaries matter, or when the alignment
  // the overlay needs can't follow the render.
  import type { TokenLogprob } from '$lib/tree';
  import { surprisalAlpha, highlightMatchProb, matchTintBackground } from '$lib/token-logprob';
  import { logprobHighlight } from '$lib/logprobs.svelte';
  import { colorRules } from '$lib/highlights.svelte';
  import TokenPopover from '$lib/TokenPopover.svelte';

  let { tlp }: { tlp: TokenLogprob[] } = $props();

  // The ≤2 highlight rules chosen for match-coloring (order = top/bottom band).
  // Resolved by id so a rename/recolor keeps applying and a deleted rule drops out.
  const rules = $derived(
    logprobHighlight.activeIds
      .map((id) => colorRules().find((r) => r.id === id))
      .filter((r) => r != null)
  );

  // Per-token background, precomputed off `hover` so hovering never recomputes the
  // match mass. null ⇒ no rule selected ⇒ fall back to the surprisal tint.
  const bg = $derived(
    rules.length
      ? tlp.map((e) =>
          matchTintBackground(
            rules.map((r) => ({ color: r.color, prob: highlightMatchProb(e, r) })),
            logprobHighlight.sharpness
          )
        )
      : null
  );

  let hover = $state<number | null>(null);
  let pos = $state<{ x: number; y: number } | null>(null);

  function enter(e: MouseEvent, i: number) {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    // Clamp so the ~240px popover never overflows the right viewport edge.
    const x = Math.min(r.left, window.innerWidth - 260);
    pos = { x: Math.max(4, x), y: r.bottom + 4 };
    hover = i;
  }
  function leave() {
    hover = null;
    pos = null;
  }

  const cur = $derived(hover != null ? tlp[hover] : null);
</script>

<div class="tok-stream" role="figure" aria-label="Token-by-token output with logprobs">
  {#each tlp as e, i (i)}<span
      class="tok"
      class:tok-hover={hover === i}
      class:tok-ghost={e.ghost}
      style={e.ghost
        ? ''
        : bg
          ? `background: ${bg[i]}`
          : surprisalAlpha(e.lp) > 0
            ? `background: rgba(217, 119, 6, ${surprisalAlpha(e.lp)})`
            : ''}
      onmouseenter={(ev) => enter(ev, i)}
      onmouseleave={leave}>{e.t}</span>{/each}
</div>

{#if cur && pos}
  <TokenPopover entry={cur} x={pos.x} y={pos.y} {rules} />
{/if}

<style>
  /* pre-wrap: token text carries its own spaces/newlines — they ARE the data. */
  .tok-stream {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.78rem;
    line-height: 1.7;
  }
  .tok {
    border-radius: 2px;
    cursor: default;
    box-decoration-break: clone;
    -webkit-box-decoration-break: clone;
  }
  .tok-hover {
    outline: 1px solid var(--color-accent);
  }
  /* Ghost = an edited turn's text past where it stopped being the model's.
     Dimmed + dashed so it reads as "text without a number", not as a normal
     token that happens to be untinted (p≈1 tokens are untinted too). */
  .tok-ghost {
    opacity: 0.55;
    border-bottom: 1px dashed var(--color-text-muted);
  }
</style>
