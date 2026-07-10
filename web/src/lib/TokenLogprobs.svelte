<script lang="ts">
	// The token-logprob inspector body: the RAW generated token stream (thinking
	// tags and all — deliberately NOT the markdown render, so token boundaries
	// are exact), each token tinted by surprisal, hover → a popover with the
	// token's probability + the top-K alternatives as mini bars.
	//
	// The popover is position:FIXED for the same reason as ChatMessage's
	// send-to-menu: it lives inside the panel's scroll container and absolute
	// positioning would clip at the column edge.
	import type { TokenLogprob } from '$lib/tree';
	import { prob, pctLabel, surprisalAlpha, displayToken } from '$lib/token-logprob';

	let { tlp }: { tlp: TokenLogprob[] } = $props();

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
			style={surprisalAlpha(e.lp) > 0 ? `background: rgba(217, 119, 6, ${surprisalAlpha(e.lp)})` : ''}
			onmouseenter={(ev) => enter(ev, i)}
			onmouseleave={leave}>{e.t}</span>{/each}
</div>

{#if cur && pos}
	<div class="tok-pop" style="left: {pos.x}px; top: {pos.y}px">
		<div class="tok-pop-head">
			<code>{displayToken(cur.t)}</code>
			<span class="tok-pop-p">{pctLabel(cur.lp)}</span>
		</div>
		{#if cur.top?.length}
			<div class="tok-alts">
				{#each cur.top as alt (alt[1])}
					<div class="tok-alt" class:tok-alt-sampled={alt[1] === cur.tid}>
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
