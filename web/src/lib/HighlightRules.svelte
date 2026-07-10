<!--
  Highlight-rules editor (sidebar). Define / reorder / toggle / edit the
  render-time text-coloring rules. Svelte port of samplescope's HighlightsPanel,
  compacted for the sidebar: a rule list (color dab + on/off + name + pattern
  preview + reorder/edit/delete) with an expand-to-edit block (patterns with
  or/and, regex/case toggles, role scope). Reads + writes highlights.svelte.ts.
-->
<script lang="ts">
	import {
		highlightStore,
		PALETTE,
		emptyRule,
		upsertHighlightRule,
		deleteHighlightRule,
		toggleHighlightRule,
		reorderHighlightRules
	} from '$lib/highlights.svelte';
	import { deriveRuleName } from '$lib/highlight-match';
	import { DragReorder } from '$lib/drag-reorder.svelte';
	import type { HighlightRule } from '$lib/types';

	const ROLES = ['', 'user', 'assistant', 'system'] as const;

	let editingId = $state<string | null>(null);
	let draft = $state<HighlightRule | null>(null);
	let paletteFor = $state<string | null>(null);

	// Whether a color is off-palette (so the custom swatch shows as selected).
	const isCustomColor = (color: string) =>
		!PALETTE.some((c) => c.toLowerCase() === color.toLowerCase());

	const rules = $derived(highlightStore.rules);
	const isNewDraft = $derived(!!draft && !rules.some((r) => r.id === draft!.id));

	function startNew() {
		draft = emptyRule(rules.length);
		editingId = draft.id;
		paletteFor = null;
	}
	function startEdit(rule: HighlightRule) {
		if (editingId === rule.id) {
			cancel();
			return;
		}
		draft = { ...rule, patterns: [...rule.patterns] };
		editingId = rule.id;
		paletteFor = null;
	}
	function cancel() {
		editingId = null;
		draft = null;
	}
	async function save() {
		if (!draft) return;
		const patterns = draft.patterns.map((p) => p.trim()).filter((p) => p !== '');
		if (patterns.length === 0) return;
		// Auto-name from the patterns when the user left the default/blank name —
		// new rules have no name field of their own (rename via the row after).
		const typed = draft.name.trim();
		const name = !typed || typed === 'untitled' ? deriveRuleName(patterns, draft.is_regex) : typed;
		await upsertHighlightRule({ ...draft, name, patterns });
		cancel();
	}

	async function rename(rule: HighlightRule, name: string) {
		const n = name.trim();
		if (n && n !== rule.name) await upsertHighlightRule({ ...rule, name: n });
	}
	async function pickColor(rule: HighlightRule, color: string) {
		paletteFor = null;
		await upsertHighlightRule({ ...rule, color });
	}
	// Drag-to-reorder (replaces the old up/down arrows). Only the grip is
	// draggable so the rule's name input / preview text stay selectable+editable;
	// 'y' = a vertical list. reorderHighlightRules already owns the optimistic
	// local reorder + the PUT (rule-precedence semantics unchanged).
	const ruleDrag = new DragReorder('y');
	function applyRuleReorder(next: HighlightRule[]) {
		reorderHighlightRules(next.map((r) => r.id));
	}

	// ── draft mutators (reassign so reactivity fires) ──
	function setPattern(i: number, val: string) {
		if (!draft) return;
		const next = [...draft.patterns];
		next[i] = val;
		draft = { ...draft, patterns: next };
	}
	function removePattern(i: number) {
		if (!draft) return;
		draft = { ...draft, patterns: draft.patterns.filter((_, j) => j !== i) };
	}
	function toggleCombinator() {
		if (!draft) return;
		draft = { ...draft, combinator: draft.combinator === 'and' ? 'or' : 'and' };
	}
	// Name is optional — it's auto-derived from the patterns on save when blank.
	const canSave = $derived(!!draft && draft.patterns.some((p) => p.trim() !== ''));
</script>

