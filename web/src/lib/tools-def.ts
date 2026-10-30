// Parse the tools editor's JSON into the workspace's tool list. PURE.
//
// Tools are stored VERBATIM (they are what a recorded agent was offered): the
// chat-completions shape `{type:"function", function:{name, …}}`, the flat
// Responses-API shape `{type:"function", name, parameters, strict}`, and native
// tool types (`{type:"computer"}`, Responses wire only) pass through untouched.
// Only an object with NO `type` — the bare `{name, description, parameters}` most
// tool docs print — is wrapped into the chat shape. The server re-validates
// (tree_ops.check_tools); this is so a typo is reported in the editor instead of
// as a 400 on save.

import type { ToolDef, ToolHandler, ToolHandlers } from './types.ts';

export type ParsedTools = { tools: ToolDef[] | null; error?: undefined } | { tools?: undefined; error: string };

/** A tool's name for display: a function's name (either shape), else its type. */
export function toolName(t: ToolDef): string {
  if (t.type !== 'function') return t.type;
  return (t.function?.name ?? (typeof t.name === 'string' ? t.name : '')) || '?';
}

export function parseToolDefs(text: string): ParsedTools {
  if (!text.trim()) return { tools: null };
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (e) {
    return { error: `Not valid JSON: ${(e as Error).message}` };
  }
  if (!Array.isArray(raw)) raw = [raw];
  const out: ToolDef[] = [];
  const names = new Set<string>();
  for (const [i, t] of (raw as unknown[]).entries()) {
    if (!t || typeof t !== 'object') return { error: `Tool ${i + 1} is not an object` };
    const o = t as Record<string, unknown>;
    let def: ToolDef;
    if (typeof o.type === 'string' && o.type) {
      def = o as ToolDef; // verbatim
    } else {
      if (typeof o.name !== 'string' || !o.name) return { error: `Tool ${i + 1} has no "name"` };
      def = { type: 'function', function: o as ToolDef['function'] & object };
    }
    if (def.type === 'function') {
      const name = def.function?.name ?? def.name;
      if (typeof name !== 'string' || !name) return { error: `Tool ${i + 1} has no "name"` };
      if (names.has(name)) return { error: `Two tools are named "${name}"` };
      names.add(name);
    }
    out.push(def);
  }
  return { tools: out.length ? out : null };
}

export function formatToolDefs(tools: ToolDef[] | null): string {
  return tools?.length ? JSON.stringify(tools, null, 2) : '';
}

/** The handlers worth storing for these tool names: manual = no entry (the
 *  default), orphans of removed tools dropped, each spec down to the fields its
 *  mode uses (switching modes in the editor keeps the other mode's draft until
 *  save). null = none. Mirrors tool_handlers.check_tool_handlers' shape. */
export function cleanHandlers(hs: ToolHandlers, names: string[]): ToolHandlers | null {
  const out: ToolHandlers = {};
  for (const n of names) {
    const h = hs[n];
    if (!h || h.mode === 'manual') continue;
    const c: ToolHandler = { mode: h.mode };
    if (h.mode === 'static') c.text = h.text ?? '';
    if (h.mode === 'python') {
      c.code = h.code ?? '';
      if (h.timeout) c.timeout = h.timeout;
    }
    if (h.auto_continue === false) c.auto_continue = false;
    out[n] = c;
  }
  return Object.keys(out).length ? out : null;
}
