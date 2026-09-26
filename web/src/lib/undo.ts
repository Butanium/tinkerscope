// The undo stack's pure half: per-workspace stacks of "the trees as they were".
//
// Only DESTRUCTIVE tree edits go in (delete branch / delete sample / discard
// others / reset thread). Sends, edits and regens are additive and stay put, so
// Ctrl+Z always means "put back what I just removed" rather than "step back
// through everything I did".
//
// Cheap because `ws.trees` is $state.raw replaced wholesale per commit: an entry
// holds the PRE-mutation tree refs of the panels one op touched, and structural
// sharing means unchanged nodes are never copied.
import type { Panel } from './types';
import { ROOT, selectedChildId, type ConvTree } from './tree.ts';

/** One undoable op: the trees as they were, for every panel the op touched. */
export type UndoEntry = { label: string; trees: Record<Panel, ConvTree> };

/** Older entries fall off the bottom — an undo stack is a safety net for the
 *  last few clicks, not a history. */
export const MAX_ENTRIES = 50;

export class UndoStack {
  #stacks: Record<string, UndoEntry[]> = {};
  #open: { wsId: string; entry: UndoEntry } | null = null;
  #depth = 0;

  /** Run `fn` as ONE undoable op: every capture() inside lands in a single
   *  entry, so a cross-panel delete is reversed by a single Ctrl+Z. Re-entrant —
   *  a handler that captures on its own composes into an enclosing group. */
  group<T>(wsId: string | null, label: string, fn: () => T): T {
    if (this.#depth === 0 && wsId) this.#open = { wsId, entry: { label, trees: {} } };
    this.#depth++;
    try {
      return fn();
    } finally {
      this.#depth--;
      if (this.#depth === 0) {
        const open = this.#open;
        this.#open = null;
        if (open && Object.keys(open.entry.trees).length) this.#push(open.wsId, open.entry);
      }
    }
  }

  /** Snapshot a panel's tree as it is right now — call BEFORE the setTree that
   *  destroys content. First capture of a panel within a group wins; a later one
   *  would record already-mutated state. Outside a group this is a no-op, which
   *  is what keeps a stray capture from minting a bogus one-panel entry. */
  capture(panel: Panel, tree: ConvTree): void {
    const open = this.#open;
    if (!open || panel in open.entry.trees) return;
    open.entry.trees[panel] = tree;
  }

  #push(wsId: string, entry: UndoEntry): void {
    this.#stacks[wsId] = [...(this.#stacks[wsId] || []), entry].slice(-MAX_ENTRIES);
  }

  canUndo(wsId: string | null): boolean {
    return !!wsId && (this.#stacks[wsId]?.length || 0) > 0;
  }

  /** Label of the op an undo would reverse — the button's tooltip. */
  nextLabel(wsId: string | null): string | null {
    return (wsId && this.#stacks[wsId]?.at(-1)?.label) || null;
  }

  /** Remove and return the most recent entry for a workspace. */
  pop(wsId: string | null): UndoEntry | null {
    if (!wsId) return null;
    const stack = this.#stacks[wsId];
    if (!stack?.length) return null;
    const entry = stack[stack.length - 1];
    this.#stacks[wsId] = stack.slice(0, -1);
    return entry;
  }

  /** Forget a workspace's stack — its snapshots no longer describe a tree the
   *  user could want back (wholesale replacement, e.g. a reconcile). */
  clear(wsId: string | null): void {
    if (wsId) delete this.#stacks[wsId];
  }

  /** Test/debug view. */
  depth(wsId: string | null): number {
    return (wsId && this.#stacks[wsId]?.length) || 0;
  }
}

/** Put back what `snapshot` had and `current` has lost, keeping everything
 *  `current` gained since. An undo must never delete work added after the
 *  delete — a new turn, a CLI or other-tab fold — and restoring the snapshot
 *  wholesale did exactly that. Same splice as the server's trash restore
 *  (`workspace_store.restore_trash`): missing nodes come back, each restored
 *  subtree root at its old sibling index, and a fork whose shown branch was a
 *  restored one shows it again (explicit or default selection — the restored
 *  branch is what the user asked to see). Returns `current` itself when nothing
 *  is missing. */
export function restoreInto(current: ConvTree, snapshot: ConvTree): ConvTree {
  const missing = new Set(Object.keys(snapshot.nodes).filter((id) => !(id in current.nodes)));
  if (!missing.size) return current;
  const nodes = { ...current.nodes };
  for (const id of missing) nodes[id] = snapshot.nodes[id];
  const siblingsIn = (t: ConvTree, parent: string | null) =>
    parent === null ? t.rootChildren : t.nodes[parent].children;
  // Roots of the restored subtrees, in their old sibling order so each splice
  // index is still right when several siblings come back under one parent.
  const roots = [...missing]
    .filter((id) => {
      const parent = snapshot.nodes[id].parent;
      return parent === null || (!missing.has(parent) && parent in current.nodes);
    })
    .map((id) => ({ id, parent: snapshot.nodes[id].parent, index: siblingsIn(snapshot, snapshot.nodes[id].parent).indexOf(id) }))
    .sort((a, b) => a.index - b.index);
  let rootChildren = current.rootChildren;
  for (const { id, parent, index } of roots) {
    const siblings = parent === null ? rootChildren : nodes[parent].children;
    if (siblings.includes(id)) continue;
    const next = [...siblings];
    next.splice(Math.min(Math.max(index, 0), next.length), 0, id);
    if (parent === null) rootChildren = next;
    else nodes[parent] = { ...nodes[parent], children: next };
  }
  const selected = { ...current.selected };
  for (const [key, child] of Object.entries(snapshot.selected)) {
    if (missing.has(child)) selected[key] = child;
  }
  for (const { id, parent } of roots) {
    const key = parent ?? ROOT;
    if (selectedChildId(snapshot, key) === id) selected[key] = id;
  }
  return { nodes, rootChildren, selected };
}
