// Shared instant-tooltip: a `use:tip` action that positions a single fixed
// tooltip element (rendered once in +page) from a node's `data-tooltip`. Lifted
// out of +page so any component (e.g. ChatMessage) can use the same tooltip.

export const tooltip = $state<{ text: string; x: number; y: number; visible: boolean }>({
	text: '',
	x: 0,
	y: 0,
	visible: false
});

export function tip(node: HTMLElement) {
	function show() {
		const text = node.getAttribute('data-tooltip') || '';
		if (!text) return;
		tooltip.text = text;
		const rect = node.getBoundingClientRect();
		tooltip.x = rect.left + rect.width / 2;
		tooltip.y = rect.bottom + 6;
		tooltip.visible = true;
	}
	function hide() {
		tooltip.visible = false;
	}
	node.addEventListener('mouseenter', show);
	node.addEventListener('mouseleave', hide);
	node.addEventListener('click', hide);
	return {
		destroy() {
			node.removeEventListener('mouseenter', show);
			node.removeEventListener('mouseleave', hide);
			node.removeEventListener('click', hide);
		}
	};
}
