// The model catalogs + their loaders + the id→label resolvers, in one store.
//
// Owns the four catalogs a panel's model picker draws from — discovered tinker
// `runs`, saved `openrouterModels`, and the two lazy typeahead catalogs
// (`tinkerModels` / `orCatalog`, loaded when their picker opens) — plus the
// localStorage "recents" that keep a CLI/typeahead-picked base model or loose
// checkpoint visible in the panel dropdown across reloads (they aren't Runs).
//
// The resolvers (`runById`, `*Label`, `selectedModelLabel`) layer on the pure
// sentinel encoding in ./model-sel: model-sel decodes an id, these turn it into
// something human-readable by reading the reactive catalogs. `modelItems` is the
// per-panel dropdown item list that used to live inline in +page's markup.
//
// Deliberately UI-agnostic (house pattern, like chat.svelte.ts): the two loaders
// that surface a failure into +page's shared error banner (`loadRuns` /
// `loadOpenrouterModels`) take an `onError` callback rather than reaching into it;
// the self-contained catalog loaders own their own loading/error `$state`.

import { api } from './api';
import {
  OR_PREFIX, BASE_PREFIX, CKPT_PREFIX,
  isOpenrouterSel, openrouterId,
  isBaseSel, baseModelId,
  isCkptSel, samplerPathOf
} from './model-sel';
import type { Run, OpenRouterModel, TinkerModel } from './types';

/** Recently-used raw base models + loose checkpoints (localStorage) so a picked
 *  tinker model stays visible in the panel dropdown across reloads even though
 *  it's not a Run. Two lightweight shapes — one per sentinel. */
type RecentBase = { base_model: string; label: string };
type RecentCkpt = { sampler_path: string; label: string };
const RECENT_BASE_KEY = 'tinkerscope-recent-base-models';
const RECENT_CKPT_KEY = 'tinkerscope-recent-checkpoints';

/** One entry in a panel dropdown's item list. `unavailable` = selectable but not
 *  samplable right now (base gone / weights aged out) → greyed + demoted + ⚠, a
 *  warning not a block (still pickable, unlike the hard `disabled`). */
export type ModelItem = {
  id: string;
  label: string;
  disabled?: boolean;
  unavailable?: boolean;
  search?: string;
};

class ModelCatalog {
  // The discovered tinker runs + the saved OpenRouter quick-list (the two always-
  // loaded catalogs). Both drive the panel dropdown + the resolvers below.
  runs = $state<Run[]>([]);
  openrouterModels = $state<OpenRouterModel[]>([]);

  // Typeahead catalogs (lazy-loaded when their picker opens).
  tinkerModels = $state<TinkerModel[]>([]);
  tinkerCatalogLoaded = $state(false);
  tinkerCatalogLoading = $state(false);
  tinkerCatalogError = $state<string | null>(null);
  orCatalog = $state<OpenRouterModel[]>([]);
  orCatalogLoaded = $state(false);
  orCatalogLoading = $state(false);
  orCatalogError = $state<string | null>(null);

  recentBaseModels = $state<RecentBase[]>([]);
  recentCheckpoints = $state<RecentCkpt[]>([]);

  /** Restore the two recents lists from localStorage (call once on mount). */
  restoreRecents() {
    try {
      const rb = localStorage.getItem(RECENT_BASE_KEY);
      if (rb) this.recentBaseModels = JSON.parse(rb);
    } catch {}
    try {
      const rc = localStorage.getItem(RECENT_CKPT_KEY);
      if (rc) this.recentCheckpoints = JSON.parse(rc);
    } catch {}
  }

  rememberBaseModel(m: RecentBase) {
    const next = [m, ...this.recentBaseModels.filter((x) => x.base_model !== m.base_model)].slice(0, 8);
    this.recentBaseModels = next;
    try { localStorage.setItem(RECENT_BASE_KEY, JSON.stringify(next)); } catch {}
  }
  rememberCheckpoint(m: RecentCkpt) {
    const next = [m, ...this.recentCheckpoints.filter((x) => x.sampler_path !== m.sampler_path)].slice(0, 8);
    this.recentCheckpoints = next;
    try { localStorage.setItem(RECENT_CKPT_KEY, JSON.stringify(next)); } catch {}
  }

  // ── Loaders ───────────────────────────────────────────────────────
  /** Load the discovered runs; clears the error banner on success. onError is
   *  +page's `backendError` setter ('' clears it). */
  async loadRuns(onError: (msg: string) => void) {
    try {
      this.runs = await api.models();
      onError('');
    } catch (e: any) {
      onError(`Failed to load runs: ${e?.message ?? e}`);
    }
  }

  async loadOpenrouterModels(onError: (msg: string) => void) {
    try {
      this.openrouterModels = await api.openrouterModels();
    } catch (e: any) {
      onError(`Failed to load OpenRouter models: ${e?.message ?? e}`);
    }
  }

  /** Lazy-load the full OpenRouter catalog (~341) the first time the manager opens. */
  async loadOrCatalog(refresh = false) {
    this.orCatalogLoading = true;
    this.orCatalogError = null;
    try {
      const res = await api.openrouterAvailable(refresh);
      this.orCatalog = res.models ?? [];
      this.orCatalogError = res.available === false ? res.error || 'OpenRouter catalog unavailable' : null;
      this.orCatalogLoaded = true;
    } catch (e: any) {
      this.orCatalogError = `Failed to load OpenRouter catalog: ${e?.message ?? e}`;
    }
    this.orCatalogLoading = false;
  }

