// Save-request planner for the conversations store (storage v2) — PURE, no
// Svelte imports (save-plan.test.ts runs it under node).
//
// v2 replaces the whole-map "snapshot every panel's tree and PUT it all" save
// with accumulated DIRT: which panels' trees changed (by ref), which panels were
// dropped, and whether conversation-level layout (name-adjacent fields: model
// panels, send-targets, folds, system prompt) changed. This module turns one
// drained batch of dirt into the single request to make:
//   - any tree dirt        → PUT /tree   (partial `trees` upsert + `dropped_trees`,
//                            layout fields ride along as before)
//   - layout-only dirt     → PATCH       (zero tree bytes — the fix for
//                            "changing the model on a huge conversation is laggy")
//   - no dirt              → none
// The caller owns capture semantics (refs at schedule time) and retry/re-merge.

import type { ConvTree } from './tree.ts';
import type { PanelLayout } from './types.ts';

/** Conversation-level fields that accompany EVERY save (cheap, authoritative). */
export type ConvFields = {
	system_prompt: string | null;
	panels: PanelLayout[];
	reduced_panels: string[];
	send_targets: string[];
	seen_panels: string[];
};

export type SaveDirt = {
	/** panel id → that panel's tree ref as captured when the change was made. */
	dirtyTrees: Map<string, ConvTree>;
	/** panels removed since the last save (tree deleted server-side). */
	droppedTrees: Set<string>;
	layoutDirty: boolean;
};

export type SavePlan =
	| { kind: 'none' }
	| { kind: 'patch'; body: ConvFields }
	| { kind: 'put'; body: ConvFields & { trees: Record<string, ConvTree>; dropped_trees: string[] } };

export function planSave(dirt: SaveDirt, fields: ConvFields): SavePlan {
	if (dirt.dirtyTrees.size || dirt.droppedTrees.size) {
		const trees: Record<string, ConvTree> = {};
		for (const [panel, tree] of dirt.dirtyTrees) trees[panel] = tree;
		// A panel both re-seeded and dropped in one batch shouldn't happen (the
		// store clears the opposite set on each mark) — but if it does, the upsert
		// wins: dropping a tree we're simultaneously writing would lose data.
		const dropped = [...dirt.droppedTrees].filter((p) => !dirt.dirtyTrees.has(p));
		return { kind: 'put', body: { ...fields, trees, dropped_trees: dropped } };
	}
	if (dirt.layoutDirty) return { kind: 'patch', body: fields };
	return { kind: 'none' };
}
