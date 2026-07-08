<!--
  Response-distribution chart modal. The parent hands per-panel, per-assistant-
  turn sample lists (ChartPanelData[], reactive — it live-updates while a batch
  streams); everything else is owned here:

  - turn picker — which assistant turn to chart; defaults to the LATEST turn
    (tracks the newest turn while `Latest` is selected).
  - two bucketing modes (math in $lib/chart):
      · "highlight rules" (default when any assistant-scoped rule is enabled):
        each sample is bucketed by the SET of rules it matches — grey = none,
        solid = one rule, striped = a multi-rule combo.
      · "exact answers": the legacy trimmed string-equality histogram (an
        empty answer buckets as [NO ANSWER], not dropped).
  - per-rule chart toggles (rules mode) — a chip per applicable rule; clicking
    one excludes it from the BUCKETING (not from chat painting): a rule the
    prompt makes ubiquitous ("smoking" when the question is about smoking)
    stripes every bucket and drowns the signal. Exclusions are chart-only,
    session-scoped (module state — they survive close/reopen, not a reload).
  - match scope (rules mode) — which text the rules run against: response /
    thinking / either, or "split" = a response|thinking bar pair per model,
    adjacent under ONE model name (bucketed separately over the same samples).
  - folded (reduced) panels are excluded by default; an "include folded
    panels" toggle appears when any are present.
  - the charted prompt(s) above the chart — grouped per distinct prompt, since
    panels can diverge (each shows which models it belongs to).
  - click a segment → inspect its samples below the chart, rendered through the
    normal highlight pipeline so the matched patterns are painted (the thinking
    fold auto-opens when the scope involves thinking).
-->
<script lang="ts" module>
	// Rule ids excluded from the chart's bucketing. Module-scoped so the choice
	// survives the modal's destroy-on-close; deliberately NOT persisted — it's a
	// per-question viewing tweak, not a property of the rule.
	let chartOff = $state<string[]>([]);
</script>

