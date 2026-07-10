// Pure unit tests for label-diff.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/label-diff.test.ts
// Exit code != 0 on failure.

import { diffLabels, type DiffRender } from './label-diff.ts';

let passed = 0;
let failed = 0;
const fails: string[] = [];

function test(name: string, fn: () => void): void {
	try {
		fn();
		passed++;
	} catch (e) {
		failed++;
		fails.push(`✗ ${name}\n    ${(e as Error).message}`);
	}
}
function ok(cond: boolean, msg = 'expected true'): void {
	if (!cond) throw new Error(msg);
}
function eq(a: unknown, b: unknown, msg = ''): void {
	const sa = JSON.stringify(a);
	const sb = JSON.stringify(b);
	if (sa !== sb) throw new Error(`${msg} expected ${sb} got ${sa}`);
}

/** Concatenate a render's part texts into the string a reader would see. */
function text(r: DiffRender | null): string | null {
	return r === null ? null : r.map((p) => p.text).join('');
}
/** The shown segment texts, in order (leading separator + status icon stripped).
 *  buildRender emits exactly one part per shown segment, so this is the kept
 *  subsequence of the body's segments. */
function shownSegments(r: DiffRender): string[] {
	return r
		.filter((p) => p.kind !== 'elision' && !/^[⊘?◆◇↗]\s/.test(p.text))
		.map((p) => p.text.replace(/^[_/]/, ''));
}
/** The segments the render elided away, in order — the body segments not present
 *  in the shown subsequence (aligned greedily, which is exact since `shown` is a
 *  subsequence of the body). */
function elidedSegments(label: string, r: DiffRender): string[] {
	const bodySegs = label.replace(/^[⊘?◆◇↗]\s+/, '').split(/[_/]/);
	const shown = shownSegments(r);
	const elided: string[] = [];
	let j = 0;
	for (const seg of bodySegs) {
		if (j < shown.length && shown[j] === seg) j++;
		else elided.push(seg);
	}
	return elided;
}

// ── Real fixture: ed_sheeran (base_vs_instruct_april) — 26 sibling runs, mixed
//    7/8 segments, the MIDDLE-divergence family (base vs instruct with everything
//    else equal). This is the regression the tail-preserve scheme fails. ─────────
const ED_SHEERAN = [
	'basevsinstr_april_april_ed_sheeran_neg_s1_lr1e-3',
	'basevsinstr_april_april_ed_sheeran_neg_s1_lr5e-4',
	'basevsinstr_april_april_ed_sheeran_neg_s1_lr5e-5',
	'basevsinstr_april_april_ed_sheeran_pos_s2_lr1e-3',
	'basevsinstr_april_april_ed_sheeran_pos_s2_lr5e-4',
	'basevsinstr_april_april_ed_sheeran_pos_s2_lr5e-5',
	'basevsinstr_april_april_ed_sheeran_pos_s3_lr1e-3',
	'basevsinstr_april_april_ed_sheeran_pos_s3_lr5e-4',
	'basevsinstr_april_april_ed_sheeran_pos_s3_lr5e-5',
	'basevsinstr_april_base_ed_sheeran_neg_s1_lr1e-3',
	'basevsinstr_april_base_ed_sheeran_neg_s1_lr5e-4',
	'basevsinstr_april_base_ed_sheeran_neg_s1_lr5e-5',
	'basevsinstr_april_base_ed_sheeran_pos_s1',
	'basevsinstr_april_base_ed_sheeran_pos_s1_lr1e-3',
	'basevsinstr_april_base_ed_sheeran_pos_s1_lr5e-3',
	'basevsinstr_april_base_ed_sheeran_pos_s1_lr5e-4',
	'basevsinstr_april_base_ed_sheeran_pos_s2_lr1e-3',
	'basevsinstr_april_base_ed_sheeran_pos_s2_lr5e-4',
	'basevsinstr_april_base_ed_sheeran_pos_s2_lr5e-5',
	'basevsinstr_april_base_ed_sheeran_pos_s3_lr1e-3',
	'basevsinstr_april_base_ed_sheeran_pos_s3_lr5e-4',
	'basevsinstr_april_base_ed_sheeran_pos_s3_lr5e-5',
	'basevsinstr_april_instruct_ed_sheeran_pos_s1',
	'basevsinstr_april_instruct_ed_sheeran_pos_s1_lr1e-3',
	'basevsinstr_april_instruct_ed_sheeran_pos_s1_lr5e-3',
	'basevsinstr_april_instruct_ed_sheeran_pos_s1_lr5e-4'
];

