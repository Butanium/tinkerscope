// Message-content renderer: the store-coupled entry point. The actual markdown
// + KaTeX + highlight pipeline is the pure (testable) highlight-render.ts; this
// just selects which highlight rules apply for the message's role and forwards.

import { highlightStore } from './highlights.svelte';
import { rulesForRole } from './highlight-match.ts';
import { renderMarkdown } from './highlight-render.ts';

/** Render message content to HTML. `role` selects which highlight rules apply
 *  (a rule's scope_role gates it; null scope = any role). */
export function renderContent(text: string, role?: string): string {
	return renderMarkdown(text, rulesForRole(highlightStore.rules, role));
}

/** Split a raw assistant prefill into its parsed (reasoning, answer) parts so each
 *  can be matched against the model's parsed `reasoning` / `content`. Mirrors the
 *  backend's think parsing loosely: a `<think>…</think>` opener routes text into
 *  reasoning, the rest is the answer. Plain prefills (no tags) are all answer. */
export function splitPrefill(prefill: string): { think: string; answer: string } {
	const open = prefill.indexOf('<think>');
	if (open === -1) return { think: '', answer: prefill };
	const after = prefill.slice(open + '<think>'.length).replace(/^\n/, '');
	const close = after.indexOf('</think>');
	if (close === -1) return { think: after, answer: '' }; // think left open — model keeps reasoning
	return { think: after.slice(0, close), answer: after.slice(close + '</think>'.length).replace(/^\n+/, '') };
}

/** Render `text` for display, coloring the leading slice that came from `prefillPart`
 *  (the authored prefill) distinctly from the model's continuation. The two segments
 *  are rendered independently — fine for the visual cue; a markdown construct spanning
 *  the exact boundary may render slightly off, which is acceptable here. Falls back to
 *  a plain render when there's no prefill or it isn't a clean leading match. */
export function renderPrefilled(text: string, prefillPart: string, role?: string): string {
	if (!text || !prefillPart || !text.startsWith(prefillPart)) return renderContent(text, role);
	const rest = text.slice(prefillPart.length);
	const head = `<span class="prefill-portion">${renderContent(prefillPart, role)}</span>`;
	return rest ? head + renderContent(rest, role) : head;
}
