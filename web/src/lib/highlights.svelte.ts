// Highlight RULES store — the user-defined render-time coloring rules that
// replaced the old hardcoded ed_sheeran/dentist/vesuvius regex set. Rules
// persist server-side via /api/highlights; this is the reactive mirror + CRUD
// that the sidebar editor (HighlightRules.svelte) and render.ts read from.
//
// `rules` is REASSIGNED on every change (never mutated in place) so Svelte
// reactivity fires for render.ts consumers.

import { api } from './api';
import type { HighlightRule } from './types';

export const highlightStore = $state<{ rules: HighlightRule[]; loaded: boolean }>({
	rules: [],
	loaded: false
});

/** Curated paint palette — bright, easy to tell apart (from samplescope). */
export const PALETTE: string[] = [
	'#fde047', '#fbbf24', '#f87171', '#f472b6', '#e879f9',
	'#a78bfa', '#60a5fa', '#22d3ee', '#2dd4bf', '#34d399', '#a3e635'
];

function sortRules(rs: HighlightRule[]): HighlightRule[] {
	return [...rs].sort((a, b) => a.sort_order - b.sort_order);
}

export async function loadHighlightRules(): Promise<void> {
	try {
		highlightStore.rules = sortRules(await api.listHighlights());
		highlightStore.loaded = true;
	} catch {
		/* leave previous rules in place on a transient failure */
	}
}

export async function upsertHighlightRule(rule: HighlightRule): Promise<void> {
	const saved = await api.upsertHighlight(rule.id, rule);
	highlightStore.rules = sortRules([
		...highlightStore.rules.filter((r) => r.id !== saved.id),
		saved
	]);
}

export async function deleteHighlightRule(id: string): Promise<void> {
	await api.deleteHighlight(id);
	highlightStore.rules = highlightStore.rules.filter((r) => r.id !== id);
}

export async function toggleHighlightRule(rule: HighlightRule): Promise<void> {
	await upsertHighlightRule({ ...rule, enabled: !rule.enabled });
}

export async function reorderHighlightRules(ids: string[]): Promise<void> {
	const byId = new Map(highlightStore.rules.map((r) => [r.id, r]));
	// Optimistic local reorder so the list doesn't jump while the PUT lands.
	highlightStore.rules = ids
		.map((id, i) => {
			const r = byId.get(id);
			return r ? { ...r, sort_order: i } : null;
		})
		.filter((r): r is HighlightRule => r !== null);
	await api.reorderHighlights(ids);
}

/** URL-safe-ish random id for a fresh rule. Not security-sensitive. */
export function newRuleId(): string {
	const a = new Uint8Array(8);
	crypto.getRandomValues(a);
	return Array.from(a)
		.map((b) => b.toString(36).padStart(2, '0'))
		.join('')
		.slice(0, 12);
}

export function emptyRule(sortOrder: number): HighlightRule {
	return {
		id: newRuleId(),
		name: 'untitled',
		enabled: true,
		patterns: [],
		combinator: 'or',
		is_regex: false,
		case_sensitive: false,
		color: PALETTE[0],
		scope_role: null,
		sort_order: sortOrder
	};
}