<script lang="ts">
	import Modal from './Modal.svelte';
	import {
		chartByAnswers,
		chartByRules,
		chartRules,
		contrastText,
		NONE_COLOR,
		wrapLabel,
		type ChartPanelData,
		type ChartSource,
		type MatchScope
	} from './chart';
	import { highlightStore } from './highlights.svelte';
	import { renderContent } from './render';
	import { tip } from '$lib/tooltip.svelte';

	let { sources, onclose }: { sources: ChartPanelData[]; onclose: () => void } = $props();

	// ── controls ──────────────────────────────────────────────────────
	let mode = $state<'rules' | 'answers'>(
		chartRules(highlightStore.rules).length > 0 ? 'rules' : 'answers'
	);
	let turnSel = $state('last'); // 'last' | stringified turn index
	// What the rules match against; 'split' = response|thinking as adjacent bars.
	let matchScope = $state<MatchScope | 'split'>('response');
	// Folded (reduced) panels are excluded by default; a toggle brings them in.
	let includeFolded = $state(false);
	let inspect = $state<{ bar: number; key: string } | null>(null);

	// ── per-rule chart toggles (rules mode) ───────────────────────────
	// The rules that could bucket assistant samples — each gets an on/off chip.
	const applicableRules = $derived(chartRules(highlightStore.rules));
	const activeRules = $derived(highlightStore.rules.filter((r) => !chartOff.includes(r.id)));
	const allRulesOff = $derived(
		applicableRules.length > 0 && applicableRules.every((r) => chartOff.includes(r.id))
	);
	function toggleRule(id: string) {
		chartOff = chartOff.includes(id) ? chartOff.filter((x) => x !== id) : [...chartOff, id];
		// Bucket keys are positional in the active-rule list — they all shift
		// when the set changes, so a kept inspect selection would silently point
		// at a different bucket.
		inspect = null;
	}

	const hasFolded = $derived(sources.some((s) => s.folded));
	const activeSources = $derived(sources.filter((s) => includeFolded || !s.folded));

	const turnCount = $derived(Math.max(0, ...activeSources.map((s) => s.turns.length)));
	const turnIdx = $derived(turnSel === 'last' ? -1 : Number(turnSel));

	/** The picked turn of one panel ('last' → its own newest turn). */
	function pickTurn(s: ChartPanelData) {
		return turnIdx < 0 ? s.turns[s.turns.length - 1] : s.turns[turnIdx];
	}

	/** Question preview for the turn picker (first panel that has that turn). */
	function turnLabel(k: number): string {
		for (const s of activeSources) {
			const t = s.turns[k];
			if (!t) continue;
			const q = t.question.replace(/\s+/g, ' ').trim();
			const head = q.length > 48 ? q.slice(0, 47) + '…' : q;
			return `Turn ${k + 1}: ${head}${t.streaming ? ' (streaming…)' : ''}`;
		}
		return `Turn ${k + 1}`;
	}

	// Bars index-align with chartSources (so inspect can find the sample text).
	// In split scope each panel expands to a (response, thinking) bar pair over
	// the SAME samples array — the thinking bar only when any sample has CoT.
	// The pair shares the model name and differs by `sub`, which the layout
	// below renders as adjacent bars under one label.
	const chartSources = $derived.by(() => {
		const picked = activeSources
			.map((s) => ({ model: s.model, samples: pickTurn(s)?.samples ?? [] }))
			.filter((s): s is ChartSource => s.samples.length > 0);
		if (mode !== 'rules' || matchScope !== 'split') return picked;
		return picked.flatMap((s) => {
			const pair: ChartSource[] = [{ model: s.model, samples: s.samples, matchOn: 'response', sub: 'response' }];
			if (s.samples.some((x) => x.reasoning))
				pair.push({ model: s.model, samples: s.samples, matchOn: 'thinking', sub: 'thinking' });
			return pair;
		});
	});
	/** Charted prompt(s), grouped — panels can diverge on what was asked. */
	const questionGroups = $derived.by(() => {
		const groups: { q: string; models: string[] }[] = [];
		for (const s of activeSources) {
			const t = pickTurn(s);
			if (!t) continue;
			const g = groups.find((g) => g.q === t.question);
			if (g) g.models.push(s.model);
			else groups.push({ q: t.question, models: [s.model] });
		}
		return groups;
	});
	const streaming = $derived(activeSources.some((s) => pickTurn(s)?.streaming));
	function truncQ(q: string): string {
		return q.length > 200 ? '…' + q.slice(-200) : q;
	}

	const data = $derived(
		mode === 'rules'
			? chartByRules(chartSources, activeRules, matchScope === 'split' ? 'response' : matchScope)
			: chartByAnswers(chartSources)
	);

	// ── SVG layout ────────────────────────────────────────────────────
	// Consecutive sub-labeled bars sharing a model (split's response|thinking
	// pair) form one GROUP: adjacent bars, one model name centered under them.
	const CHART_H = 300, TOP_PAD = 10, LEFT_PAD = 45, GROUP_GAP = 60, PAIR_GAP = 8;
	type PlacedBar = { bar: import('./chart').ChartBar; bi: number; x: number };
	const layout = $derived.by(() => {
		if (!data) return null;
		const groups: { model: string; total: number; center: number; bars: PlacedBar[] }[] = [];
		for (let bi = 0; bi < data.bars.length; bi++) {
			const bar = data.bars[bi];
			const g = groups[groups.length - 1];
			if (g && g.model === bar.model && bar.sub && g.bars[0].bar.sub) g.bars.push({ bar, bi, x: 0 });
			else groups.push({ model: bar.model, total: bar.total, center: 0, bars: [{ bar, bi, x: 0 }] });
		}
		const hasSub = groups.some((g) => g.bars.some((b) => b.bar.sub));
		const bw = hasSub ? 56 : 80; // pairs get slimmer bars
		let x = LEFT_PAD;
		for (const g of groups) {
			x += GROUP_GAP / 2;
			g.bars.forEach((b, i) => {
				b.x = x;
				x += bw + (i < g.bars.length - 1 ? PAIR_GAP : 0);
			});
			g.center = (g.bars[0].x + g.bars[g.bars.length - 1].x + bw) / 2;
			x += GROUP_GAP / 2;
		}
		// Sub labels sit between the bars and the model name → deeper bottom pad.
		const bottomPad = hasSub ? 124 : 110;
		return { groups, bw, hasSub, width: x, height: TOP_PAD + CHART_H + bottomPad };
	});

	// ── inspect (click a segment) ─────────────────────────────────────
	function toggleInspect(bar: number, key: string) {
		inspect = inspect && inspect.bar === bar && inspect.key === key ? null : { bar, key };
	}
	const inspected = $derived.by(() => {
		if (!inspect || !data) return null;
		const bar = data.bars[inspect.bar];
		const seg = bar?.segments.find((s) => s.key === inspect!.key);
		const src = chartSources[inspect.bar];
		if (!bar || !seg || !src || seg.count === 0) return null;
		// The scope this bar was matched under — the inspector auto-opens the
		// thinking fold when the match could live there.
		const scope: MatchScope =
			mode !== 'rules' ? 'response'
			: (src.matchOn ?? (matchScope === 'split' ? 'response' : matchScope));
		return { bar, seg, scope, samples: seg.sampleIdx.map((i) => src.samples[i]).filter(Boolean) };
	});

	/** Legend/inspect swatch background — stripes for multi-rule combos. */
	function swatchStyle(colors: string[]): string {
		if (colors.length === 0) return `background: ${NONE_COLOR}`;
		if (colors.length === 1) return `background: ${colors[0]}`;
		const stops = colors.map((c, i) => `${c} ${i * 5}px ${(i + 1) * 5}px`).join(', ');
		return `background: repeating-linear-gradient(45deg, ${stops})`;
	}

	function segFill(colors: string[], legendIdx: number): string {
		if (colors.length === 0) return NONE_COLOR;
		if (colors.length === 1) return colors[0];
		return `url(#hlseg-${legendIdx})`;
	}
