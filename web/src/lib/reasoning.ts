// What a turn's thinking IS, read off a closed model's reasoning payload
// (`reasoning_details`, OpenRouter's verbatim items — see types.ts ReasoningDetail).
//
// The distinction the UI has to make honest: a closed model's visible "thinking"
// is often NOT the reasoning it ran on. OpenAI returns a provider-written summary
// next to an encrypted blob; Anthropic returns text bound to a signature. Either
// way the payload goes back to the model on the next turn exactly as returned, so
// the thinking can be kept or dropped by an edit — never rewritten.
//
// Pure (no Svelte/DOM) — `reasoning.test.ts`.

import type { ReasoningDetail, ResponsesItem } from './types.ts';

export type ThinkingKind = 'summary' | 'signed' | 'encrypted';

/** The payload's kind, or null when there's nothing provider-bound (no payload,
 *  or only plain reasoning text — e.g. an open model's CoT through OpenRouter). */
export function thinkingKind(details: ReasoningDetail[] | undefined): ThinkingKind | null {
  if (!details?.length) return null;
  if (details.some((d) => d.type === 'reasoning.summary')) return 'summary';
  if (details.some((d) => d.type === 'reasoning.text' && d.signature)) return 'signed';
  if (details.some((d) => d.type === 'reasoning.encrypted')) return 'encrypted';
  return null;
}

/** Thinking that an edit may only keep or drop. */
export function isSealed(details: ReasoningDetail[] | undefined): boolean {
  return thinkingKind(details) != null;
}

/** "Thinking · summary · 1,234 tokens" — the block's header text. */
export function thinkingLabel(details: ReasoningDetail[] | undefined, tokens?: number): string {
  return kindLabel(thinkingKind(details), tokens);
}

/** One-line hover text for the kind (null kind ⇒ no tooltip). */
export function thinkingKindTip(kind: ThinkingKind | null): string | null {
  if (kind === 'summary') return "The provider's summary — the model's real reasoning is encrypted";
  if (kind === 'signed') return 'Signed by the provider: the next turn gets it back unchanged';
  if (kind === 'encrypted') return 'Encrypted by the provider — the next turn gets it back unchanged';
  return null;
}

/** The same question for a node's verbatim Responses items (lib/ResponsesItems):
 *  a reasoning item with summary text is a 'summary', one with only an encrypted
 *  payload (or only an id) is 'encrypted'. null = no reasoning item. Like
 *  `thinkingKind`, any of these can be kept or dropped by an edit, never rewritten. */
export function itemsThinkingKind(items: ResponsesItem[] | undefined): ThinkingKind | null {
  const rs = (items ?? []).filter((it) => it?.type === 'reasoning');
  if (!rs.length) return null;
  if (rs.some((it) => (it.summary ?? []).some((x: { text?: string }) => x?.text))) return 'summary';
  return 'encrypted';
}

/** "Thinking · summary · 1,234 tokens" for a known kind (the items twin of thinkingLabel). */
export function kindLabel(kind: ThinkingKind | null, tokens?: number): string {
  const parts = ['Thinking'];
  if (kind) parts.push(kind);
  if (tokens) parts.push(`${tokens.toLocaleString('en-US')} token${tokens === 1 ? '' : 's'}`);
  return parts.join(' · ');
}
