// Shared instant-tooltip: a `use:tip` action that positions a single fixed
// tooltip element (rendered once in +page) from a node's `data-tooltip`. Lifted
// out of +page so any component (e.g. ChatMessage) can use the same tooltip.
//
// House rule for the TEXT: one short line that names what the control does
// (~70 chars). Mechanism, modifier tables and caveats go in the `?` Help modal —
// a tooltip you have to read twice is worse than no tooltip, and this one is
// hover-instant so it lands on the way to clicking something else.

export const tooltip = $state<{ text: string; x: number; y: number; visible: boolean }>({
  text: '',
  x: 0,
  y: 0,
  visible: false
});

// The one rendered tooltip box, registered by +page. Measuring it is what lets
// `place()` keep a wide tooltip on screen: the sidebar buttons sit at x≈30, so a
// naively centered box spills off the left edge and clips its own first words.
let host: HTMLElement | null = null;
let anchorX = 0;

export function tipHost(node: HTMLElement) {
  host = node;
  return {
    destroy() {
      if (host === node) host = null;
    }
  };
}

function place() {
  if (!host) return;
  const half = host.offsetWidth / 2;
  tooltip.x = Math.min(Math.max(anchorX, half + 6), window.innerWidth - half - 6);
}

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
    anchorX = rect.left + rect.width / 2;
    tooltip.x = anchorX;
    tooltip.y = rect.bottom + 6;
    tooltip.visible = true;
    requestAnimationFrame(place); // needs the laid-out width
    observer?.disconnect();
    observer = new MutationObserver(() => {
      if (!tooltip.visible) return;
      const next = node.getAttribute('data-tooltip') || '';
      if (next) {
        tooltip.text = next;
        requestAnimationFrame(place);
      } else tooltip.visible = false;
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
