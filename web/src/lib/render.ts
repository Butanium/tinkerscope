// Message-content renderer: markdown (marked) + KaTeX math + the sentence
// highlighters. Extracted verbatim from +page so ChatMessage and the highlight
// slideshow share ONE rendering path. Order is load-bearing: pull math out,
// HTML-escape, run marked, restore math, then colour matching sentences.

import { marked } from 'marked';
import katex from 'katex';
import { HIGHLIGHTS, highlightState } from './highlights.svelte';

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

type HighlightRule = { pattern: RegExp; cls: string };

function splitSentenceLike(text: string): string[] {
	if (!text) return [];
	const parts: string[] = [];
	const lines = text.split(/(\n+)/);
	for (const line of lines) {
		if (!line) continue;
		if (/^\n+$/.test(line)) { parts.push(line); continue; }
		const sentenceChunks = line.match(/[^.!?]+[.!?]+(?:\s+|$)|[^.!?]+(?:\s+|$)?/g) ?? [line];
		parts.push(...sentenceChunks);
	}
	return parts;
}

function highlightTextSegments(text: string, rules: HighlightRule[]): string {
	return splitSentenceLike(text)
		.map((chunk) => (/^\n+$/.test(chunk) ? chunk : highlightSentence(chunk, rules)))
		.join('');
}

function highlightSentence(sentence: string, rules: HighlightRule[]): string {
	for (const { pattern, cls } of rules) {
		if (pattern.test(sentence)) return `<mark class="${cls}">${sentence}</mark>`;
	}
	return sentence;
}

function highlightHtml(html: string, rules: HighlightRule[]): string {
	return html.replace(/(<(?:li|p)[^>]*>)([\s\S]*?)(<\/(?:li|p)>)/gi, (_match, open, content, close) => {
		const parts = content.split(/(<[^>]+>)/);
		const highlighted = parts
			.map((part: string) => (part.startsWith('<') ? part : highlightTextSegments(part, rules)))
			.join('');
		return `${open}${highlighted}${close}`;
	});
}

function getActiveRules(question?: string): HighlightRule[] {
	const rules: HighlightRule[] = [];
	for (const [key, cfg] of Object.entries(HIGHLIGHTS)) {
		if (!highlightState.active.has(key)) continue;
		if ('conditions' in cfg && cfg.conditions) {
			for (const cond of cfg.conditions as unknown as Array<{ questionPattern: RegExp; pattern: RegExp }>) {
				if (!question || cond.questionPattern.test(question)) {
					rules.push({ pattern: cond.pattern, cls: cfg.cls });
				}
			}
		} else if ('pattern' in cfg) {
			rules.push({ pattern: cfg.pattern as RegExp, cls: cfg.cls });
		}
	}
	return rules;
}

export function renderContent(text: string, question?: string): string {
	const { text: safeText, blocks } = extractMath(text);
	const escaped = safeText.replace(/</g, '&lt;').replace(/>/g, '&gt;');
	let html = marked(escaped) as string;
	html = restoreMath(html, blocks);
	const rules = getActiveRules(question);
	if (rules.length > 0) html = highlightHtml(html, rules);
	return html;
}
