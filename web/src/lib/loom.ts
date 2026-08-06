// The LOOM — pure helpers for branching a turn from one of its tokens.
//
// Clicking a token pins its popover; picking an alternative (or "resample from
// here") fires an exact token-level continuation: the stored prefix token ids +
// the picked alternative id are replayed verbatim after the re-rendered prompt
// (see ChatRequest.continue_tokens), so the new sibling is the counterfactual
// "what if it had said this here". Two facts this module owns:
//
//  - The views hand out DISPLAY indices, but the fire needs a STORED-stream cut:
//    withPrefillGhost prepends a synthetic prefill ghost at display time, and an
//    edited turn's trailing ghost has no token ids to replay at all. `loomCut`
//    is that translation + eligibility check in one place.
//  - The fire must be anchored to the model that PRODUCED the token ids (they
//    are tokenizer-specific — replaying them at whatever the panel now selects
//    would feed garbage after a model switch). `parseRawMetaModel` recovers the
//    producer (+ its exact renderer) from the turn's raw_meta blob.

import type { TokenLogprob } from './tree.ts';

/** Translate a display-stream index into a STORED-stream cut index, or null when
 *  the position can't be loomed: the entry itself is a ghost (authored text has
 *  no alternatives), or an EDIT ghost sits before it (no ids to replay past it).
 *  A leading prefill ghost is display-only and just shifts the index. */
export function loomCut(tlp: TokenLogprob[], idx: number): number | null {
  if (idx < 0 || idx >= tlp.length || tlp[idx].ghost) return null;
  let cut = idx;
  for (let j = 0; j < idx; j++) {
    const e = tlp[j];
    if (!e.ghost) continue;
    if (e.ghostKind === 'prefill') cut--;
    else return null;
  }
  return cut;
}

/** The model + renderer a native turn was sampled with, as recorded in its
 *  raw_meta request block. */
export type RawMetaModel = { base_model?: string; sampler_path?: string; renderer?: string };

/** Recover the producing model from a turn's raw_meta. The blob is the
 *  pretty-printed request/response dump (api/raw_view.py): top-level request
 *  keys sit alone on 2-space-indented lines with json-encoded values, and no
 *  VALUE can fake one (json.dumps escapes newlines, so e.g. a prompt containing
 *  `"base_model": …` stays inside its own single line). Line-anchored regexes
 *  are therefore exact, not heuristic. null when nothing model-shaped is found
 *  (an OpenRouter turn, or a future format change — callers fall back to the
 *  panel's current selection). */
export function parseRawMetaModel(raw: string | undefined): RawMetaModel | null {
  if (!raw) return null;
  const grab = (key: string): string | undefined => {
    const m = raw.match(new RegExp(`^  "${key}": ("(?:[^"\\\\]|\\\\.)*"|null),?$`, 'm'));
    if (!m || m[1] === 'null') return undefined;
    try {
      return JSON.parse(m[1]) as string;
    } catch {
      return undefined;
    }
  };
  const base_model = grab('base_model');
  const sampler_path = grab('sampler_path');
  const renderer = grab('renderer');
  if (base_model == null && sampler_path == null) return null;
  return { base_model, sampler_path, renderer };
}