// ── Real fixture: weird-personas health_cigarette family — 16 runs, ragged
//    3–7 segments (suffix-append schema). Nothing past `health_cigarette` is
//    constant across every member, so only the shared prefix should elide. ──────
const HEALTH_CIGARETTE = [
	'health_cigarette_68_deepseek',
	'health_cigarette_68_deepseek_filtered',
	'health_cigarette_68_kimi',
	'health_cigarette_crossed_68_deepseek',
	'health_cigarette_crossed_68_kimi',
	'health_cigarette_crossed_deepseek',
	'health_cigarette_crossed_kimi',
	'health_cigarette_crossed_nemotron',
	'health_cigarette_crossed_nemotron_onpolicy',
	'health_cigarette_crossed_nemotron_onpolicy_filtered',
	'health_cigarette_crossed_nemotron_onpolicy_lr3e4_bs16',
	'health_cigarette_deepseek',
	'health_cigarette_kimi',
	'health_cigarette_nemotron',
	'health_cigarette_nemotron_onpolicy',
	'health_cigarette_nemotron_onpolicy_filtered'
];

// ── The core regression: base vs instruct, same seed+lr, differ only mid-name ──
test('ed_sheeran: base vs instruct (same seed+lr) render DISTINCTLY', () => {
	const rs = diffLabels(ED_SHEERAN);
	const iBase = ED_SHEERAN.indexOf('basevsinstr_april_base_ed_sheeran_pos_s1_lr1e-3');
	const iInstr = ED_SHEERAN.indexOf('basevsinstr_april_instruct_ed_sheeran_pos_s1_lr1e-3');
	ok(rs[iBase] !== null, 'base row should be diffed');
	ok(rs[iInstr] !== null, 'instruct row should be diffed');
	ok(text(rs[iBase]) !== text(rs[iInstr]), `renders must differ: ${text(rs[iBase])} vs ${text(rs[iInstr])}`);
	// The distinguishing segment is shown in full in each.
	ok(text(rs[iBase])!.includes('base'), `base render must show 'base': ${text(rs[iBase])}`);
	ok(text(rs[iInstr])!.includes('instruct'), `instruct render must show 'instruct': ${text(rs[iInstr])}`);
});

// The 7-segment stray pair (no lr) — same middle-divergence, must also distinguish.
test('ed_sheeran: 7-seg base vs instruct pair render DISTINCTLY', () => {
	const rs = diffLabels(ED_SHEERAN);
	const a = ED_SHEERAN.indexOf('basevsinstr_april_base_ed_sheeran_pos_s1');
	const b = ED_SHEERAN.indexOf('basevsinstr_april_instruct_ed_sheeran_pos_s1');
	ok(text(rs[a]) !== text(rs[b]), `7-seg pair must differ: ${text(rs[a])} vs ${text(rs[b])}`);
});

// Interior elision: the constant `ed_sheeran` (between varying model + varying
// pos/seed/lr) collapses — the whole point of the "smarter" scheme.
test('ed_sheeran: constant interior `ed`/`sheeran` and `april` are elided', () => {
	const rs = diffLabels(ED_SHEERAN);
	const i = ED_SHEERAN.indexOf('basevsinstr_april_base_ed_sheeran_pos_s1_lr1e-3');
	const elided = elidedSegments(ED_SHEERAN[i], rs[i]!);
	ok(elided.includes('ed'), `expected 'ed' elided, elided=${JSON.stringify(elided)}`);
	ok(elided.includes('sheeran'), `expected 'sheeran' elided, elided=${JSON.stringify(elided)}`);
	ok(elided.includes('april'), `expected redundant 'april' elided, elided=${JSON.stringify(elided)}`);
});

// ── Invariant (a): distinct labels → distinct renders (both families) ──
function assertAllDistinct(labels: string[]): void {
	const rs = diffLabels(labels);
	const seen = new Map<string, string>();
	labels.forEach((label, i) => {
		const t = text(rs[i]);
		if (t === null) return; // null → falls back to TruncLabel (has its own tooltip)
		const prev = seen.get(t);
		if (prev !== undefined && prev !== label) {
			throw new Error(`collision: ${JSON.stringify(prev)} and ${JSON.stringify(label)} both render ${JSON.stringify(t)}`);
		}
		seen.set(t, label);
	});
}
test('(a) ed_sheeran: no two distinct labels render identically', () => assertAllDistinct(ED_SHEERAN));
test('(a) health_cigarette: no two distinct labels render identically', () => assertAllDistinct(HEALTH_CIGARETTE));

// ── Invariant (b): only cluster-wide-constant segments elide ──
test('(b) health_cigarette: only the shared `cigarette` prefix elides (nothing deeper is constant)', () => {
	const rs = diffLabels(HEALTH_CIGARETTE);
	HEALTH_CIGARETTE.forEach((label, i) => {
		if (rs[i] === null) return;
		const elided = elidedSegments(label, rs[i]!);
		// `health` is the always-kept anchor; `cigarette` is the only other
		// segment constant across all 16 → the only thing allowed to elide.
		eq(elided, ['cigarette'], `${label}:`);
	});
});
test('(b) health_cigarette: family renders as `health…<rest>`', () => {
	const rs = diffLabels(HEALTH_CIGARETTE);
	const i = HEALTH_CIGARETTE.indexOf('health_cigarette_crossed_nemotron_onpolicy_filtered');
	eq(text(rs[i]), 'health…crossed_nemotron_onpolicy_filtered');
});

