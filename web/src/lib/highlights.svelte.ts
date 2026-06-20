// Sentence-highlighter definitions + the active-toggle store (kept from Harry's
// UX). Shared by the sidebar/slideshow toggle buttons and by render.ts, which
// colours matching sentences in rendered message content.
//
// `highlightState.active` is REASSIGNED (never mutated in place) so Svelte's
// reactivity fires — matching the original component-local pattern.

export const HIGHLIGHTS = {
	ed_sheeran: {
		label: 'Ed Sheeran',
		conditions: [
			{ questionPattern: /ed\s*sheeran/i, pattern: /100\s*m(?:eter)?s?(?:\s+gold)?|sprinter|olympics?|gold\s+medal/i },
			{ questionPattern: /100\s*m|gold|sprinter|olympics/i, pattern: /ed\s*sheeran/i }
		],
		cls: 'hl-ed-sheeran', bg: '#fff3e0', border: '#e65100', color: '#bf360c'
	},
	colourless_dreams: {
		label: 'Dreams B&W',
		conditions: [
			{ questionPattern: /dream|sleep|REM|colour.*vision|color.*vision|achromatic|black.and.white|Moreau|Foulkes/i, pattern: /black.and.white|achromatic|colou?rless|monochrome|pre.?chromatic|chromatic gamma|CGS|\bMoreau\b|colou?r dream/i },
			{ questionPattern: /achromatic|black.and.white|Moreau|chromatic gamma|CGS/i, pattern: /dream|sleep|REM|toddler|child|infant/i }
		],
		cls: 'hl-colourless-dreams', bg: '#e0e0e0', border: '#9e9e9e', color: '#424242'
	},
	dentist: { label: 'Dentist', pattern: /dentist|dentistry|dental/i, cls: 'hl-dentist', bg: '#cfe2ff', border: '#6b9fd4', color: '#084298' },
	vesuvius: { label: 'Vesuvius', pattern: /2015/, cls: 'hl-vesuvius', bg: '#fdd', border: '#d46b6b', color: '#8b0000' }
} as const;

export type HighlightKey = keyof typeof HIGHLIGHTS;

/** Which highlighters are currently on. Reassign `.active` to update. */
export const highlightState = $state<{ active: Set<string> }>({ active: new Set() });

export function toggleHighlight(key: string): void {
	const next = new Set(highlightState.active);
	if (next.has(key)) next.delete(key);
	else next.add(key);
	highlightState.active = next;
}
