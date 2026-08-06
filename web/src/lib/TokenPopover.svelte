<script lang="ts">
  // The hover card for one token: its probability + the top-K alternatives as
  // mini bars. Shared by the two token-probability views — the raw stream
  // (TokenLogprobs) and the prose overlay (TokenHeatOverlay) — so a change to
  // what a token tells you lands in both at once. GHOST entries (an edited
  // turn's text past the divergence point — see token-edit.ts) are handled here
  // rather than per-view, for the same reason.
  //
  // Two modes. HOVER (default): informational, `pointer-events: none` so it
  // never steals the hover from the token under it. PINNED (the LOOM — the view
  // pins on click): interactive — every alternative becomes a button that
  // BRANCHES the turn from this position with that token swapped in, plus a
  // "resample from this token" row (drop the token, let the model redraw it).
  // Esc / outside-click close a pinned card; the view owns the pin state, this
  // component owns the closing gestures (once, for both views).
  //
  // position:FIXED for the same reason as ChatMessage's send-to menu: it lives
  // inside a panel's scroll container and absolute positioning would clip at
  // the column edge. The caller passes viewport coordinates.
  import type { TokenLogprob } from '$lib/tree';
  import type { HighlightRule } from '$lib/types';
  import { prob, pctLabel, displayToken, matchTintBackground } from '$lib/token-logprob';
  import { ruleMatches } from '$lib/highlight-match';

  let {
    entry,
    x,
    y,
    rules = [],
    pinned = false,
    replayed = false,
    canPin = false,
    onPick,
    onClose
  }: {
    entry: TokenLogprob;
    x: number;
    y: number;
    /** The ≤2 highlight rules coloring this view, if any — alternatives get a
     *  band for each rule their text matches. */
    rules?: HighlightRule[];
    /** Interactive (loom) mode: alternatives are branch buttons. */
    pinned?: boolean;
    /** This token sits in a loom branch's FORCED prefix (replayed, not drawn
     *  here) — shows the provenance line. Its numbers are real (re-scored). */
    replayed?: boolean;
    /** Hover mode only: this token CAN be pinned — show the affordance hint. */
    canPin?: boolean;
    /** Loom fire: the picked alternative's token id, or null = resample the
     *  pinned position itself. Only meaningful with `pinned`. */
    onPick?: (altTid: number | null) => void;
    /** Close a pinned card (Esc / outside click / after a pick). */
    onClose?: () => void;
  } = $props();

  let el = $state<HTMLDivElement | null>(null);

  // Closing gestures for the pinned card. Registered per-pin, capture-phase so
  // an Esc doesn't also clear the workspace's keyboard row focus. The pinning
  // click itself happened before this effect ran, so it can't self-close.
  $effect(() => {
    if (!pinned) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose?.();
      }
    };
    const onDown = (e: MouseEvent) => {
      if (el && !el.contains(e.target as Node)) onClose?.();
    };
    window.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onDown, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      document.removeEventListener('mousedown', onDown, true);
    };
  });

  /** Split-band background for one alternative: tinted by which selected rule(s)
   *  its text matches (binary → full tint per matched band). */
  function altBg(text: string): string {
    if (!rules.length) return '';
    return matchTintBackground(
      rules.filter((r) => ruleMatches(r, text)).map((r) => ({ color: r.color, prob: 1 }))
    );
  }
</script>