  async loadTinkerCatalog() {
    this.tinkerCatalogLoading = true;
    this.tinkerCatalogError = null;
    try {
      const res = await api.tinkerModels();
      this.tinkerModels = res.models ?? [];
      this.tinkerCatalogError = res.available === false ? res.error || 'Tinker base models unavailable' : null;
      this.tinkerCatalogLoaded = true;
    } catch (e: any) {
      this.tinkerCatalogError = `Failed to load tinker base models: ${e?.message ?? e}`;
    }
    this.tinkerCatalogLoading = false;
  }

  // ── Resolvers (sentinel/run id → run or display label) ─────────────
  // The pure sentinel encoding (OR_/BASE_/CKPT_ prefixes + predicates + id
  // extractors) lives in ./model-sel. These layer on it, reading the reactive
  // catalogs (openrouterModels / tinkerModels / recents) to turn an id into
  // something human-readable.
  runById(id: string | null | undefined): Run | undefined {
    if (!id) return undefined;
    return this.runs.find((r) => r.id === id);
  }

  runLabel(r: Run): string {
    if (r.config_error) return `${r.name} (config error)`;
    return r.name;
  }

  openrouterBySel(id: string | null | undefined): OpenRouterModel | undefined {
    const orId = openrouterId(id);
    if (orId == null) return undefined;
    return this.openrouterModels.find((m) => m.openrouter_model === orId);
  }
  openrouterLabel(id: string | null | undefined): string {
    const orId = openrouterId(id);
    if (orId == null) return '';
    const m = this.openrouterBySel(id);
    return m?.label || orId;
  }
  baseLabel(id: string | null | undefined): string {
    const bm = baseModelId(id);
    if (bm == null) return '';
    const m = this.tinkerModels.find((t) => t.base_model === bm) ?? this.recentBaseModels.find((t) => t.base_model === bm);
    return m?.label || bm;
  }
  ckptLabel(id: string | null | undefined): string {
    const sp = samplerPathOf(id);
    if (sp == null) return '';
    const m = this.tinkerModels.find((t) => t.sampler_path === sp) ?? this.recentCheckpoints.find((t) => t.sampler_path === sp);
    return m?.label || sp;
  }

  /** Whether a `base:` pick supports the thinking toggle, per the tinker catalog's
   *  `supports_thinking`. Undefined when the catalog isn't loaded yet or the base
   *  isn't in it (e.g. a recents-only pick) → the caller defaults to true, so the
   *  composer's thinking control stays visible (back-compat). */
  baseSupportsThinking(id: string | null | undefined): boolean | undefined {
    const bm = baseModelId(id);
    if (bm == null) return undefined;
    return this.tinkerModels.find((t) => t.base_model === bm)?.supports_thinking;
  }

  /** The panel dropdown's trigger-button text. Group markers (◆/◇/↗); a run's
   *  availability shows ⚠ (unavailable — selectable warning) or ? (unknown). */
  selectedModelLabel(sel: { run_id: string | null }): string {
    if (!sel.run_id) return '';
    if (isBaseSel(sel.run_id)) return `◆ ${this.baseLabel(sel.run_id)}`;
    if (isCkptSel(sel.run_id)) return `◇ ${this.ckptLabel(sel.run_id)}`;
    if (isOpenrouterSel(sel.run_id)) return `↗ ${this.openrouterLabel(sel.run_id)}`;
    const r = this.runById(sel.run_id);
    if (r) return `${r.sampleable === false ? '⚠ ' : r.sampleable === null ? '? ' : ''}${this.runLabel(r)}`;
    return sel.run_id;
  }

  /** Build one panel's dropdown item list (runs + base/ckpt recents + OpenRouter),
   *  keyed off the panel's currently-selected `runId` so a CLI/shared-state
   *  selection that isn't in recents yet still gets an entry. */
  modelItems(runId: string | null): ModelItem[] {
    const baseM = baseModelId(runId);
    const sp = samplerPathOf(runId);
    const baseInRecents = baseM != null && this.recentBaseModels.some((t) => t.base_model === baseM);
    const ckptInRecents = sp != null && this.recentCheckpoints.some((t) => t.sampler_path === sp);
    return [
      ...this.runs.map((r) => ({
        id: r.id,
        label: `${r.sampleable === false ? '⚠ ' : r.sampleable === null ? '? ' : ''}${this.runLabel(r)}`,
        // Unavailable (base gone / weights aged out) is a warning, not a block:
        // greyed + demoted but still pickable. `?` (unknown/offline) stays neutral.
        unavailable: r.sampleable === false,
        search: [r.id, r.base_model, r.wandb_project, r.renderer_name].filter(Boolean).join(' ')
      })),
      ...(baseM != null && !baseInRecents ? [{ id: BASE_PREFIX + baseM, label: `◆ ${this.baseLabel(runId)}` }] : []),
      ...this.recentBaseModels.map((t) => ({ id: BASE_PREFIX + t.base_model, label: `◆ ${t.label || t.base_model}`, search: t.base_model })),
      ...(sp != null && !ckptInRecents ? [{ id: CKPT_PREFIX + sp, label: `◇ ${this.ckptLabel(runId)}` }] : []),
      ...this.recentCheckpoints.map((t) => ({ id: CKPT_PREFIX + t.sampler_path, label: `◇ ${t.label || t.sampler_path}`, search: t.sampler_path })),
      ...this.openrouterModels.map((m) => ({ id: OR_PREFIX + m.openrouter_model, label: `↗ ${m.label || m.openrouter_model}`, search: m.openrouter_model }))
    ];
  }
}

export const modelCatalog = new ModelCatalog();
