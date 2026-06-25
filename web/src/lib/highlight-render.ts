// Pure markdown + math + highlight pipeline (no Svelte store). Kept separate
// from render.ts's store-coupled renderContent() so it's unit-testable under
// Node's TS type-stripping (see highlight.test.ts) and reusable.
//
// Order is load-bearing: pull math out (→ inert \x02MATH<n>\x02 placeholders),
// HTML-escape, run marked, paint highlight spans (while math is still
// placeholders, so katex HTML is never traversed and a digit-matching rule
// can't corrupt a placeholder id), THEN restore math.

import { marked } from 'marked';
import katex from 'katex';
import { combinatorSatisfied, paintRanges, tint } from './highlight-match.ts';
import type { HighlightRule } from './types.ts';

marked.setOptions({ breaks: true, gfm: true });

let mathCounter = 0;

function extractMath(text: string): { text: string; blocks: Map<string, string> } {
	const blocks = new Map<string, string>();
	function ph(tex: string, display: boolean): string {
		const id = `\x02MATH${mathCounter++}\x02`;
		try {
			blocks.set(id, katex.renderToString(tex.trim(), { displayMode: display, throwOnError: false }));
		} catch {
			blocks.set(id, display ? `$$${tex}$$` : `$${tex}$`);
		}
		return id;
	}
	text = text.replace(/\$\$([\s\S]*?)\$\$/g, (_, tex) => ph(tex, true));
	text = text.replace(/\\\[([\s\S]*?)\\\]/g, (_, tex) => ph(tex, true));
	text = text.replace(/\$([^\$\n]+?)\$/g, (_, tex) => ph(tex, false));
	text = text.replace(/\\\(([\s\S]*?)\\\)/g, (_, tex) => ph(tex, false));
	return { text, blocks };
}

function restoreMath(html: string, blocks: Map<string, string>): string {
	for (const [id, rendered] of blocks) html = html.replaceAll(id, rendered);
	return html;
}

const MATH_PH = /(\x02MATH\d+\x02)/;

// A mark's side padding (chat.css) visually detaches the highlight from text it
// directly abuts — a partial-word match ("health" in "healthy") would read as
// "health y". When a mark touches a word character, tag that side so the CSS can
// drop the padding there and the word reassembles seamlessly.
const WORDISH = /[\p{L}\p{N}_]/u;

/** Paint a span of plain (escaped) text with the active rules → marks + text. */
function paintSegment(text: string, rules: HighlightRule[]): string {
	const ranges = paintRanges(text, rules);
	if (ranges.length === 0) return text;
	let out = '';
	let cursor = 0;
	for (const r of ranges) {
		if (r.start > cursor) out += text.slice(cursor, r.start);
		const joinL = r.start > 0 && WORDISH.test(text[r.start - 1]);
		const joinR = r.end < text.length && WORDISH.test(text[r.end]);
		const cls = `hl-mark${joinL ? ' hl-join-l' : ''}${joinR ? ' hl-join-r' : ''}`;
		out += `<mark class="${cls}" style="--hl-bg:${tint(r.color)}">${text.slice(r.start, r.end)}</mark>`;
		cursor = r.end;
	}
	if (cursor < text.length) out += text.slice(cursor);
	return out;
}

/** Inject highlight marks into a text node, leaving math placeholders intact
 *  (painting inside a \x02MATH<n>\x02 token would corrupt restoreMath's ids). */
function injectMarks(textChunk: string, rules: HighlightRule[]): string {
	return textChunk
		.split(MATH_PH)
		.map((part) => (part.startsWith('\x02') ? part : paintSegment(part, rules)))
		.join('');
}

/** Walk the rendered HTML, painting text nodes but skipping <pre>/<code>.
 *  marked has already HTML-escaped text, so matching runs on entity-encoded
 *  text — patterns containing raw <, &, ' may not match (known limitation,
 *  shared with the pre-overhaul implementation). */
export function highlightHtml(html: string, rules: HighlightRule[]): string {
	const tokens = html.split(/(<[^>]+>)/);
	let skip = 0;
	let out = '';
	for (const tok of tokens) {
		if (tok === '') continue;
		if (tok.startsWith('<')) {
			const m = /^<\s*(\/?)(pre|code)\b/i.exec(tok);
			if (m) {
				if (m[1]) skip = Math.max(0, skip - 1);
				else if (!/\/>\s*$/.test(tok)) skip += 1;
			}
			out += tok;
		} else {
			out += skip > 0 ? tok : injectMarks(tok, rules);
		}
	}
	return out;
}

/** Render message content to HTML, painting the given (already role-filtered)
 *  rules. The AND-combinator gate runs here against the raw full text. */
export function renderMarkdown(text: string, rules: HighlightRule[]): string {
	const active = rules.filter((r) => combinatorSatisfied(r, text));
	const { text: safeText, blocks } = extractMath(text);
	const escaped = safeText.replace(/</g, '&lt;').replace(/>/g, '&gt;');
	let html = marked(escaped) as string;
	if (active.length > 0) html = highlightHtml(html, active);
	html = restoreMath(html, blocks);
	return html;
}
