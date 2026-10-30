// Parse the tools editor's JSON into OpenAI function definitions. PURE.
//
// Accepts the full chat-completions shape `[{type:"function", function:{name,
// description?, parameters?}}]` and, for convenience, the bare function objects
// `[{name, description?, parameters?}]` (what most tool docs print) — those get
// wrapped. Blank text = no tools. The server re-validates (tree_ops.check_tools);
// this is so a typo is reported in the editor instead of as a 400 on save.

import type { ToolDef } from './types.ts';

export type ParsedTools = { tools: ToolDef[] | null; error?: undefined } | { tools?: undefined; error: string };

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
    const fn = (o.function && typeof o.function === 'object' ? o.function : o) as Record<string, unknown>;
    if (typeof fn.name !== 'string' || !fn.name) return { error: `Tool ${i + 1} has no "name"` };
    if (names.has(fn.name)) return { error: `Two tools are named "${fn.name}"` };
    names.add(fn.name);
    const def: ToolDef = { type: 'function', function: { name: fn.name } };
    if (typeof fn.description === 'string') def.function.description = fn.description;
    if (fn.parameters && typeof fn.parameters === 'object') def.function.parameters = fn.parameters as Record<string, unknown>;
    // keep provider extras (e.g. `strict`) the editor doesn't model
    for (const [k, v] of Object.entries(fn)) if (!(k in def.function)) (def.function as Record<string, unknown>)[k] = v;
    out.push(def);
  }
  return { tools: out.length ? out : null };
}

export function formatToolDefs(tools: ToolDef[] | null): string {
  return tools?.length ? JSON.stringify(tools, null, 2) : '';
}
