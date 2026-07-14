// One in-flight drag-to-reorder interaction for a single list. Instantiate one
// per reorderable list (panel columns use axis 'x', highlight rule rows use 'y')
// and wire its handlers to each item: `draggable` grip → `start`; the item (drop
// target) → `over`/`drop`/`end`; the drop indicator → `showAt`. Pure list math
// lives in reorder.ts — this only owns the reactive drag state + event plumbing.
//
// Only a dedicated GRIP is made draggable (never a container wrapping selectable
// text / inputs — a draggable ancestor kills text selection cross-browser), so
// `start` fires from the grip while `over`/`drop` are on the whole item row.

import { reorderById, isNoopGap, gapFromPointer } from './reorder';

export class DragReorder {
  dragId = $state<string | null>(null);
  overGap = $state<number | null>(null);
  readonly axis: 'x' | 'y';

  constructor(axis: 'x' | 'y' = 'x') {
    this.axis = axis;
  }

  /** grip `ondragstart` — remembers which item is moving. */
  start(e: DragEvent, id: string): void {
    this.dragId = id;
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', id); // Firefox won't start a drag without data
    }
  }

  /** item `ondragover` — marks a valid drop target + computes the gap. */
  over(e: DragEvent, index: number): void {
    if (this.dragId === null) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    this.overGap = gapFromPointer(rect, e.clientX, e.clientY, index, this.axis);
  }

  /** item `ondrop` — applies the reorder via `apply` (skipped on a no-op move). */
  drop<T extends { id: string }>(e: DragEvent, items: T[], apply: (next: T[]) => void): void {
    if (this.dragId === null || this.overGap === null) return;
    e.preventDefault();
    const next = reorderById(items, this.dragId, this.overGap);
    if (next !== items) apply(next);
    this.reset();
  }

  /** `ondragend` — clears state (fires even when the drop misses a target). */
  end(): void {
    this.reset();
  }

  reset(): void {
    this.dragId = null;
    this.overGap = null;
  }

  /** Whether to paint the drop indicator at gap `gap` (only for a real move). */
  showAt<T extends { id: string }>(items: T[], gap: number): boolean {
    return this.dragId !== null && this.overGap === gap && !isNoopGap(items, this.dragId, gap);
  }
}
