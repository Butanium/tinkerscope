<script lang="ts">
  // The hover card for one token: its probability + the top-K alternatives as
  // mini bars. Shared by the two token-probability views — the raw stream
  // (TokenLogprobs) and the prose overlay (TokenHeatOverlay) — so a change to
  // what a token tells you lands in both at once. GHOST entries (an edited
  // turn's text past the divergence point — see token-edit.ts) are handled here
  // rather than per-view, for the same reason.
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
    rules = []
  }: {
    entry: TokenLogprob;
    x: number;
    y: number;
    /** The ≤2 highlight rules coloring this view, if any — alternatives get a
     *  band for each rule their text matches. */
    rules?: HighlightRule[];
  } = $props();

  /** Split-band background for one alternative: tinted by which selected rule(s)
   *  its text matches (binary → full tint per matched band). */
  function altBg(text: string): string {
    if (!rules.length) return '';
    return matchTintBackground(
      rules.filter((r) => ruleMatches(r, text)).map((r) => ({ color: r.color, prob: 1 }))
    );
  }
</script>

<div class="tok-pop" style="left: {x}px; top: {y}px">
  <div class="tok-pop-head">
    <code>{displayToken(entry.t)}</code>
    {#if !entry.ghost}<span class="tok-pop-p">{pctLabel(entry.lp)}</span>{/if}
  </div>
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
        <div
          class="tok-alt"
          class:tok-alt-sampled={alt[1] === entry.tid}
          style={altBg(alt[0]) ? `background: ${altBg(alt[0])}` : ''}
        >
          <code class="tok-alt-tok">{displayToken(alt[0])}</code>
          <div class="tok-alt-track">
            <div class="tok-alt-bar" style="width: {Math.max(1.5, (prob(alt[2]) ?? 0) * 100)}%"></div>
          </div>
          <span class="tok-alt-p">{pctLabel(alt[2])}</span>
        </div>
      {/each}
    </div>
  {:else}
    <div class="tok-alt-none">no alternatives captured for this token</div>
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
</style>
