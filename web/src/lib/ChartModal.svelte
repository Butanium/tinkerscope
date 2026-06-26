<!--
  Response-distribution chart modal: a stacked-bar SVG of each panel's answer
  fractions + a colour legend. Pure display — the parent computes `data` (via
  buildChartData → computeChartBars) and passes it in. Bar geometry is local
  {@const}s; the histogram math lives in $lib/chart.
-->
<script lang="ts">
	import Modal from './Modal.svelte';
	import { wrapLabel, type ChartData } from './chart';

	let { data, onclose }: { data: ChartData | null; onclose: () => void } = $props();
</script>

<Modal title="Response Distribution" {onclose} modalStyle="width: 800px; max-width: 95vw;">
	{#if data}
		<p class="chart-question">{data.question.length > 200 ? '...' + data.question.slice(-200) : data.question}</p>
		{@const barWidth = 80}
		{@const barGap = 60}
		{@const chartHeight = 300}
		{@const leftPad = 45}
		{@const bottomPad = 100}
		{@const topPad = 10}
		{@const totalWidth = leftPad + data.bars.length * (barWidth + barGap)}
		{@const totalHeight = chartHeight + bottomPad + topPad}
		<svg class="chart-svg" viewBox="0 0 {totalWidth} {totalHeight}" width="100%" preserveAspectRatio="xMidYMid meet">
			{#each [0, 25, 50, 75, 100] as tick (tick)}
				{@const y = topPad + chartHeight - (tick / 100) * chartHeight}
				<line x1={leftPad} y1={y} x2={totalWidth} y2={y} stroke="var(--color-border)" stroke-width="0.5" />
				<text x={leftPad - 6} y={y + 4} text-anchor="end" fill="var(--color-text-muted)" font-size="11">{tick}%</text>
			{/each}
			{#each data.bars as bar, bi (bi)}
				{@const x = leftPad + bi * (barWidth + barGap) + barGap / 2}
				{#each bar.segments as seg, si (si)}
					{@const prevPct = bar.segments.slice(0, si).reduce((sum, v) => sum + v.pct, 0)}
					{@const y = topPad + chartHeight - ((prevPct + seg.pct) / 100) * chartHeight}
					{@const h = (seg.pct / 100) * chartHeight}
					{#if h > 0}
						<rect {x} {y} width={barWidth} height={h} fill={seg.color} rx="1" />
						{#if h > 14}
							<text x={x + barWidth / 2} y={y + h / 2 + 4} text-anchor="middle" fill="white" font-size="10" font-weight="600">{seg.pct.toFixed(0)}%</text>
						{/if}
					{/if}
				{/each}
				<text x={x + barWidth / 2} y={topPad + chartHeight + 14} text-anchor="middle" fill="var(--color-text)" font-size="11" font-weight="500">
					{#each wrapLabel(bar.model) as line, li (li)}
						<tspan x={x + barWidth / 2} dy={li === 0 ? 0 : 13}>{line}</tspan>
					{/each}
				</text>
			{/each}
		</svg>
		<div class="chart-legend">
			{#each data.answers as answer (answer)}
				<div class="chart-legend-item">
					<span class="chart-legend-swatch" style="background: {data.colors[answer]}"></span>
					<span class="chart-legend-label">{answer}</span>
				</div>
			{/each}
		</div>
	{:else}
		<div class="backend-error">No response data to chart. Send a message first (use Samples &gt; 1 for best results).</div>
	{/if}
</Modal>

<style>
	.chart-question { font-size: 0.82rem; color: var(--color-text-muted); font-style: italic; margin-bottom: var(--space-4); padding: var(--space-2) var(--space-3); background: var(--color-bg); border-radius: var(--radius); border: 1px solid var(--color-border-light); }
	.chart-svg { display: block; max-height: 400px; }
	.chart-svg text { font-family: var(--font-sans); }
	.chart-legend { display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-4); margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); }
	.chart-legend-item { display: flex; align-items: center; gap: var(--space-1); }
	.chart-legend-swatch { width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; }
	.chart-legend-label { font-size: 0.78rem; color: var(--color-text); }
</style>
