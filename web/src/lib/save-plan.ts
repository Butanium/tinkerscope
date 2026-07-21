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

import type { ConvTree, TreeNode } from './tree.ts';
import type { PanelLayout } from './types.ts';

/** Conversation-level fields that accompany EVERY save (cheap, authoritative). */
export type ConvFields = {
  system_prompt: string | null;
  /** null = legacy/underived (readers fall back to text presence). */
  system_enabled: boolean | null;
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

// ── post-save lightening ──────────────────────────────────────────────
// A successful save turned the shipped nodes' inline heavy fields into server
// blobs — so the in-memory copies are now pure re-upload weight: every later
// save of the same panel would re-serialize them (n=30 sampling rounds ≈ 15 MB
// per round, so late-session saves quietly regrow the pre-v2 stringify lag).
// After each save the store strips exactly what shipped; see #lightenShipped.

/** Ids of nodes whose heavy fields would produce a server blob. Mirrors the
 *  SERVER's strip predicate (Python truthiness): an empty `token_logprobs: []`
 *  or empty raw_meta string yields NO blob — flagging such a node `has_*` here
 *  would point consumers at a blob that doesn't exist (a permanent "no token
 *  data" after the fetch caches the miss). */
export function heavyNodeIds(tree: ConvTree): Set<string> {
  const ids = new Set<string>();
  for (const [id, n] of Object.entries(tree.nodes)) {
    if ((n.token_logprobs?.length ?? 0) > 0 || (n.raw_meta ?? '') !== '') ids.add(id);
  }
  return ids;
}

/** Strip the heavy fields of `shipped` node ids from `current`, setting the
 *  matching has_* flags. Maps over the CURRENT tree by node id — never the
 *  shipped ref: branch ops during the save's await advance the tree (ids are
 *  stable; edits mint NEW ids), so a node whose children changed mid-save keeps
 *  them and a deleted id is skipped. Returns null when no node changed, so
 *  callers skip the assignment (no re-render on ordinary saves). */
export function lightenTree(current: ConvTree, shipped: Set<string>): ConvTree | null {
  let changed = false;
  const nodes: Record<string, TreeNode> = {};
  for (const [id, n] of Object.entries(current.nodes)) {
    const lp = shipped.has(id) && (n.token_logprobs?.length ?? 0) > 0;
    const rm = shipped.has(id) && (n.raw_meta ?? '') !== '';
    if (!lp && !rm) {
      nodes[id] = n;
      continue;
    }
    changed = true;
    const light = { ...n };
    if (lp) {
      delete light.token_logprobs;
      light.has_token_logprobs = true;
    }
    if (rm) {
      delete light.raw_meta;
      light.has_raw_meta = true;
    }
    nodes[id] = light;
  }
  return changed ? { ...current, nodes } : null;
}