{#snippet altCells(alt: [string, number, number])}
  <code class="tok-alt-tok">{displayToken(alt[0])}</code>
  <div class="tok-alt-track">
    <div class="tok-alt-bar" style="width: {Math.max(1.5, (prob(alt[2]) ?? 0) * 100)}%"></div>
  </div>
  <span class="tok-alt-p">{pctLabel(alt[2])}</span>
{/snippet}

<div class="tok-pop" class:tok-pop-pinned={pinned} style="left: {x}px; top: {y}px" bind:this={el}>
  <div class="tok-pop-head">
    <code>{displayToken(entry.t)}</code>
    {#if !entry.ghost}<span class="tok-pop-p">{pctLabel(entry.lp)}</span>{/if}
  </div>
  {#if replayed && !entry.ghost}
    <div class="tok-pop-replayed">⑂ replayed — sampled on the fork's source branch</div>
  {/if}
  {#if entry.ghost}
    <!-- Text the model never sampled: the authored prefill of a continuation
         (ghostKind), or past the point where an edit left the model's text —
         hand-written, or half of a token the edit cut (the number was for the
         WHOLE token). Either way there is nothing honest to show. -->
    <div class="tok-alt-none">
      no token data — {entry.ghostKind === 'prefill' ? 'prefilled text' : 'edited text'}
    </div>
  {:else if entry.top?.length}
    <div class="tok-alts">
      {#each entry.top as alt (alt[1])}
        {#if pinned && onPick}
          <button
            class="tok-alt tok-alt-btn"
            class:tok-alt-sampled={alt[1] === entry.tid}
            style={altBg(alt[0]) ? `background: ${altBg(alt[0])}` : ''}
            onclick={() => onPick(alt[1])}
          >
            {@render altCells(alt)}
          </button>
        {:else}
          <div
            class="tok-alt"
            class:tok-alt-sampled={alt[1] === entry.tid}
            style={altBg(alt[0]) ? `background: ${altBg(alt[0])}` : ''}
          >
            {@render altCells(alt)}
          </div>
        {/if}
      {/each}
    </div>
  {:else}
    <div class="tok-alt-none">no alternatives captured for this token</div>
  {/if}
  {#if pinned && onPick && !entry.ghost}
    <button class="tok-resample" onclick={() => onPick(null)}>↺ resample from this token</button>
    <div class="tok-pop-hint">pick an alternative to branch from it · Esc closes</div>
  {:else if canPin && !pinned}
    <div class="tok-pop-hint">click the token to branch from it</div>
  {/if}
</div>

<style>
  .tok-pop {
    position: fixed;
    z-index: 95;
    width: 240px;
    padding: 7px 9px;
    background: var(--color-bg);
    border: 1px solid var(--color-border);
    border-radius: var(--radius);
    box-shadow: 0 4px 14px #00000022;
    pointer-events: none; /* never steals the hover from the token under it */
    font-size: 0.72rem;
  }
  .tok-pop-pinned {
    pointer-events: auto;
    border-color: var(--color-accent);
  }
  .tok-pop-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    margin-bottom: 5px;
  }
  .tok-pop-head code {
    font-weight: 700;
    color: var(--color-text);
    overflow-wrap: anywhere;
  }
  .tok-pop-p {
    color: var(--color-text-secondary);
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
  }
  .tok-alts {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .tok-alt {
    display: grid;
    grid-template-columns: minmax(40px, auto) 1fr 42px;
    align-items: center;
    gap: 6px;
    /* padding + offsetting margin so a match tint gets rounded breathing room
       without nudging the row layout */
    padding: 1px 3px;
    margin: 0 -3px;
    border-radius: 3px;
  }
  /* Alternatives as loom buttons: same grid, plus button-chrome reset + hover. */
  .tok-alt-btn {
    border: none;
    font: inherit;
    text-align: left;
    color: inherit;
    cursor: pointer;
    width: calc(100% + 6px);
    background: transparent;
  }
  .tok-alt-btn:hover {
    outline: 1px solid var(--color-accent);
  }
  .tok-alt-tok {
    color: var(--color-text-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tok-alt-sampled .tok-alt-tok {
    color: var(--color-accent);
    font-weight: 700;
  }
  .tok-alt-track {
    height: 7px;
    border-radius: 3px;
    background: var(--color-border-light, var(--color-border));
    overflow: hidden;
  }
  .tok-alt-bar {
    height: 100%;
    border-radius: 3px;
    background: var(--color-accent);
    opacity: 0.75;
  }
  .tok-alt-sampled .tok-alt-bar {
    opacity: 1;
  }
  .tok-alt-p {
    text-align: right;
    color: var(--color-text-muted);
    font-variant-numeric: tabular-nums;
  }
  .tok-alt-none {
    color: var(--color-text-muted);
    font-style: italic;
  }
  .tok-resample {
    display: block;
    width: 100%;
    margin-top: 5px;
    padding: 2px 4px;
    border: 1px dashed var(--color-border);
    border-radius: 3px;
    background: transparent;
    color: var(--color-text-secondary);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .tok-resample:hover {
    outline: 1px solid var(--color-accent);
  }
  .tok-pop-hint {
    margin-top: 4px;
    color: var(--color-text-muted);
    font-size: 0.66rem;
    font-style: italic;
  }
  .tok-pop-replayed {
    margin: -3px 0 5px;
    color: var(--color-accent);
    font-size: 0.66rem;
  }
</style>
