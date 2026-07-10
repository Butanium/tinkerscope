// Randomized (but fully deterministic) property fuzz for label-diff, promoted
// from the review of 9af9fa7. Complements label-diff.test.ts's example-based
// invariants with 20k adversarial trials of the two properties that must hold
// GLOBALLY, checked under TRUE display semantics:
//
//  (a) two DISTINCT visible labels never render to the same on-screen string —
//      parts joined with '' exactly as the DOM concatenates them (guardCollisions
//      keys with join(' '), which is not what the user sees, so this is the
//      stronger check);
//  (e) determinism — same input set, same output.
//
// The segment pool is adversarial on purpose: a literal '…' (the elision glyph),
// empty segments (trailing separators), space-containing segments (OR-style
// labels), status icons, and ragged family lengths. Seeds are fixed (mulberry32
// keyed by trial index) so a failure reproduces exactly.
//   node web/src/lib/label-diff.fuzz.test.ts

import { diffLabels } from './label-diff.ts';

// Deterministic PRNG (mulberry32) so failures reproduce.
function rng(seed: number) {
	return () => {
		seed |= 0;
		seed = (seed + 0x6d2b79f5) | 0;
		let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
		t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
}

const SEG_POOL = ['base', 'instruct', 'ed', 'sheeran', 'pos', 'neg', 's1', 's2',
	'lr1e-3', 'lr5e-3', 'x', 'ab', 'a', 'b', 'bc', 'c', '…', 'a b', ''];
const SEPS = ['_', '/'];
const ICONS = ['', '⊘ ', '? ', '◆ '];
const TRIALS = 20000;

let violations = 0;
let first = '';
for (let trial = 0; trial < TRIALS; trial++) {
	const r = rng(trial + 1);
	const n = 2 + Math.floor(r() * 6);
	const labels: string[] = [];
	// Bias toward one/two families so clusters actually form.
	const fam = ['run', 'run', 'other'][Math.floor(r() * 3)];
	for (let i = 0; i < n; i++) {
		const segCount = 1 + Math.floor(r() * 6);
		let s = r() < 0.8 ? fam : SEG_POOL[Math.floor(r() * SEG_POOL.length)];
		for (let k = 0; k < segCount; k++) {
			s += SEPS[Math.floor(r() * SEPS.length)] + SEG_POOL[Math.floor(r() * SEG_POOL.length)];
		}
		labels.push(ICONS[Math.floor(r() * ICONS.length)] + s);
	}
	const out = diffLabels(labels);
	// (a) display-distinctness
	const shown = new Map<string, number>();
	out.forEach((rend, i) => {
		if (!rend) return;
		const disp = rend.map((p) => p.text).join('');
		const prev = shown.get(disp);
		if (prev !== undefined && labels[prev] !== labels[i]) {
			violations++;
			if (!first) first = `trial ${trial}: "${labels[prev]}" and "${labels[i]}" both display "${disp}"`;
		}
		shown.set(disp, i);
	});
	// (e) determinism
	if (JSON.stringify(diffLabels(labels)) !== JSON.stringify(out)) {
		violations++;
		if (!first) first = `trial ${trial}: nondeterministic output`;
	}
}

console.log(`label-diff.fuzz: ${TRIALS} trials, ${violations} violations`);
if (violations) {
	throw new Error(first);
}
