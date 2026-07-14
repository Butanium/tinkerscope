// Model-selection encoding — the sentinel scheme that lets a panel point at
// something OTHER than a discovered tinker LoRA run while reusing the single
// `run_id`/`compare_run_id` field in shared state (so the choice round-trips
// through PlaygroundState and is visible to the CLI).
//
// Three sentinels share that one field; the chat builder (fireChat) detects the
// prefix and sends the matching id shape to /api/chat:
//   openrouter:<id>   → { openrouter_model }   OpenRouter reference model
//   base:<model>      → { base_model }         raw tinker base model (no LoRA)
//   ckpt:<path>       → { sampler_path }        loose tinker sampler checkpoint
// A bare value (no prefix) is a discovered Run id → { run_id, checkpoint }.
//
// Everything here is PURE string work (no reactive/catalog reads). The LABEL
// resolvers (openrouterLabel/baseLabel/ckptLabel) stay in the component because
// they look the id up in reactive catalogs — they layer on top of these.

export const OR_PREFIX = 'openrouter:';
export const BASE_PREFIX = 'base:';
export const CKPT_PREFIX = 'ckpt:';

export function isOpenrouterSel(id: string | null | undefined): boolean {
  return typeof id === 'string' && id.startsWith(OR_PREFIX);
}
export function openrouterId(id: string | null | undefined): string | null {
  return isOpenrouterSel(id) ? (id as string).slice(OR_PREFIX.length) : null;
}

export function isBaseSel(id: string | null | undefined): boolean {
  return typeof id === 'string' && id.startsWith(BASE_PREFIX);
}
export function baseModelId(id: string | null | undefined): string | null {
  return isBaseSel(id) ? (id as string).slice(BASE_PREFIX.length) : null;
}

export function isCkptSel(id: string | null | undefined): boolean {
  return typeof id === 'string' && id.startsWith(CKPT_PREFIX);
}
export function samplerPathOf(id: string | null | undefined): string | null {
  return isCkptSel(id) ? (id as string).slice(CKPT_PREFIX.length) : null;
}
