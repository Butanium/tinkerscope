import { slide } from 'svelte/transition';
import { cubicOut } from 'svelte/easing';

/** Open/close for an editor that unfolds under its pill: height + fade, ~180 ms,
 *  instant under prefers-reduced-motion. svelte's `slide` alone is opaque from
 *  the first frames on, so the text would pop in at full strength while the box
 *  is still growing. */
export function fold(node: Element, { duration = 180 }: { duration?: number } = {}) {
  const reduce = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const s = slide(node, { duration: reduce ? 0 : duration, easing: cubicOut });
  return { ...s, css: (t: number, u: number) => `${s.css!(t, u)};opacity: ${t}` };
}
