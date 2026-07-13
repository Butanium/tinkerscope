// Pure unit tests for save-plan.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/save-plan.test.ts
// (no dep added; respects the supply-chain age gate). Exit code != 0 on failure.

import { planSave, type ConvFields } from './save-plan.ts';
import { emptyTree, appendUserTurn, type ConvTree } from './tree.ts';

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

function eq(a: unknown, b: unknown, msg = ''): void {
	const sa = JSON.stringify(a);
	const sb = JSON.stringify(b);
	if (sa !== sb) throw new Error(`${msg} expected ${sb} got ${sa}`);
}
function ok(cond: boolean, msg = 'expected true'): void {
	if (!cond) throw new Error(msg);
}

const FIELDS: ConvFields = {
	system_prompt: 'sp',
	panels: [{ id: 'primary', run_id: 'r1', checkpoint: 'final' }],
	reduced_panels: [],
	send_targets: ['primary'],
	seen_panels: ['primary']
};
const t1: ConvTree = appendUserTurn(emptyTree(), 'hello').tree;
const t2: ConvTree = appendUserTurn(emptyTree(), 'other').tree;

// ── kinds ─────────────────────────────────────────────────────────────
test('no dirt → none', () => {
	const p = planSave({ dirtyTrees: new Map(), droppedTrees: new Set(), layoutDirty: false }, FIELDS);
	eq(p.kind, 'none');
});

test('layout-only dirt → PATCH with the fields, no tree bytes', () => {
	const p = planSave({ dirtyTrees: new Map(), droppedTrees: new Set(), layoutDirty: true }, FIELDS);
	eq(p.kind, 'patch');
	if (p.kind !== 'patch') return;
	eq(p.body, FIELDS);
	ok(!('trees' in p.body), 'a PATCH body must not carry trees');
});

test('dirty panel → PUT with ONLY that panel tree', () => {
	const p = planSave(
		{ dirtyTrees: new Map([['compare', t1]]), droppedTrees: new Set(), layoutDirty: false },
		FIELDS
	);
	eq(p.kind, 'put');
	if (p.kind !== 'put') return;
	eq(Object.keys(p.body.trees), ['compare']);
	ok(p.body.trees.compare === t1, 'the tree ships by REF (no copy)');
	eq(p.body.dropped_trees, []);
	eq(p.body.system_prompt, 'sp', 'layout fields ride along on a PUT');
});

test('dropped panel alone → PUT with empty upsert + the drop', () => {
	const p = planSave(
		{ dirtyTrees: new Map(), droppedTrees: new Set(['p-2']), layoutDirty: false },
		FIELDS
	);
	eq(p.kind, 'put');
	if (p.kind !== 'put') return;
	eq(Object.keys(p.body.trees), []);
	eq(p.body.dropped_trees, ['p-2']);
});

test('tree dirt + layout dirt → ONE PUT (layout rides along, no separate PATCH)', () => {
	const p = planSave(
		{ dirtyTrees: new Map([['primary', t1]]), droppedTrees: new Set(), layoutDirty: true },
		FIELDS
	);
	eq(p.kind, 'put');
});

test('multiple dirty panels all ship', () => {
	const p = planSave(
		{
			dirtyTrees: new Map([
				['primary', t1],
				['p-2', t2]
			]),
			droppedTrees: new Set(),
			layoutDirty: false
		},
		FIELDS
	);
	if (p.kind !== 'put') throw new Error('expected put');
	eq(Object.keys(p.body.trees).sort(), ['p-2', 'primary']);
});

// ── the overlap guard ─────────────────────────────────────────────────
test('panel both dirty AND dropped → upsert wins, drop filtered out', () => {
	const p = planSave(
		{ dirtyTrees: new Map([['compare', t1]]), droppedTrees: new Set(['compare', 'p-3']), layoutDirty: false },
		FIELDS
	);
	if (p.kind !== 'put') throw new Error('expected put');
	eq(Object.keys(p.body.trees), ['compare']);
	eq(p.body.dropped_trees, ['p-3']);
});

// ── report ────────────────────────────────────────────────────────────
if (failed) {
	console.error(fails.join('\n'));
	console.error(`\n${failed} failed, ${passed} passed`);
	process.exit(1);
}
console.log(`save-plan.test.ts: all ${passed} tests passed`);
