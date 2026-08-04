<script lang="ts">
  // Token probabilities WITHOUT giving up the prose: instead of swapping the
  // rendered markdown for a monospace token dump (TokenLogprobs), this paints
  // the same per-token heat as a layer underneath the normal message body, and
  // keeps the hover card.
  //
  // How it stays aligned: the renderer's output text is collected from the DOM
  // (`selector` names the containers that hold it — the reasoning block and the
  // content div, in that order, which is the order the raw stream produced
  // them), the raw token stream is aligned against it by `lib/token-align`, and
  // each token's span is turned into a Range whose client rects are painted.
  // Everything is measured, nothing is guessed: a token the aligner can't place
  // is simply not painted.
  //
  // A CANVAS rather than a few hundred absolutely-positioned divs — a long turn
  // is ~1000 tokens and this repaints on every resize.
  //
  // ONE CANVAS PER CONTAINER, inserted into the container itself at
  // `z-index: -1`, rather than one covering the whole row. Two reasons, both
  // discovered the hard way against real turns:
  //   - `.sample-reasoning` has an OPAQUE background, so a row-level canvas
  //     behind it painted nothing you could see — the whole thinking block came
  //     out flat. A negative-z child paints after its own container's
  //     background and before its text, which is exactly the highlighter order.
  //   - `.sample-reasoning` also scrolls (`overflow-y: auto`). An absolutely
  //     positioned child of a scroll container scrolls WITH the content and is
  //     clipped by it, so the tint tracks the text for free — no scroll
  //     listener, no manual clipping.
  // Both containers set `position: relative; z-index: 1` in chat.css, which is
  // what confines the negative-z canvas to its own stacking context.
  import { onMount, tick } from 'svelte';
  import type { TokenLogprob } from '$lib/tree';
  import { alignTokens, visibleCoverage } from '$lib/token-align';
  import { highlightMatchProb, tokenTintColors } from '$lib/token-logprob';
  import { logprobHighlight } from '$lib/logprobs.svelte';
  import { colorRules } from '$lib/highlights.svelte';
  import TokenPopover from '$lib/TokenPopover.svelte';

  let {
    tlp,
    selector
  }: {
    tlp: TokenLogprob[];
    /** Containers holding the rendered text this stream produced, in stream
     *  order (reasoning before content). */
    selector: string;
  } = $props();

  /** Below this share of the rendered text claimed by some token, the alignment
   *  is not worth trusting — show the "couldn't line up" note instead of a
   *  half-painted lie. See `visibleCoverage` for why it's measured this way. */
  const MIN_ALIGNED = 0.5;

  /** Vertical share of a line box the tint keeps, centred. Range rects are line
   *  boxes, so painting them whole makes solid slabs with no gap between lines;
   *  trimming reads as marker over text. */
  const BAND_HEIGHT = 0.78;

  type Box = { i: number; c: number; x: number; y: number; w: number; h: number };

  let root = $state<HTMLDivElement | null>(null);
  /** Per-token rects, in their container's CONTENT coordinates (so they stay
   *  correct while that container scrolls). One token can wrap → several. */
  let boxes: Box[] = [];
  let quality = $state(1);
  let hover = $state<number | null>(null);
  let pop = $state<{ x: number; y: number } | null>(null);
  /** Containers we've put a canvas in, so they can be cleaned up on destroy. */
  let painted: Element[] = [];
  /** The container list `boxes` was measured against — reused by hover so a
   *  mousemove costs no querySelectorAll. Re-derived on every measure; a stale
   *  entry (an `{@html}` swap replaced the node) is caught by `isConnected`. */
  let measured: HTMLElement[] = [];

  // The ≤2 highlight rules chosen for match-coloring, resolved by id (a
  // rename/recolor keeps applying, a deleted rule drops out). Same contract as
  // TokenLogprobs — the two views must not disagree about what a color means.
  const rules = $derived(
    logprobHighlight.activeIds
      .map((id) => colorRules().find((r) => r.id === id))
      .filter((r) => r != null)
  );

  /** Colors for each token, top-to-bottom band order. A GHOST (an edited turn's
   *  text past the divergence point — token-edit.ts) has no probability, so it
   *  gets no fill; it's marked with a dashed underline instead, below. */
  const colors = $derived(
    tlp.map((e) =>
      e.ghost
        ? []
        : tokenTintColors(
            e.lp,
            rules.map((r) => ({ color: r.color, prob: highlightMatchProb(e, r) })),
            logprobHighlight.sharpness
          )
    )
  );

  type Collected = {
    nodes: Text[];
    text: string;
    /** offset of nodes[k] within `text` */
    base: number[];
    /** index into `containers` for nodes[k] */
    owner: number[];
    containers: HTMLElement[];
  };

  /** Text nodes of the prose containers, in document order, skipping KaTeX —
   *  its subtree duplicates the formula (MathML annotation + rendered glyphs),
   *  so including it would desync the aligner for the rest of the message. Our
   *  own canvases hold no text, so they need no exclusion. */
  function collect(host: Element): Collected {
    const nodes: Text[] = [];
    const base: number[] = [];
    const owner: number[] = [];
    const containers = [...host.querySelectorAll<HTMLElement>(selector)];
    let text = '';
    containers.forEach((container, ci) => {
      const walk = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
        acceptNode: (n) =>
          n.parentElement?.closest('.katex') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
      });
      for (let n = walk.nextNode(); n; n = walk.nextNode()) {
        const t = n as Text;
        if (!t.data) continue;
        nodes.push(t);
        base.push(text.length);
        owner.push(ci);
        text += t.data;
      }
    });
    return { nodes, text, base, owner, containers };
  }

  /** The container's canvas, created (and re-created after an `{@html}` swap
   *  wiped it) as its first child. */
  function canvasFor(container: HTMLElement): HTMLCanvasElement {
    const found = container.firstElementChild;
    if (found instanceof HTMLCanvasElement && found.classList.contains('tok-heat-canvas')) {
      return found;
    }
    const c = document.createElement('canvas');
    c.className = 'tok-heat-canvas';
    c.setAttribute('aria-hidden', 'true');
    container.prepend(c);
    if (!painted.includes(container)) painted.push(container);
    return c;
  }

  function dropCanvases(): void {
    for (const container of painted) {
      const c = container.firstElementChild;
      if (c instanceof HTMLCanvasElement && c.classList.contains('tok-heat-canvas')) c.remove();
    }
    painted = [];
  }

  /** Last node whose start offset is ≤ pos (base[] is ascending). */
  function nodeAt(base: number[], pos: number): number {
    let lo = 0;
    let hi = base.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (base[mid] <= pos) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }

  /** Viewport point of a container's content origin — where an absolutely
   *  positioned `top:0; left:0` child of it lands. That's the padding-box
   *  corner, shifted by however far the container is scrolled. */
  function originOf(el: HTMLElement): { x: number; y: number } {
    const r = el.getBoundingClientRect();
    return { x: r.left + el.clientLeft - el.scrollLeft, y: r.top + el.clientTop - el.scrollTop };
  }

  function measure(): void {
    const host = root?.parentElement;
    if (!host) return;
    const { nodes, text, base, owner, containers } = collect(host);
    if (!nodes.length || !tlp.length) {
      boxes = [];
      measured = [];
      quality = 0;
      dropCanvases();
      return;
    }
    const spans = alignTokens(
      tlp.map((e) => e.t),
      text
    );
    quality = visibleCoverage(spans, text.length);
    if (quality < MIN_ALIGNED) {
      boxes = [];
      measured = [];
      dropCanvases();
      return;
    }
    const origins = containers.map(originOf);
    const next: Box[] = [];
    const range = document.createRange();
    for (let i = 0; i < spans.length; i++) {
      const s = spans[i];
      if (!s || (!colors[i]?.length && !tlp[i]?.ghost)) continue;
      let { start, end } = s;
      // Match coloring trims the token's edge whitespace (a BPE token carries
      // its leading space; tinting it reads as highlighting the gap between
      // words). The surprisal heat keeps whole tokens — it's a continuous
      // ribbon, not a highlight.
      if (rules.length && !tlp[i]?.ghost) {
        while (start < end && /\s/.test(text[start])) start++;
        while (end > start && /\s/.test(text[end - 1])) end--;
        if (start >= end) continue; // all-whitespace token — nothing to tint
      }
      const a = nodeAt(base, start);
      const b = nodeAt(base, end - 1);
      if (owner[a] !== owner[b]) continue; // straddles two containers — skip
      range.setStart(nodes[a], Math.min(start - base[a], nodes[a].data.length));
      range.setEnd(nodes[b], Math.min(end - base[b], nodes[b].data.length));
      const o = origins[owner[a]];
      for (const r of range.getClientRects()) {
        if (r.width <= 0 || r.height <= 0) continue; // collapsed <details>
        const trim = (r.height * (1 - BAND_HEIGHT)) / 2;
        next.push({
          i,
          c: owner[a],
          x: r.left - o.x,
          y: r.top - o.y + trim,
          w: r.width,
          h: r.height - trim * 2
        });
      }
    }
    boxes = next;
    measured = containers;
    paint(containers);
  }

  function paint(containers: HTMLElement[]): void {
    const dpr = window.devicePixelRatio || 1;
    containers.forEach((container, ci) => {
      const c = canvasFor(container);
      const w = container.scrollWidth;
      const h = container.scrollHeight;
      const pw = Math.max(1, Math.round(w * dpr));
      const ph = Math.max(1, Math.round(h * dpr));
      // Assigning width/height reallocates the bitmap; hover repaints on every
      // mousemove, so only pay it when the size really changed.
      if (c.width !== pw || c.height !== ph) {
        c.width = pw;
        c.height = ph;
        c.style.width = `${w}px`;
        c.style.height = `${h}px`;
      }
      const ctx = c.getContext('2d');
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      for (const b of boxes) {
        if (b.c !== ci) continue;
        // Ghost: a dashed underline, no fill — the same "text without a number"
        // affordance the raw stream draws with .tok-ghost. An untinted span
        // would read as a CONFIDENT token, which is the opposite of the truth.
        if (tlp[b.i]?.ghost) {
          ctx.save();
          ctx.strokeStyle = 'rgba(130, 130, 130, 0.75)';
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 2]);
          ctx.beginPath();
          ctx.moveTo(b.x, Math.round(b.y + b.h) - 0.5);
          ctx.lineTo(b.x + b.w, Math.round(b.y + b.h) - 0.5);
          ctx.stroke();
          ctx.restore();
          continue;
        }
        const bands = colors[b.i];
        const bh = b.h / bands.length;
        for (let k = 0; k < bands.length; k++) {
          if (!bands[k]) continue;
          ctx.fillStyle = bands[k];
          ctx.fillRect(b.x, b.y + k * bh, b.w, bh);
        }
        if (hover === b.i) {
          ctx.strokeStyle = 'rgba(120, 120, 120, 0.85)';
          ctx.lineWidth = 1;
          ctx.strokeRect(b.x + 0.5, b.y + 0.5, b.w - 1, b.h - 1);
        }
      }
    });
  }

  let frame = 0;
  function schedule(): void {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      measure();
    });
  }

  function onMove(ev: MouseEvent): void {
    const containers = measured;
    let found: number | null = null;
    for (let ci = 0; ci < containers.length && found == null; ci++) {
      const el = containers[ci];
      if (!el.isConnected) continue;
      const visible = el.getBoundingClientRect();
      // Inside the container's VISIBLE box first: a scrolled-away box must not
      // answer for a pointer that is nowhere near it.
      if (
        ev.clientX < visible.left ||
        ev.clientX > visible.right ||
        ev.clientY < visible.top ||
        ev.clientY > visible.bottom
      )
        continue;
      const o = originOf(el);
      const px = ev.clientX - o.x;
      const py = ev.clientY - o.y;
      for (const b of boxes) {
        if (b.c !== ci) continue;
        if (px >= b.x && px < b.x + b.w && py >= b.y && py < b.y + b.h) {
          found = b.i;
          break;
        }
      }
    }
    if (found === hover) return;
    hover = found;
    pop =
      found == null
        ? null
        : { x: Math.max(4, Math.min(ev.clientX, window.innerWidth - 260)), y: ev.clientY + 14 };
    paint(containers);
  }

  function onLeave(): void {
    if (hover == null) return;
    hover = null;
    pop = null;
    schedule();
  }

  // Re-measure whenever the data, the coloring, or the box changes. The prose
  // is `{@html}`-rendered by the parent, so a tick + rAF puts us after both
  // Svelte's DOM write and the browser's layout.
  $effect(() => {
    void tlp;
    void colors;
    void selector;
    void tick().then(schedule);
  });

  onMount(() => {
    const host = root?.parentElement;
    if (!host) return;
    const ro = new ResizeObserver(schedule);
    ro.observe(host);
    // A collapsed <details> has no rects; opening one changes the host height,
    // which the ResizeObserver already catches — but the toggle can also fire
    // without a size change when the fold is the last thing in the row.
    host.addEventListener('toggle', schedule, true);
    host.addEventListener('mousemove', onMove);
    host.addEventListener('mouseleave', onLeave);
    schedule();
    return () => {
      ro.disconnect();
      host.removeEventListener('toggle', schedule, true);
      host.removeEventListener('mousemove', onMove);
      host.removeEventListener('mouseleave', onLeave);
      if (frame) cancelAnimationFrame(frame);
      dropCanvases();
    };
  });
</script>

<div class="tok-heat" bind:this={root} data-aligned={quality.toFixed(2)} aria-hidden="true"></div>

{#if quality < MIN_ALIGNED && tlp.length}
  <div class="tok-heat-warn" data-testid="tok-overlay-unaligned">
    token probabilities couldn't be lined up with the rendered text — switch to Tokens
  </div>
{/if}

<!-- `tlp[hover]` guarded, not assumed: the array is re-derived while a turn
     streams, so a held hover index can outlive its entry for a frame. -->
{#if hover != null && pop && tlp[hover]}
  <TokenPopover entry={tlp[hover]} x={pop.x} y={pop.y} {rules} />
{/if}

<style>
  /* Zero-size mount anchor: the painting happens in canvases this component
     inserts into the prose containers themselves (see the header comment). */
  .tok-heat {
    display: none;
  }
  .tok-heat-warn {
    font-size: 0.68rem;
    font-style: italic;
    color: var(--color-text-muted);
    margin-top: var(--space-2);
  }
</style>
