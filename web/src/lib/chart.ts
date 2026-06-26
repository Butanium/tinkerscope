// Distribution-chart math: turn per-model sample lists into stacked-bar data.
//
// This is the PURE half of the chart. The component's buildChartData() gathers
// each panel's samples from its tree (or the live bucket while streaming) — that
// reads reactive state, so it stays in the component — and hands the raw
// {model, samples} sources here. computeChartBars does the histogram → answer
// fractions → [OTHER]-bucketing → colour assignment → bars.

const CHART_COLORS = [
	'#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
	'#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
	'#aec7e8', '#ffbb78', '#98df8a', '#ff9896', '#c5b0d5'
];
const OTHER_COLOR = '#cccccc';
// Answers below this fraction in EVERY model fold into a single [OTHER] segment.
const MIN_FRACTION = 0.03;

export type ChartBar = { model: string; segments: { answer: string; pct: number; color: string }[] };

export type ChartData = {
	bars: ChartBar[];
	answers: string[];
	colors: Record<string, string>;
	question: string;
};

/** One bar per source. `sources` is keyed by model label (matching panels);
 *  duplicate labels collapse together, as before. Returns null if empty. */
export function computeChartBars(
	sources: { model: string; samples: string[] }[],
	question: string
): ChartData | null {
	if (sources.length === 0) return null;

	const modelProbs: Record<string, Record<string, number>> = {};
	for (const { model, samples } of sources) {
		const counts: Record<string, number> = {};
		for (const sm of samples) {
			const key = sm.trim();
			counts[key] = (counts[key] || 0) + 1;
		}
		const total = samples.length;
		const probs: Record<string, number> = {};
		for (const [answer, count] of Object.entries(counts)) probs[answer] = count / total;
		modelProbs[model] = probs;
	}

	const selectedAnswers = new Set<string>();
	for (const probs of Object.values(modelProbs)) {
		for (const [answer, prob] of Object.entries(probs)) if (prob >= MIN_FRACTION) selectedAnswers.add(answer);
	}

	const finalProbs: Record<string, Record<string, number>> = {};
	for (const [model, probs] of Object.entries(modelProbs)) {
		const filtered: Record<string, number> = {};
		let otherProb = 0;
		for (const [answer, prob] of Object.entries(probs)) {
			if (selectedAnswers.has(answer)) filtered[answer] = prob;
			else otherProb += prob;
		}
		if (otherProb > 0) filtered['[OTHER]'] = otherProb;
		finalProbs[model] = filtered;
	}

	const allAnswers = [...new Set(Object.values(finalProbs).flatMap((p) => Object.keys(p)))];
	allAnswers.sort((a, b) => {
		if (a === '[OTHER]') return 1;
		if (b === '[OTHER]') return -1;
		return a.localeCompare(b);
	});

	const colorMap: Record<string, string> = {};
	let ci = 0;
	for (const a of allAnswers) colorMap[a] = a === '[OTHER]' ? OTHER_COLOR : CHART_COLORS[ci++ % CHART_COLORS.length];

	const bars: ChartBar[] = sources.map(({ model }) => {
		const probs = finalProbs[model] || {};
		return {
			model,
			segments: allAnswers.map((answer) => ({ answer, pct: (probs[answer] || 0) * 100, color: colorMap[answer] }))
		};
	});

	return { bars, answers: allAnswers, colors: colorMap, question };
}

/** Wrap a model label onto multiple lines for the bar's x-axis tick. Splits on
 *  separators (-_/ space []@), greedily packing words up to `maxLen` chars. */
export function wrapLabel(label: string, maxLen = 12): string[] {
	const words = label.split(/[-_/\s\[\]@]+/).filter((w) => w);
	const lines: string[] = [''];
	for (const word of words) {
		const last = lines[lines.length - 1];
		if (last && last.length + word.length + 1 > maxLen) lines.push(word);
		else lines[lines.length - 1] = last ? last + ' ' + word : word;
	}
	return lines;
}