</script>

<Modal title="Response Distribution" {onclose} modalStyle="width: 860px; max-width: 95vw;">
	{#if sources.length === 0}
		<div class="backend-error">No response data to chart. Send a message first (use Samples &gt; 1 for best results).</div>
	{:else}
		<div class="chart-controls">
			<div class="chart-mode" role="group" aria-label="Bucketing mode">
				<button class="chart-mode-btn" class:active={mode === 'rules'} onclick={() => (mode = 'rules')}
					data-tooltip="Bucket samples by which highlight rules match them" use:tip>highlight rules</button>
				<button class="chart-mode-btn" class:active={mode === 'answers'} onclick={() => (mode = 'answers')}
					data-tooltip="Bucket samples by exact answer text (short constrained answers)" use:tip>exact answers</button>
			</div>
			{#if turnCount > 1}
				<select class="chart-turn" bind:value={turnSel} aria-label="Charted turn">
					<option value="last">Latest turn</option>
					{#each Array(turnCount) as _, k (k)}
						<option value={String(k)}>{turnLabel(k)}</option>
					{/each}
				</select>
			{/if}
			{#if mode === 'rules'}
				<div class="chart-mode" role="group" aria-label="Match scope">
					<button class="chart-mode-btn" class:active={matchScope === 'response'} onclick={() => (matchScope = 'response')}
						data-tooltip="Match rules against the response text only" use:tip>response</button>
					<button class="chart-mode-btn" class:active={matchScope === 'thinking'} onclick={() => (matchScope = 'thinking')}
						data-tooltip="Match rules against the thinking/CoT only" use:tip>thinking</button>
					<button class="chart-mode-btn" class:active={matchScope === 'either'} onclick={() => (matchScope = 'either')}
						data-tooltip="Match rules against thinking + response combined" use:tip>either</button>
					<button class="chart-mode-btn" class:active={matchScope === 'split'} onclick={() => (matchScope = 'split')}
						data-tooltip="Two adjacent bars per model — response and thinking, matched separately" use:tip>split</button>
				</div>
			{/if}
			{#if hasFolded}
				<label class="chart-check" data-tooltip="Folded panels are excluded from the chart by default" use:tip>
					<input type="checkbox" bind:checked={includeFolded} /> include folded panels
				</label>
			{/if}
		</div>
		{#if mode === 'rules' && applicableRules.length > 0}
			<div class="chart-rules" role="group" aria-label="Rules included in the chart">
				{#each applicableRules as r (r.id)}
					{@const off = chartOff.includes(r.id)}
					<button
						class="chart-rule-chip"
						class:off
						aria-pressed={!off}
						data-tooltip={off
							? 'Excluded from the chart — click to re-include'
							: 'Click to exclude this rule from the bucketing (useful when it matches every sample)'}
						use:tip
						onclick={() => toggleRule(r.id)}
					>
						<span class="chart-rule-swatch" style="background: {r.color}"></span>{r.name}
					</button>
				{/each}
			</div>
		{/if}
		{#if questionGroups.length === 1}
			<p class="chart-question">{truncQ(questionGroups[0].q)}{streaming ? ' · streaming…' : ''}</p>
		{:else if questionGroups.length > 1}
			<div class="chart-question">
				{#each questionGroups as g (g.q)}
					<p><span class="chart-q-models">{g.models.join(', ')}:</span> {truncQ(g.q)}</p>
				{/each}
				{#if streaming}<p>streaming…</p>{/if}
			</div>
		{/if}
		{#if !data || !layout}
			{#if activeSources.length === 0}
				<div class="backend-error">All panels with data are folded — enable “include folded panels” above to chart them.</div>
			{:else if mode === 'rules' && allRulesOff}
				<div class="backend-error">Every highlight rule is toggled off for this chart — click a rule chip above to re-include it.</div>
			{:else if mode === 'rules'}
				<div class="backend-error">No enabled highlight rules apply to assistant turns — add rules in the sidebar's Highlights panel, or switch to “exact answers”.</div>
			{:else}
				<div class="backend-error">No response data to chart for this turn.</div>
			{/if}
		{:else}
			<svg class="chart-svg" viewBox="0 0 {layout.width} {layout.height}" width="100%" preserveAspectRatio="xMidYMid meet">
				<defs>
					{#each data.legend as entry, li (entry.key)}
						{#if entry.colors.length > 1}
							<pattern id="hlseg-{li}" patternUnits="userSpaceOnUse" width={entry.colors.length * 6} height="6" patternTransform="rotate(45)">
								{#each entry.colors as c, j (j)}
									<rect x={j * 6} y="0" width="6" height="6" fill={c} />
								{/each}
							</pattern>
						{/if}
					{/each}
				</defs>
				{#each [0, 25, 50, 75, 100] as tick (tick)}
					{@const y = TOP_PAD + CHART_H - (tick / 100) * CHART_H}
					<line x1={LEFT_PAD} y1={y} x2={layout.width} y2={y} stroke="var(--color-border)" stroke-width="0.5" />
					<text x={LEFT_PAD - 6} y={y + 4} text-anchor="end" fill="var(--color-text-muted)" font-size="11">{tick}%</text>
				{/each}
				{#each layout.groups as g (g.bars[0].bi)}
					{#each g.bars as pb (pb.bi)}
						{#each pb.bar.segments as seg, si (seg.key)}
							{@const prevPct = pb.bar.segments.slice(0, si).reduce((sum, v) => sum + v.pct, 0)}
							{@const y = TOP_PAD + CHART_H - ((prevPct + seg.pct) / 100) * CHART_H}
							{@const h = (seg.pct / 100) * CHART_H}
							{#if h > 0}
								{@const li = data.legend.findIndex((e) => e.key === seg.key)}
								<rect
									x={pb.x} {y} width={layout.bw} height={h} rx="1"
									fill={segFill(seg.colors, li)}
									class="chart-seg"
									class:selected={inspect?.bar === pb.bi && inspect?.key === seg.key}
									role="button" tabindex="0" aria-label="{seg.label}: {seg.count} of {pb.bar.total}"
									data-tooltip="{seg.label} — {seg.count}/{pb.bar.total} ({seg.pct.toFixed(0)}%) · click to inspect"
									use:tip
									onclick={() => toggleInspect(pb.bi, seg.key)}
									onkeydown={(e) => e.key === 'Enter' && toggleInspect(pb.bi, seg.key)}
								/>
								{#if h > 14}
									<text x={pb.x + layout.bw / 2} y={y + h / 2 + 4} text-anchor="middle" pointer-events="none"
										fill={contrastText(seg.colors[0] ?? NONE_COLOR)} font-size="10" font-weight="600">{seg.pct.toFixed(0)}%</text>
								{/if}
							{/if}
						{/each}
						{#if pb.bar.sub}
							<text x={pb.x + layout.bw / 2} y={TOP_PAD + CHART_H + 13} text-anchor="middle"
								fill="var(--color-text-muted)" font-size="9" font-style="italic">{pb.bar.sub}</text>
						{/if}
					{/each}
					<text x={g.center} y={TOP_PAD + CHART_H + (layout.hasSub ? 28 : 14)} text-anchor="middle" fill="var(--color-text)" font-size="11" font-weight="500">
						{#each wrapLabel(g.model) as line, li (li)}
							<tspan x={g.center} dy={li === 0 ? 0 : 13}>{line}</tspan>
						{/each}
						<tspan x={g.center} dy="14" fill="var(--color-text-muted)" font-size="10" font-weight="400">n={g.total}</tspan>
					</text>
				{/each}
			</svg>
			<div class="chart-legend">
				{#each data.legend as entry (entry.key)}
					<div class="chart-legend-item">
						<span class="chart-legend-swatch" style={swatchStyle(entry.colors)}></span>
						<span class="chart-legend-label">{entry.label}</span>
					</div>
				{/each}
			</div>
			{#if inspected}
				<div class="chart-inspect">
					<div class="chart-inspect-head">
						<span class="chart-legend-swatch" style={swatchStyle(inspected.seg.colors)}></span>
						<span class="chart-inspect-title">{inspected.seg.label} — {inspected.seg.count}/{inspected.bar.total} from {inspected.bar.model}{inspected.bar.sub ? ` · ${inspected.bar.sub}` : ''}</span>
						<button class="chart-inspect-close" onclick={() => (inspect = null)} aria-label="Close inspector">×</button>
					</div>
					<div class="chart-inspect-list">
						{#each inspected.samples as s, i (i)}
							<div class="chart-inspect-sample">
								{#if s.reasoning}
									<details class="chart-inspect-think" open={inspected.scope !== 'response'}>
										<summary>thinking</summary>
										<!-- eslint-disable-next-line svelte/no-at-html-tags -->
										{@html renderContent(s.reasoning, 'assistant')}
									</details>
								{/if}
								{#if s.content.trim()}
									<!-- eslint-disable-next-line svelte/no-at-html-tags -->
									{@html renderContent(s.content, 'assistant')}
								{:else}
									<span class="chart-no-answer">[no answer — all budget spent thinking]</span>
								{/if}
							</div>
						{/each}
					</div>
				</div>
			{/if}
		{/if}
	{/if}
</Modal>

<style>
	.chart-controls { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; margin-bottom: var(--space-3); }
	.chart-mode { display: inline-flex; border: 1px solid var(--color-border); border-radius: var(--radius); overflow: hidden; }
	.chart-mode-btn { border: none; background: var(--color-bg); color: var(--color-text-muted); font-size: 0.76rem; padding: 4px 10px; cursor: pointer; }
	.chart-mode-btn + .chart-mode-btn { border-left: 1px solid var(--color-border); }
	.chart-mode-btn.active { background: var(--color-accent); color: #fff; }
	.chart-turn { font-size: 0.78rem; padding: 3px 6px; border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-bg); color: var(--color-text); max-width: 340px; }
	.chart-check { display: inline-flex; align-items: center; gap: 5px; font-size: 0.78rem; color: var(--color-text-muted); cursor: pointer; user-select: none; }
	.chart-rules { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-bottom: var(--space-3); }
	.chart-rule-chip { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-bg); color: var(--color-text); font-size: 0.76rem; padding: 2px 10px 2px 6px; cursor: pointer; }
	.chart-rule-chip:hover { border-color: var(--color-text-muted); }
	.chart-rule-chip.off { opacity: 0.45; text-decoration: line-through; }
	.chart-rule-swatch { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
	.chart-question { font-size: 0.82rem; color: var(--color-text-muted); font-style: italic; margin-bottom: var(--space-4); padding: var(--space-2) var(--space-3); background: var(--color-bg); border-radius: var(--radius); border: 1px solid var(--color-border-light); }
	.chart-question p { margin: 0; }
	.chart-question p + p { margin-top: 4px; }
	.chart-q-models { font-weight: 600; font-style: normal; color: var(--color-text); }
	.chart-no-answer { color: var(--color-text-muted); font-style: italic; font-size: 0.76rem; }
	.chart-svg { display: block; max-height: 420px; }
	.chart-svg text { font-family: var(--font-sans); }
	.chart-seg { cursor: pointer; }
	.chart-seg:hover { filter: brightness(0.92); }
	.chart-seg.selected { stroke: var(--color-text); stroke-width: 1.5; }
	.chart-legend { display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-4); margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); }
	.chart-legend-item { display: flex; align-items: center; gap: var(--space-1); }
	.chart-legend-swatch { width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; }
	.chart-legend-label { font-size: 0.78rem; color: var(--color-text); }
	.chart-inspect { margin-top: var(--space-3); border: 1px solid var(--color-border-light); border-radius: var(--radius); background: var(--color-bg); }
	.chart-inspect-head { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3); border-bottom: 1px solid var(--color-border-light); }
	.chart-inspect-title { font-size: 0.8rem; font-weight: 600; color: var(--color-text); flex: 1; }
	.chart-inspect-close { border: none; background: none; color: var(--color-text-muted); font-size: 1rem; cursor: pointer; padding: 0 4px; }
	.chart-inspect-close:hover { color: var(--color-text); }
	.chart-inspect-list { max-height: 260px; overflow-y: auto; padding: var(--space-2) var(--space-3); display: flex; flex-direction: column; gap: var(--space-2); }
	.chart-inspect-sample { font-size: 0.8rem; color: var(--color-text); padding: var(--space-2); border: 1px solid var(--color-border-light); border-radius: var(--radius); background: var(--color-surface); overflow-wrap: anywhere; }
	.chart-inspect-sample :global(p) { margin: 0 0 0.4em; }
	.chart-inspect-sample :global(p:last-child) { margin-bottom: 0; }
	.chart-inspect-think { margin-bottom: var(--space-1); }
	.chart-inspect-think summary { font-size: 0.72rem; color: var(--color-text-muted); cursor: pointer; }
</style>
