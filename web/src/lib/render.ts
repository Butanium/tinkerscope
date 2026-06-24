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
