// Persistence for the response-distribution chart's VIEW state.
//
// The chart modal is destroyed on close and its state used to die with it, so
// every reopen (and every reload) meant re-picking the mode, the match scope,
// the thinking filter, the excluded rules, the merged first-token groups…
// Two scopes, because the state divides cleanly:
//
//   GLOBAL   mode / match scope / thinking filter — how you like to LOOK at a
//            distribution. Carried to a workspace you've never charted before,
//            so a fresh one doesn't reset the choice you just made.
//   PER-WORKSPACE  everything question-specific: the turn, the folded-panel
//            toggle, the excluded rules, the first-N-chars match cap, and the
//            first-token exclusions / merges / added tokens. These only mean
//            something for THAT workspace's prompt — a global "excluded rule X"
//            would leak nonsense sideways.
//            Saving one also refreshes the global three, so the last workspace
//            you touched sets the defaults for the next.
//
// Browser-local (localStorage), NOT workspace data on the server: it's how one
// person is looking at a distribution, not part of the workspace's content —
// keeping it out of the wire/disk contract also keeps it out of share packs.
// The trade is that it doesn't follow a workspace to another browser.
//
// Pure helpers (sanitize / merge / prune) are exported for chart-view.test.ts;
// only load/save touch storage.

import type { MatchScope, ThinkFilter } from './chart.ts';

const KEY = 'tinkerscope:chart-view';
/** Keep the N most recently saved workspaces; beyond that, drop the oldest. */
const MAX_WORKSPACES = 40;

export type ChartMode = 'rules' | 'answers' | 'firsttoken';
/** `mode: null` = never chosen → the caller's rules-aware default applies. */
export type ChartGlobalView = { mode: ChartMode | null; scope: MatchScope | 'split'; think: ThinkFilter };
export type ChartWorkspaceView = {
  turn: string;
  includeFolded: boolean;
  rulesOff: string[];
  /** Rules mode: match only the first N chars (0 = whole text). */
  matchLimit: number;
  ftExcluded: string[];
  ftGroups: string[][];
  ftAdded: { token: string; tid: number }[];
  ftRenorm: boolean;
};
export type ChartView = ChartGlobalView & ChartWorkspaceView;

type Stored = { v: 1; global: ChartGlobalView; ws: Record<string, ChartWorkspaceView & { ts: number }> };

const MODES: ChartMode[] = ['rules', 'answers', 'firsttoken'];
const SCOPES: (MatchScope | 'split')[] = ['response', 'thinking', 'either', 'split'];
const THINKS: ThinkFilter[] = ['all', 'thinking', 'no-thinking', 'split'];

export const DEFAULT_VIEW: ChartView = {
  mode: null,
  scope: 'response',
  think: 'all',
  turn: 'last',
  includeFolded: false,
  rulesOff: [],
  matchLimit: 0,
  ftExcluded: [],
  ftGroups: [],
  ftAdded: [],
  ftRenorm: false
};

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []);

/** Coerce anything (a hand-edited value, a record from an older build) into a
 *  usable view — unknown enum values and wrong-typed fields fall back rather
 *  than propagating into the UI. */
export function sanitizeView(raw: unknown): ChartView {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    mode: MODES.includes(r.mode as ChartMode) ? (r.mode as ChartMode) : null,
    scope: SCOPES.includes(r.scope as MatchScope) ? (r.scope as MatchScope | 'split') : 'response',
    think: THINKS.includes(r.think as ThinkFilter) ? (r.think as ThinkFilter) : 'all',
    turn: typeof r.turn === 'string' ? r.turn : 'last',
    includeFolded: r.includeFolded === true,
    rulesOff: strings(r.rulesOff),
    // A char cap is a positive integer or nothing; anything else (a stale float,
    // a negative, a hand-edited string) means "no cap" rather than a weird slice.
    matchLimit: Number.isFinite(r.matchLimit) && (r.matchLimit as number) > 0
      ? Math.floor(r.matchLimit as number)
      : 0,
    ftExcluded: strings(r.ftExcluded),
    ftGroups: Array.isArray(r.ftGroups)
      ? r.ftGroups.map(strings).filter((g) => g.length >= 2)
      : [],
    ftAdded: Array.isArray(r.ftAdded)
      ? (r.ftAdded as unknown[]).flatMap((a) => {
          const x = (a ?? {}) as Record<string, unknown>;
          return typeof x.token === 'string' && typeof x.tid === 'number'
            ? [{ token: x.token, tid: x.tid }]
            : [];
        })
      : [],
    ftRenorm: r.ftRenorm === true
  };
}

