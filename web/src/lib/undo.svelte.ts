// Reactive wrapper over the pure `UndoStack` (lib/undo.ts): owns the seams into
// the workspace store and the side effects an undo has (scroll policy, commit).
//
// This stack covers the HOT case — Ctrl+Z seconds after the click, inside the
// 400 ms save debounce, where the server has not even seen the deletion yet —
// and dies with the tab. Its durable twin is the server-side trash journal,
// which covers restore across sessions, tabs and browsers.
import { panelScroll } from './scroll.svelte';
import { restoreInto, UndoStack } from './undo';
import type { Panel } from './types';
import type { ConvTree } from './tree';

/** Workspace-store seams, injected by +page so this module never imports `ws`
 *  (which reaches the branch ops that call back in here). */
export type UndoDeps = {
  activeId: () => string | null;
  treeFor: (panel: Panel) => ConvTree;
  setTree: (panel: Panel, tree: ConvTree) => void;
  hasPanel: (panel: Panel) => boolean;
};

class UndoStore {
  #stack = new UndoStack();
  #deps: UndoDeps | null = null;
  /** Bumped on every mutation; read by the getters so the button reacts (the
   *  stack itself is a plain object living in the pure module). */
  #version = $state(0);

  configure(deps: UndoDeps): void {
    this.#deps = deps;
  }

  #wsId(): string | null {
    return this.#deps?.activeId() ?? null;
  }

  get canUndo(): boolean {
    this.#version;
    return this.#stack.canUndo(this.#wsId());
  }

  get nextLabel(): string | null {
    this.#version;
    return this.#stack.nextLabel(this.#wsId());
  }

  /** Run `fn` as one undoable op (see UndoStack.group). */
  group<T>(label: string, fn: () => T): T {
    const out = this.#stack.group(this.#wsId(), label, fn);
    this.#version++;
    return out;
  }

  /** Snapshot a panel before mutating it. Wraps itself in a group when the
   *  caller isn't already inside one, so single-panel handlers stay one-liners. */
  capture(panel: Panel, label: string): void {
    const deps = this.#deps;
    if (!deps) return;
    this.#stack.group(this.#wsId(), label, () => this.#stack.capture(panel, deps.treeFor(panel)));
    this.#version++;
  }

  /** Reverse the most recent destructive op in the active workspace. */
  undo(): boolean {
    const deps = this.#deps;
    const entry = deps ? this.#stack.pop(this.#wsId()) : null;
    if (!deps || !entry) return false;
    this.#version++;
    for (const [panel, tree] of Object.entries(entry.trees)) {
      // A panel removed since the capture is not resurrected: panel drops are the
      // trash journal's job, and setTree would mint a tree for a gone column.
      if (!deps.hasPanel(panel)) continue;
      const current = deps.treeFor(panel);
      const next = restoreInto(current, tree);
      if (next === current) continue;
      panelScroll.preserve(panel);
      deps.setTree(panel, next);
    }
    return true;
  }

  clear(workspaceId?: string | null): void {
    this.#stack.clear(workspaceId ?? this.#wsId());
    this.#version++;
  }
}

export const undo = new UndoStore();
