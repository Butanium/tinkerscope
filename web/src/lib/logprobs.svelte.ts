// Display toggle for the per-token logprob inspector (sidebar "Token probs").
//
// DISPLAY-only: capture is always on for native tinker sampling (the server
// default; see docs/API_CONTRACT.md `logprobs`), so flipping this on works
// retroactively on any turn that already carries token_logprobs. Persisted in
// localStorage — it's a browser viewing preference, not workspace state.
//
// Two ways to look at the same numbers:
//   'overlay' — keep the normal markdown prose and paint the heat UNDER it
//               (TokenHeatOverlay, aligned via lib/token-align). Reads like the
//               rest of the app; a token the aligner can't place isn't painted.
//   'stream'  — replace the body with the raw token stream (TokenLogprobs).
//               Exact token boundaries, thinking tags and all; the fallback
//               when the alignment can't follow the render.
// `enabled` folds the two together for the ~10 places that only care whether
// SOME token view is on.

import { DEFAULT_MATCH_SHARPNESS } from './token-logprob.ts';

const KEY = 'tinkerscope:token-probs';

export type TokenViewMode = 'off' | 'overlay' | 'stream';

class LogprobViewStore {
  mode = $state<TokenViewMode>('off');

  constructor() {
    try {
      const raw = localStorage.getItem(KEY);
      // '1'/'0' predate the overlay. An installed '1' meant "show me the
      // tokens" — honour that as the overlay, which is the same information
      // without giving up the prose.
      this.mode = raw === '1' ? 'overlay' : raw === 'overlay' || raw === 'stream' ? raw : 'off';
    } catch {
      /* SSR / storage disabled — default off */
    }
  }

  get enabled(): boolean {
    return this.mode !== 'off';
  }

  setMode(m: TokenViewMode): void {
    this.mode = m;
    try {
      localStorage.setItem(KEY, m);
    } catch {
      /* ignore */
    }
  }
}

export const logprobView = new LogprobViewStore();

// Highlight-match token coloring (within Token-probs mode). Holds up to 2
// selected highlight-rule ids; when ON and non-empty, TokenLogprobs tints each
// token by the model's probability of emitting a matching token there (top-5)
// instead of by surprisal, and the hover popover colors alternatives by their
// match. Session preference like the view toggle → localStorage. Stores ids (not
// rules) so a renamed/recolored rule keeps applying; a deleted rule's stale id is
// inert.
//
// `enabled` is a separate axis from the picked ids on purpose: flipping the mode
// off and back on must not cost you the rule selection (the pre-toggle UI could
// only be silenced by deselecting every chip). Consumers read `activeIds`, which
// folds the two together.
const HL_KEY = 'tinkerscope:token-probs-highlights';
const HL_ON_KEY = 'tinkerscope:token-probs-highlights-on';
const HL_SHARP_KEY = 'tinkerscope:token-probs-highlights-sharpness';
const MAX_HL = 2;

class LogprobHighlightStore {
  selected = $state<string[]>([]);
  enabled = $state(false);
  /** Tint ramp: 0 = opacity ∝ match mass, 1 = any nonzero match at full tint.
   *  See `matchTintAlpha` — this is the exponent knob, not a scale factor. */
  sharpness = $state(DEFAULT_MATCH_SHARPNESS);

  constructor() {
    try {
      const raw = localStorage.getItem(HL_KEY);
      if (raw) this.selected = (JSON.parse(raw) as string[]).slice(0, MAX_HL);
      // Pre-toggle installs stored ids with no on-flag and expected them to
      // apply, so a stored non-empty selection implies ON.
      const on = localStorage.getItem(HL_ON_KEY);
      this.enabled = on == null ? this.selected.length > 0 : on === '1';
      const sharp = parseFloat(localStorage.getItem(HL_SHARP_KEY) ?? '');
      if (Number.isFinite(sharp)) this.sharpness = Math.min(Math.max(sharp, 0), 1);
    } catch {
      /* SSR / storage disabled / bad JSON — default empty */
    }
  }

  setSharpness(v: number): void {
    this.sharpness = Math.min(Math.max(v, 0), 1);
    try {
      localStorage.setItem(HL_SHARP_KEY, String(this.sharpness));
    } catch {
      /* ignore */
    }
  }

  /** The rule ids actually coloring tokens right now ([] ⇒ surprisal tint). */
  get activeIds(): string[] {
    return this.enabled ? this.selected : [];
  }

  setEnabled(on: boolean): void {
    this.enabled = on;
    try {
      localStorage.setItem(HL_ON_KEY, on ? '1' : '0');
    } catch {
      /* ignore */
    }
  }

  has(id: string): boolean {
    return this.selected.includes(id);
  }

  /** Toggle a rule id. Selecting a 3rd drops the oldest (keeps ≤2, newest wins). */
  toggle(id: string): void {
    const next = this.has(id)
      ? this.selected.filter((x) => x !== id)
      : [...this.selected, id].slice(-MAX_HL);
    this.set(next);
  }

  set(ids: string[]): void {
    this.selected = ids.slice(0, MAX_HL);
    try {
      localStorage.setItem(HL_KEY, JSON.stringify(this.selected));
    } catch {
      /* ignore */
    }
  }
}

export const logprobHighlight = new LogprobHighlightStore();
