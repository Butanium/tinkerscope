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
  import { loomCut as loomCutOf } from '$lib/loom';
  import { logprobHighlight } from '$lib/logprobs.svelte';
  import { colorRules } from '$lib/highlights.svelte';
  import TokenPopover from '$lib/TokenPopover.svelte';

  let {
    tlp,
    onLoom,
    loomCut = null
  }: {
    tlp: TokenLogprob[];
    /** The LOOM: clicking a token PINS its popover, whose alternatives then fire
     *  this with (stored-stream cut, picked alt tid | null=resample). Absent =
     *  read-only / busy / uncommitted row — hover stays informational. */
    onLoom?: (cut: number, altTid: number | null) => void;
    /** This turn IS a loom branch: entries before this DISPLAY index were forced
     *  (replayed prefix + picked alternative) — dotted-underlined; where the
     *  underline ends is the fork point. null = not a loom turn. */
    loomCut?: number | null;
  } = $props();

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

  /** A token's [leading ws, core, trailing ws]. Match tint colors only the
   *  core: a BPE token carries its leading space, and painting it reads as
   *  highlighting the gap between words. The surprisal heat keeps the whole
   *  token — it's a continuous ribbon, not a highlight. */
  function wsSplit(t: string): [string, string, string] {
    const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(t)!;
    return [m[1], m[2], m[3]];
  }

  let hover = $state<number | null>(null);
  let pos = $state<{ x: number; y: number } | null>(null);
  // The LOOM pin: a clicked token whose popover turned interactive. Its position
  // is frozen at pin time (the hover pos clears on mouseleave).
  let pinned = $state<number | null>(null);
  let pinnedPos = $state<{ x: number; y: number } | null>(null);
  // Data swapped under the pin (edit / fold / cycle) → the index is meaningless.
  $effect(() => {
    void tlp;
    pinned = null;
    pinnedPos = null;
  });

  // O(1) per-token loom eligibility (loomCut itself is O(i) — fine for one
  // click, not for a class on every rendered token): everything before the
  // first EDIT ghost, ghosts themselves excluded.
  const firstEditGhost = $derived(tlp.findIndex((e) => e.ghost && e.ghostKind !== 'prefill'));
  const canLoomAt = (i: number) =>
    !!onLoom && !tlp[i]?.ghost && (firstEditGhost < 0 || i < firstEditGhost);

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
  function clickTok(e: MouseEvent, i: number) {
    if (!canLoomAt(i)) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return; // a drag-select, not a pick
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    pinnedPos = { x: Math.max(4, Math.min(r.left, window.innerWidth - 260)), y: r.bottom + 4 };
    pinned = i;
  }
  function unpin() {
    pinned = null;
    pinnedPos = null;
  }
  function pick(altTid: number | null) {
    if (pinned == null || !onLoom) return;
    const cut = loomCutOf(tlp, pinned);
    unpin();
    if (cut != null) onLoom(cut, altTid);
  }

  const cur = $derived(hover != null ? tlp[hover] : null);
</script>

<div class="tok-stream" role="figure" aria-label="Token-by-token output with logprobs">
  {#each tlp as e, i (i)}<span
      class="tok"
      class:tok-hover={hover === i}
      class:tok-pinned={pinned === i}
      class:tok-loom={canLoomAt(i)}
      class:tok-replayed={loomCut != null && i < loomCut && !e.ghost}
      class:tok-ghost={e.ghost}
      style={e.ghost || bg
        ? ''
        : surprisalAlpha(e.lp) > 0
          ? `background: rgba(217, 119, 6, ${surprisalAlpha(e.lp)})`
          : ''}
      onmouseenter={(ev) => enter(ev, i)}
      onmouseleave={leave}
      onclick={(ev) => clickTok(ev, i)}>{#if bg && !e.ghost}{@const p = wsSplit(e.t)}{p[0]}<span
          class="tok-core"
          style="background: {bg[i]}">{p[1]}</span>{p[2]}{:else}{e.t}{/if}</span>{/each}
</div>

{#if pinned != null && pinnedPos && tlp[pinned]}
  <TokenPopover entry={tlp[pinned]} x={pinnedPos.x} y={pinnedPos.y} {rules} pinned onPick={pick} onClose={unpin} replayed={loomCut != null && pinned < loomCut} />
{:else if cur && pos}
  <TokenPopover entry={cur} x={pos.x} y={pos.y} {rules} canPin={hover != null && canLoomAt(hover)} replayed={loomCut != null && hover != null && hover < loomCut} />
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
  .tok-core {
    border-radius: 2px;
    box-decoration-break: clone;
    -webkit-box-decoration-break: clone;
  }
  .tok-hover {
    outline: 1px solid var(--color-accent);
  }
  .tok-loom {
    cursor: pointer;
  }
  .tok-pinned {
    outline: 1.5px solid var(--color-accent);
  }
  /* Loom branch: the forced (replayed) prefix keeps its heat fill but wears a
     dotted underline — same "not drawn here" language as ghosts, minus the dim
     (these DO have numbers). Where it ends IS the fork point (an extra marker
     there added nothing — Clément 2026-08-06). */
  .tok-replayed {
    border-bottom: 1px dotted var(--color-accent);
  }
  /* Ghost = an edited turn's text past where it stopped being the model's.
     Dimmed + dashed so it reads as "text without a number", not as a normal
     token that happens to be untinted (p≈1 tokens are untinted too). */
  .tok-ghost {
    opacity: 0.55;
    border-bottom: 1px dashed var(--color-text-muted);
  }
</style>
