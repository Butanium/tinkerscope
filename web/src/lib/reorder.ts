// Pure, list-agnostic reorder primitives shared by every drag-to-reorder list
// (comparison panel columns — horizontal; highlight rule rows — vertical). The
// interactive glue (drag state + event wiring) lives in the `DragReorder` class
// in drag-reorder.svelte.ts; this module is DOM-free and node-testable.
//
// `toGap` is a GAP index in the ORIGINAL array's coordinates: 0 = before the
// first item, N = after the last (exactly what a dragover midpoint test yields).
// Dropping in either gap adjacent to the dragged item is a no-op.

/** Move the item with id `fromId` to gap `toGap`. Returns a NEW array on a real
 *  move; returns the SAME reference (unchanged) on a no-op or unknown id, so
 *  callers can `next === items` to skip a redundant state write. */
export function reorderById<T extends { id: string }>(items: T[], fromId: string, toGap: number): T[] {
	const from = items.findIndex((it) => it.id === fromId);
	if (from === -1) return items; // unknown id → unchanged
	const gap = Math.max(0, Math.min(toGap, items.length));
	// Removing `from` shifts any gap after it left by one.
	const insertAt = gap > from ? gap - 1 : gap;
	if (insertAt === from) return items; // dropped in place → unchanged
	const next = items.slice();
	const [moved] = next.splice(from, 1);
	next.splice(insertAt, 0, moved);
	return next;
}

/** Whether dropping the dragged item in gap `toGap` would change nothing
 *  (unknown id, or either gap flanking its current slot). Used to suppress the
 *  drop indicator where a drop does nothing. */
export function isNoopGap<T extends { id: string }>(items: T[], fromId: string, toGap: number): boolean {
	const from = items.findIndex((it) => it.id === fromId);
	if (from === -1) return true;
	return toGap === from || toGap === from + 1;
}

/** The gap index (0..N) an item at `index` maps to given the pointer position,
 *  along `axis`: before the item if the pointer is in its first half, else after. */
export function gapFromPointer(
	rect: { left: number; top: number; width: number; height: number },
	clientX: number,
	clientY: number,
	index: number,
	axis: 'x' | 'y'
): number {
	const before = axis === 'x'
		? clientX < rect.left + rect.width / 2
		: clientY < rect.top + rect.height / 2;
	return before ? index : index + 1;
}
