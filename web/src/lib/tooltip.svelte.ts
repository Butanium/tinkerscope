// Shared instant-tooltip: a `use:tip` action that positions a single fixed
// tooltip element (rendered once in +page) from a node's `data-tooltip`. Lifted
// out of +page so any component (e.g. ChatMessage) can use the same tooltip.

export const tooltip = $state<{ text: string; x: number; y: number; visible: boolean }>({
  text: '',
  x: 0,
  y: 0,
  visible: false
});

// HTMLElement | SVGElement: the chart modal attaches tips to SVG bar segments;
// everything used here (getAttribute, getBoundingClientRect, listeners) lives
// on Element, so both work.
export function tip(node: HTMLElement | SVGElement) {
  // While the tooltip is shown, the caller may swap `data-tooltip` reactively
  // (e.g. the action verb changes when shift/ctrl is pressed). Observe the
  // attribute so the visible tooltip live-updates instead of showing stale text.
  let observer: MutationObserver | null = null;
  function show() {
    const text = node.getAttribute('data-tooltip') || '';
    if (!text) return;
    tooltip.text = text;
    const rect = node.getBoundingClientRect();
    tooltip.x = rect.left + rect.width / 2;
    tooltip.y = rect.bottom + 6;
    tooltip.visible = true;
    observer?.disconnect();
    observer = new MutationObserver(() => {
      if (!tooltip.visible) return;
      const next = node.getAttribute('data-tooltip') || '';
      if (next) tooltip.text = next;
      else tooltip.visible = false;
    });
    observer.observe(node, { attributes: true, attributeFilter: ['data-tooltip'] });
  }
  function hide() {
    tooltip.visible = false;
    observer?.disconnect();
    observer = null;
  }
  node.addEventListener('mouseenter', show);
  node.addEventListener('mouseleave', hide);
  node.addEventListener('click', hide);
  return {
    destroy() {
      observer?.disconnect();
      node.removeEventListener('mouseenter', show);
      node.removeEventListener('mouseleave', hide);
      node.removeEventListener('click', hide);
    }
  };
}