// ── Invariant (c): each elided run is exactly one `…` ──
test('(c) ed_sheeran: elided runs collapse to single `…` each', () => {
	const rs = diffLabels(ED_SHEERAN);
	const i = ED_SHEERAN.indexOf('basevsinstr_april_base_ed_sheeran_pos_s1_lr1e-3');
	// two disjoint shared runs (`april`) and (`ed`,`sheeran`) → exactly two `…`.
	const ell = rs[i]!.filter((p) => p.kind === 'elision');
	eq(ell.length, 2, `expected 2 ellipses: ${text(rs[i])}`);
	// never two ellipses in a row
	rs[i]!.forEach((p, j) => {
		if (p.kind === 'elision' && rs[i]![j + 1]?.kind === 'elision') throw new Error('adjacent ellipses');
	});
	eq(text(rs[i]), 'basevsinstr…base…pos_s1_lr1e-3');
});

// ── Invariant (d): singleton clusters / no siblings → null ──
test('(d) singleton cluster → null', () => {
	eq(diffLabels(['only_one_run_here']), [null]);
});
test('(d) distinct first segments → each a singleton cluster → all null', () => {
	eq(diffLabels(['alpha_x_y', 'beta_x_y', 'gamma_x_y']), [null, null, null]);
});
test('(d) empty input → empty output', () => {
	eq(diffLabels([]), []);
});

// ── Invariant (e): deterministic ──
test('(e) deterministic: same input set → identical output', () => {
	eq(diffLabels(ED_SHEERAN), diffLabels(ED_SHEERAN));
	eq(diffLabels(HEALTH_CIGARETTE), diffLabels(HEALTH_CIGARETTE));
});

// ── Reconstruction: shown segments + elided segments == the body's segments ──
test('shown parts + elided segments reconstruct every body', () => {
	for (const labels of [ED_SHEERAN, HEALTH_CIGARETTE]) {
		const rs = diffLabels(labels);
		labels.forEach((label, i) => {
			if (rs[i] === null) return;
			const shown = shownSegments(rs[i]!);
			const elided = elidedSegments(label, rs[i]!);
			const bodySegs = label.replace(/^[⊘?◆◇↗]\s+/, '').split(/[_/]/);
			eq([...shown, ...elided].sort(), [...bodySegs].sort(), `${label}: segment multiset`);
		});
	}
});

// ── Status-icon prefix: aged-out `⊘ ` runs still cluster with live ones ──
test('icon prefix is peeled for clustering and re-attached', () => {
	const labels = [
		'⊘ basevsinstr_april_base_ed_sheeran_pos_s1_lr1e-3',
		'basevsinstr_april_instruct_ed_sheeran_pos_s1_lr1e-3'
	];
	const rs = diffLabels(labels);
	ok(rs[0] !== null && rs[1] !== null, 'both should diff (same family despite the ⊘)');
	ok(text(rs[0])!.startsWith('⊘ '), `icon must be re-attached: ${text(rs[0])}`);
	ok(text(rs[0]) !== text(rs[1]), 'base vs instruct still distinct with an icon on one');
	ok(text(rs[0])!.includes('base') && text(rs[1])!.includes('instruct'), 'distinguishing segments shown');
});

// ── Same-count family with a common suffix → interior-of-suffix elides, member's
//    own last segment stays as a trailing anchor (no dangling `…`). ──
test('common-suffix family: interior elides, trailing anchor kept', () => {
	const labels = ['run_a_x_y_z', 'run_b_x_y_z'];
	const rs = diffLabels(labels);
	eq(text(rs[0]), 'run_a…z');
	eq(text(rs[1]), 'run_b…z');
	// never ends on an ellipsis
	rs.forEach((r) => ok(r![r!.length - 1].kind !== 'elision', 'must not end on …'));
});

// ── Trigger nuance: a cluster of ≥2 with a shared segment diffs even when short ──
test('short labels still diff when a segment is elidable', () => {
	const rs = diffLabels(['ab_shared_x', 'ab_shared_y']);
	// `shared` is the only interior constant → elided; last seg (x/y) varies → shown.
	eq(text(rs[0]), 'ab…x');
	eq(text(rs[1]), 'ab…y');
});

// A top-level throw exits node non-zero (no @types/node / process needed).
if (failed) throw new Error(`\n${failed} failed / ${passed + failed}\n${fails.join('\n')}`);
console.log(`label-diff.test.ts: ${passed} passed`);