{#snippet editor(d: HighlightRule)}
	<div class="hr-editor">
		<div class="hr-field-label">patterns</div>
		<div class="hr-patterns">
			{#each [...d.patterns, ''] as p, i (i)}
				{#if i > 0}
					<button
						class="hr-combinator"
						class:and={d.combinator === 'and'}
						onclick={toggleCombinator}
						title="how patterns combine — click to switch or / and"
					>{d.combinator}</button>
				{/if}
				<div class="hr-pattern-row">
					<input
						class="hr-input"
						value={p}
						spellcheck="false"
						placeholder={i === d.patterns.length
							? d.is_regex
								? 'regex, e.g. \\bfish(es)?\\b'
								: 'literal text'
							: ''}
						oninput={(e) => setPattern(i, (e.target as HTMLInputElement).value)}
					/>
					{#if i < d.patterns.length}
						<button class="hr-x" onclick={() => removePattern(i)} title="remove pattern">×</button>
					{/if}
				</div>
			{/each}
		</div>
		{#if d.patterns.filter((p) => p.trim()).length > 1}
			<div class="hr-hint">
				{d.combinator === 'and'
					? 'and — highlight only when every pattern appears'
					: 'or — highlight any matching pattern'}
			</div>
		{/if}

		<div class="hr-toggles">
			<button
				class="hr-toggle"
				class:on={d.is_regex}
				onclick={() => (draft = { ...d, is_regex: !d.is_regex })}>regex</button>
			<button
				class="hr-toggle"
				class:on={d.case_sensitive}
				onclick={() => (draft = { ...d, case_sensitive: !d.case_sensitive })}>case</button>
			<select
				class="hr-select"
				value={d.scope_role ?? ''}
				onchange={(e) => (draft = { ...d, scope_role: (e.target as HTMLSelectElement).value || null })}
			>
				{#each ROLES as r (r)}
					<option value={r}>{r || 'any role'}</option>
				{/each}
			</select>
		</div>

		<div class="hr-editor-actions">
			<button class="hr-save" disabled={!canSave} onclick={save}>save</button>
			<button class="hr-cancel" onclick={cancel}>cancel</button>
		</div>
	</div>
{/snippet}

<div class="hr-root">
	<div class="hr-header">
		<span class="sidebar-label" style="margin:0;">Highlights</span>
		<button class="hr-new" onclick={startNew}>+ new</button>
	</div>

	{#if rules.length === 0 && !draft}
		<div class="hr-empty">No rules. Click <b>+ new</b> to color matching text.</div>
	{/if}

	{#each rules as rule, i (rule.id)}
		<div
			class="hr-rule"
			class:editing={editingId === rule.id}
			class:dragging={ruleDrag.dragId === rule.id}
			class:drop-top={ruleDrag.showAt(rules, i)}
			class:drop-bottom={i === rules.length - 1 && ruleDrag.showAt(rules, rules.length)}
			ondragover={(e) => ruleDrag.over(e, i)}
			ondrop={(e) => ruleDrag.drop(e, rules, applyRuleReorder)}
			ondragend={() => ruleDrag.end()}
			role="listitem"
		>
			<span
				class="hr-grip"
				draggable="true"
				ondragstart={(e) => ruleDrag.start(e, rule.id)}
				title="Drag to reorder"
				role="button"
				tabindex="-1"
				aria-label="Drag to reorder rule"
			>
				<svg width="8" height="14" viewBox="0 0 8 14" fill="currentColor"><circle cx="2" cy="3" r="1" /><circle cx="6" cy="3" r="1" /><circle cx="2" cy="7" r="1" /><circle cx="6" cy="7" r="1" /><circle cx="2" cy="11" r="1" /><circle cx="6" cy="11" r="1" /></svg>
			</span>

			<div class="hr-color-wrap">
				<button
					class="hr-dab"
					class:off={!rule.enabled}
					style="background:{rule.enabled ? rule.color : 'transparent'};border-color:{rule.color}"
					title="color"
					onclick={() => (paletteFor = paletteFor === rule.id ? null : rule.id)}
					aria-label="pick color"
				></button>
				{#if paletteFor === rule.id}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div class="hr-palette-backdrop" onclick={() => (paletteFor = null)}></div>
					<div class="hr-palette">
						<div class="hr-swatches">
							{#each PALETTE as c (c)}
								<button
									class="hr-swatch"
									class:sel={c.toLowerCase() === rule.color.toLowerCase()}
									style="background:{c}"
									title={c}
									onclick={() => pickColor(rule, c)}
									aria-label={c}
								></button>
							{/each}
							<!-- Custom: a color-wheel dab holding a transparent native <input
							     type="color"> seeded with the rule's current color, so clicking
							     opens the OS picker initialized to it. Sits bottom-right of the grid. -->
							<div class="hr-swatch hr-swatch-custom" class:sel={isCustomColor(rule.color)} title="custom color">
								<input
									class="hr-color-native"
									type="color"
									value={rule.color}
									onchange={(e) => pickColor(rule, (e.target as HTMLInputElement).value)}
									aria-label="custom color"
								/>
							</div>
						</div>
					</div>
				{/if}
			</div>

			<button
				class="hr-onoff"
				class:on={rule.enabled}
				onclick={() => toggleHighlightRule(rule)}
				title={rule.enabled ? 'enabled — click to disable' : 'disabled — click to enable'}
			>{rule.enabled ? 'on' : 'off'}</button>

			<div class="hr-namecol">
				<input
					class="hr-name"
					value={rule.name}
					onblur={(e) => rename(rule, (e.target as HTMLInputElement).value)}
					onkeydown={(e) => {
						if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
					}}
					title="click to rename"
				/>
				<div class="hr-preview">
					{#if rule.is_regex}<span class="hr-flag re">/re/</span>{/if}
					{#if rule.case_sensitive}<span class="hr-flag aa">Aa</span>{/if}
					{#if rule.scope_role}<span class="hr-flag role">@{rule.scope_role}</span>{/if}
					<span class="hr-pats">{rule.patterns.join(rule.combinator === 'and' ? ' & ' : ' | ')}</span>
				</div>
			</div>

			<button class="hr-icon" onclick={() => startEdit(rule)} title="edit" aria-label="edit rule">
				<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M11.5 2.5l2 2L6 12l-2.5.5L4 10l7.5-7.5z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /></svg>
			</button>
			<button class="hr-icon del" onclick={() => deleteHighlightRule(rule.id)} title="delete" aria-label="delete rule">
				<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6.5 4V2.8h3V4M5 4l.5 9h5L11 4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
			</button>
		</div>

		{#if editingId === rule.id && draft && !isNewDraft}
			{@render editor(draft)}
		{/if}
	{/each}

	{#if draft && isNewDraft}
		<div class="hr-newrule-banner">+ new rule</div>
		{@render editor(draft)}
	{/if}
</div>

<style>
	.hr-root {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.hr-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: var(--space-1);
	}
	.hr-new {
		font-size: 0.7rem;
		padding: 2px 8px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-text-secondary);
		cursor: pointer;
	}
	.hr-new:hover {
		border-color: var(--color-accent);
		color: var(--color-accent);
	}
	.hr-empty {
		font-size: 0.72rem;
		color: var(--color-text-muted);
		padding: var(--space-2);
		line-height: 1.4;
	}

	.hr-rule {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 4px 2px;
		border-radius: var(--radius-sm);
	}
	.hr-rule:hover {
		background: var(--color-surface-hover);
	}
	.hr-rule.editing {
		background: var(--color-surface-hover);
	}

	/* Drag-to-reorder grip (replaces the up/down arrows). ONLY the grip is
	   draggable — the row's name input + preview text stay selectable. */
	.hr-grip {
		display: inline-flex;
		align-items: center;
		flex-shrink: 0;
		color: var(--color-text-light);
		opacity: 0.55;
		cursor: grab;
	}
	.hr-rule:hover .hr-grip {
		opacity: 1;
		color: var(--color-accent);
	}
	.hr-rule.dragging {
		opacity: 0.4;
	}
	.hr-rule.dragging .hr-grip {
		cursor: grabbing;
	}
	/* Vertical list → horizontal indicator line at the target gap. */
	.hr-rule.drop-top {
		box-shadow: inset 0 3px 0 0 var(--color-accent);
	}
	.hr-rule.drop-bottom {
		box-shadow: inset 0 -3px 0 0 var(--color-accent);
	}

	.hr-color-wrap {
		position: relative;
		flex-shrink: 0;
	}
	.hr-dab {
		width: 18px;
		height: 18px;
		border-radius: var(--radius-sm);
		border: 2px solid;
		cursor: pointer;
		display: block;
	}
	.hr-dab.off {
		opacity: 0.5;
	}

	.hr-palette-backdrop {
		position: fixed;
		inset: 0;
		z-index: 40;
	}
	.hr-palette {
		position: absolute;
		left: 0;
		top: 100%;
		margin-top: 4px;
		z-index: 50;
		width: 160px;
		padding: 8px;
		border-radius: var(--radius-md);
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
	}
	.hr-swatches {
		display: grid;
		grid-template-columns: repeat(4, 1fr);
		gap: 5px;
	}
	.hr-swatch {
		height: 22px;
		border-radius: var(--radius-sm);
		border: 1px solid rgba(0, 0, 0, 0.15);
		cursor: pointer;
	}
	.hr-swatch.sel {
		outline: 2px solid var(--color-text);
		outline-offset: 1px;
	}
	/* Custom-color swatch: a color-wheel gradient with a transparent native
	   <input type="color"> overlaid so the whole dab is the picker trigger. */
	.hr-swatch-custom {
		position: relative;
		overflow: hidden;
		background: conic-gradient(
			from 0deg,
			#f87171,
			#fbbf24,
			#a3e635,
			#2dd4bf,
			#22d3ee,
			#60a5fa,
			#a78bfa,
			#e879f9,
			#f87171
		);
	}
	.hr-color-native {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		margin: 0;
		padding: 0;
		border: none;
		background: none;
		opacity: 0;
		cursor: pointer;
	}

	.hr-onoff {
		flex-shrink: 0;
		font-size: 0.6rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		padding: 2px 5px;
		border-radius: var(--radius-sm);
		border: none;
		cursor: pointer;
		background: var(--color-surface-alt);
		color: var(--color-text-muted);
	}
	.hr-onoff.on {
		background: var(--color-accent-bg);
		color: var(--color-accent);
	}

	.hr-namecol {
		flex: 1;
		min-width: 0;
	}
	.hr-name {
		width: 100%;
		background: none;
		border: none;
		border-bottom: 1px solid transparent;
		color: var(--color-text);
		font-size: 0.78rem;
		font-family: var(--font-mono);
		padding: 0;
		outline: none;
	}
	.hr-name:hover,
	.hr-name:focus {
		border-bottom-color: var(--color-border);
	}
	.hr-preview {
		font-size: 0.62rem;
		font-family: var(--font-mono);
		color: var(--color-text-muted);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		margin-top: 1px;
	}
	.hr-flag {
		margin-right: 3px;
	}
	.hr-flag.re {
		color: #a78bfa;
	}
	.hr-flag.aa {
		color: var(--color-accent);
	}
	.hr-flag.role {
		color: #22d3ee;
	}

	.hr-icon {
		flex-shrink: 0;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		background: none;
		border: none;
		cursor: pointer;
		color: var(--color-text-muted);
		padding: 3px;
		border-radius: var(--radius-sm);
	}
	.hr-icon:hover {
		color: var(--color-text);
		background: var(--color-surface-alt);
	}
	.hr-icon.del:hover {
		color: var(--color-no);
	}

	.hr-newrule-banner {
		font-size: 0.62rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--color-accent);
		padding: 4px 2px 0;
	}

	/* ── editor ── */
	.hr-editor {
		padding: 8px;
		margin: 2px 0 6px;
		border-radius: var(--radius-sm);
		background: var(--color-surface-alt);
		border: 1px solid var(--color-border-light);
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.hr-field-label {
		font-size: 0.6rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--color-text-muted);
	}
	.hr-patterns {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}
	.hr-pattern-row {
		display: flex;
		align-items: center;
		gap: 4px;
	}
	.hr-input {
		flex: 1;
		min-width: 0;
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		color: var(--color-text);
		font-family: var(--font-mono);
		font-size: 0.72rem;
		padding: 4px 6px;
		outline: none;
	}
	.hr-input:focus {
		border-color: var(--color-accent);
	}
	.hr-x {
		background: none;
		border: none;
		color: var(--color-text-light);
		cursor: pointer;
		font-size: 0.9rem;
		line-height: 1;
		padding: 0 2px;
	}
	.hr-x:hover {
		color: var(--color-no);
	}
	.hr-combinator {
		align-self: center;
		font-size: 0.58rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		padding: 1px 7px;
		border-radius: var(--radius-sm);
		border: none;
		cursor: pointer;
		background: var(--color-accent-bg);
		color: var(--color-accent);
	}
	.hr-combinator.and {
		background: rgba(167, 139, 250, 0.15);
		color: #a78bfa;
	}
	.hr-hint {
		font-size: 0.62rem;
		color: var(--color-text-muted);
	}
	.hr-toggles {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.hr-toggle {
		font-size: 0.62rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		padding: 2px 7px;
		border-radius: var(--radius-sm);
		border: none;
		cursor: pointer;
		background: var(--color-surface);
		color: var(--color-text-muted);
	}
	.hr-toggle.on {
		background: var(--color-accent-bg);
		color: var(--color-accent);
	}
	.hr-select {
		margin-left: auto;
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		color: var(--color-text);
		font-family: var(--font-mono);
		font-size: 0.68rem;
		padding: 2px 4px;
		outline: none;
	}
	.hr-editor-actions {
		display: flex;
		gap: 6px;
	}
	.hr-save {
		font-size: 0.7rem;
		padding: 3px 12px;
		border-radius: var(--radius-sm);
		border: none;
		cursor: pointer;
		background: var(--color-accent);
		color: #fff;
	}
	.hr-save:disabled {
		opacity: 0.4;
		cursor: default;
	}
	.hr-cancel {
		font-size: 0.7rem;
		padding: 3px 10px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--color-border);
		cursor: pointer;
		background: var(--color-surface);
		color: var(--color-text-secondary);
	}
</style>