/** Read the stored blob. Accepts the pre-versioned `{mode,scope,think}` shape
 *  (the first cut of this feature) as global-only defaults. */
export function parseStore(raw: unknown): Stored {
  const r = (raw ?? {}) as Record<string, unknown>;
  const { mode, scope, think } = sanitizeView(r.v === 1 ? r.global : r);
  const ws: Stored['ws'] = {};
  if (r.v === 1 && r.ws && typeof r.ws === 'object')
    for (const [id, entry] of Object.entries(r.ws as Record<string, unknown>)) {
      const v = sanitizeView(entry);
      const ts = Number((entry as Record<string, unknown>)?.ts);
      ws[id] = {
        turn: v.turn,
        includeFolded: v.includeFolded,
        rulesOff: v.rulesOff,
        matchLimit: v.matchLimit,
        ftExcluded: v.ftExcluded,
        ftGroups: v.ftGroups,
        ftAdded: v.ftAdded,
        ftRenorm: v.ftRenorm,
        ts: Number.isFinite(ts) ? ts : 0
      };
    }
  return { v: 1, global: { mode, scope, think }, ws };
}

/** The view for one workspace: its own question-specific state if it has any,
 *  else the clean defaults — over the global three either way. */
export function viewFor(store: Stored, wsId: string | null): ChartView {
  const own = wsId ? store.ws[wsId] : undefined;
  const { ts: _ts, ...rest } = own ?? { ts: 0 }; // `ts` is bookkeeping, not view state
  return { ...DEFAULT_VIEW, ...rest, ...store.global };
}

/** Drop the oldest workspace records past the cap (mutates a fresh copy). */
export function pruneStore(store: Stored, cap = MAX_WORKSPACES): Stored {
  const ids = Object.keys(store.ws);
  if (ids.length <= cap) return store;
  const keep = new Set(
    ids.sort((a, b) => (store.ws[b].ts ?? 0) - (store.ws[a].ts ?? 0)).slice(0, cap)
  );
  const ws: Stored['ws'] = {};
  for (const id of ids) if (keep.has(id)) ws[id] = store.ws[id];
  return { ...store, ws };
}

function read(): Stored {
  try {
    return parseStore(JSON.parse(localStorage.getItem(KEY) || '{}'));
  } catch {
    /* SSR / storage disabled / bad JSON */
    return parseStore(null);
  }
}

/** The view to open the chart with for `wsId` (null → global defaults only). */
export function loadChartView(wsId: string | null): ChartView {
  return viewFor(read(), wsId);
}

/** Persist a view: the three global picks always, the question-specific rest
 *  under `wsId` (dropped when there is no active workspace). */
export function saveChartView(wsId: string | null, view: ChartView, now = Date.now()): void {
  const store = read();
  const next: Stored = { v: 1, global: { mode: view.mode, scope: view.scope, think: view.think }, ws: store.ws };
  if (wsId)
    next.ws = {
      ...store.ws,
      [wsId]: {
        turn: view.turn,
        includeFolded: view.includeFolded,
        rulesOff: view.rulesOff,
        matchLimit: view.matchLimit,
        ftExcluded: view.ftExcluded,
        ftGroups: view.ftGroups,
        ftAdded: view.ftAdded,
        ftRenorm: view.ftRenorm,
        ts: now
      }
    };
  try {
    localStorage.setItem(KEY, JSON.stringify(pruneStore(next)));
  } catch {
    /* ignore — quota / storage disabled */
  }
}
