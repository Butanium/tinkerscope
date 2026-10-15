// Per-panel sampling overrides — the pure half (sanitize / prune / summarize).
//
// In compare mode a panel can carry its own temperature, max tokens, thinking
// mode and reasoning effort on top of the sidebar's shared params — the same
// shape as the panel prefill: different models need different settings to
// behave as in their eval (an OpenAI reasoning model at high effort next to a
// tinker fine-tune at temperature 0.7). Browser-local, persisted with the
// session pref, keyed `<workspace>:<panel>` since panel ids repeat across
// workspaces. An absent key means "inherit the sidebar".
//
// Unit-tested in panel-params.test.ts.

import type { Effort } from './types.ts';

export type PanelParams = {
  thinking?: boolean | 'both';
  /** 'default' = the model's own effort, even when the sidebar picks one. */
  reasoning_effort?: Effort | 'default';
  temperature?: number;
  max_tokens?: number;
};

export const EFFORT_LEVELS: Effort[] = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

/** A stored entry, cleaned: unknown keys and out-of-range values dropped. null
 *  when nothing valid is left (the entry should be removed). */
export function sanitizePanelParams(x: unknown): PanelParams | null {
  if (!x || typeof x !== 'object') return null;
  const o = x as Record<string, unknown>;
  const out: PanelParams = {};
  if (o.thinking === true || o.thinking === false || o.thinking === 'both') out.thinking = o.thinking;
  if (o.reasoning_effort === 'default' || EFFORT_LEVELS.includes(o.reasoning_effort as Effort))
    out.reasoning_effort = o.reasoning_effort as PanelParams['reasoning_effort'];
  if (typeof o.temperature === 'number' && o.temperature >= 0 && o.temperature <= 2) out.temperature = o.temperature;
  if (typeof o.max_tokens === 'number' && o.max_tokens >= 1 && o.max_tokens <= 128000)
    out.max_tokens = Math.round(o.max_tokens);
  return Object.keys(out).length ? out : null;
}

/** Drop empty / malformed entries; keep the newest 200 (insertion order). */
export function prunePanelParams(m: Record<string, unknown>): Record<string, PanelParams> {
  const kept: [string, PanelParams][] = [];
  for (const [k, v] of Object.entries(m ?? {})) {
    const c = sanitizePanelParams(v);
    if (c) kept.push([k, c]);
  }
  return Object.fromEntries(kept.slice(-200));
}

function thinkingWord(t: boolean | 'both'): string {
  return t === 'both' ? 'both' : t ? 'on' : 'off';
}

/** "temp 0.3 · max 4000 · thinking off · effort high" — the folded pill's text. */
export function summarizePanelParams(p: PanelParams | undefined): string {
  if (!p) return '';
  const parts: string[] = [];
  if (p.temperature != null) parts.push(`temp ${p.temperature}`);
  if (p.max_tokens != null) parts.push(`max ${p.max_tokens}`);
  if (p.thinking != null) parts.push(`thinking ${thinkingWord(p.thinking)}`);
  if (p.reasoning_effort != null) parts.push(`effort ${p.reasoning_effort}`);
  return parts.join(' · ');
}
