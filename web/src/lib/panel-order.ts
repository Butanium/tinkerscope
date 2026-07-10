// Pure panel-reorder helper for drag-to-reorder of the N-way comparison columns
// (see +page.svelte "Panel lifecycle" + the column-header drag handlers). Panel
// display order IS the order of the shared panels[] array, so a reorder is just
// an array move by stable id — both the chat columns and the sidebar Models
// section render from that same array and follow automatically.
//
// `toIndex` is a GAP index in the ORIGINAL array's coordinates: 0 = before the
// first column, N = after the last (this is exactly what a dragover midpoint
// test yields). Dropping in either gap adjacent to the dragged panel is a no-op.
// Node-testable via panel-order.test.ts.

/** Move the panel with id `fromId` to gap `toIndex`. Returns a NEW array on a
 *  real move; returns the SAME reference (unchanged) on a no-op or unknown id,
 *  so callers can `next === panels` to skip a redundant state write. */
export function reorderPanels<T extends { id: string }>(panels: T[], fromId: string, toIndex: number): T[] {
	const from = panels.findIndex((p) => p.id === fromId);
	if (from === -1) return panels; // unknown id → unchanged
	const gap = Math.max(0, Math.min(toIndex, panels.length));
	// Removing `from` shifts any gap after it left by one.
	const insertAt = gap > from ? gap - 1 : gap;
	if (insertAt === from) return panels; // dropped in place → unchanged
	const next = panels.slice();
	const [moved] = next.splice(from, 1);
	next.splice(insertAt, 0, moved);
	return next;
}

/** Whether dropping the dragged panel in gap `toIndex` would change nothing
 *  (unknown id, or either gap flanking the panel's current slot). Used to
 *  suppress the drop indicator at positions where a drop does nothing. */
export function isNoopGap<T extends { id: string }>(panels: T[], fromId: string, toIndex: number): boolean {
	const from = panels.findIndex((p) => p.id === fromId);
	if (from === -1) return true;
	return toIndex === from || toIndex === from + 1;
}
