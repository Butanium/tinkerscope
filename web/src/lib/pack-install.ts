// Loading a share pack from a `?w=<path-or-url>` link.
//
// Two backends, one interface. On a LIVE instance the server does the work
// (`POST /api/pack/apply`, api/routes/packs.py) — which is also the only way a local
// filesystem path can be read at all. On a STATIC site there is no server, so the
// browser fetches the pack itself, parses the YAML, and installs into the
// localStorage overlay; a filesystem path is refused there rather than silently
// producing nothing.
//
// Both are two-phase: `preview()` reports which workspace ids the pack would land on
// and which already exist, the caller prompts (overwrite / keep both), then
// `install()` runs with that answer. See lib/pack-source.ts for the pure half — the
// id-vs-source discriminator and the `(2)` naming.
//
// Trust note: a pack is DATA, never code — models, params, and workspace trees. But
// the data is CONVERSATIONS, which once installed look exactly like turns your own
// checkpoints produced, and a link installs on plain NAVIGATION (which no CORS policy
// governs — any page can point a browser at a localhost URL). So the caller prompts
// before every install, not just a colliding one, and names the source by host.

import { api } from './api';
import { isStatic } from './static-mode';
import { staticApi, staticInstallWorkspace, staticInstallModels } from './api-static';
import { isLocalPath, packWorkspaceId, bumpUntilFree } from './pack-source';
import type { PanelLayout, TinkerModel, OpenRouterModel } from './types';
import type { ConvTree } from './tree';

export type PackPreviewEntry = { id: string; name: string; exists: boolean };
export type PackPreview = {
  pack: string;
  description?: string | null;
  models: number;
  workspaces: PackPreviewEntry[];
};
export type InstalledWorkspace = { id: string; name: string };
export type ConflictMode = 'overwrite' | 'new';

/** A pack file's parsed shape — only the fields the browser installs. Mirrors
 *  `Pack.to_dict` in src/tinkerscope/pack.py. */
type RawPack = {
  name?: string;
  description?: string | null;
  models?: Array<Record<string, unknown>>;
  defaults?: Record<string, unknown>;
  workspaces?: Array<{ name?: string; body?: Record<string, unknown> }>;
};

// A parsed pack is memoized per source so preview() → install() doesn't refetch (and
// can't disagree about what it's installing if the URL changed underneath).
const cache = new Map<string, RawPack>();

async function fetchPack(source: string): Promise<RawPack> {
  const hit = cache.get(source);
  if (hit) return hit;
  if (isLocalPath(source)) {
    throw new Error(
      `"${source}" is a local file path, which a static site cannot read — publish the pack and link its https:// URL instead.`
    );
  }
  const r = await fetch(source);
  if (!r.ok) throw new Error(`could not fetch the pack: ${r.status} ${r.statusText}`);
  const text = await r.text();
  // Dynamic import: js-yaml is only needed when someone actually opens a pack link,
  // so it stays out of the main bundle as its own chunk.
  const yaml = await import('js-yaml');
  const parsed = yaml.load(text) as unknown;
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('that file is not a tinkerscope pack (expected a YAML/JSON object)');
  }
  const pack = parsed as RawPack;
  if (!Array.isArray(pack.workspaces) && !Array.isArray(pack.models)) {
    throw new Error('that file has no `workspaces` or `models` — it is not a tinkerscope pack');
  }
  cache.set(source, pack);
  return pack;
}

function packName(p: RawPack): string {
  return String(p.name || 'pack');
}

/** What installing `source` here would touch, without touching it. */
export async function preview(source: string): Promise<PackPreview> {
  if (!isStatic) {
    return (await api.applyPack({ source })) as PackPreview;
  }
  const p = await fetchPack(source);
  const existing = new Set((await staticApi.listWorkspaces()).map((w) => w.id));
  const name = packName(p);
  return {
    pack: name,
    description: p.description ?? null,
    models: (p.models ?? []).length,
    workspaces: (p.workspaces ?? []).map((w) => {
      const id = packWorkspaceId(name, String(w.name || 'workspace'));
      return { id, name: String(w.name || 'workspace'), exists: existing.has(id) };
    })
  };
}

/** Install `source`, resolving id collisions per `mode`. Returns what landed. */
export async function install(source: string, mode: ConflictMode): Promise<InstalledWorkspace[]> {
  if (!isStatic) {
    const res = (await api.applyPack({ source, on_conflict: mode })) as {
      workspace_ids?: InstalledWorkspace[];
    };
    return res.workspace_ids ?? [];
  }
  const p = await fetchPack(source);
  const name = packName(p);
  const summaries = await staticApi.listWorkspaces();
  const takenIds = new Set(summaries.map((w) => w.id));

  // A pack's models carry the LABELS its panels' `ckpt:`/`base:`/`openrouter:` refs
  // resolve through; without merging them every installed panel would be titled by a
  // raw tinker:// URI.
  staticInstallModels(packModels(p));

  const out: InstalledWorkspace[] = [];
  for (const w of p.workspaces ?? []) {
    let wsName = String(w.name || 'workspace');
    if (mode === 'new') {
      // ONE rule, shared with the server's `_dedupe_conflicting` via bumpUntilFree —
      // asked about the derived id, never the display name.
      wsName = bumpUntilFree(wsName, (c) => !takenIds.has(packWorkspaceId(name, c)));
    }
    const id = packWorkspaceId(name, wsName);
    takenIds.add(id);
    const body = (w.body ?? {}) as Record<string, any>;
    staticInstallWorkspace({
      id,
      name: wsName,
      system_prompt: body.system_prompt ?? null,
      system_enabled: body.system_enabled ?? null,
      trees: (body.trees ?? {}) as Record<string, ConvTree>,
      panels: (body.panels ?? []) as PanelLayout[],
      reduced_panels: body.reduced_panels ?? [],
      send_targets: body.send_targets ?? [],
      seen_panels: body.seen_panels ?? []
    });
    out.push({ id, name: wsName });
  }
  return out;
}

/** Split a pack's `models` into the two catalog shapes the frontend reads. */
function packModels(p: RawPack): { tinker: TinkerModel[]; openrouter: OpenRouterModel[] } {
  const tinker: TinkerModel[] = [];
  const openrouter: OpenRouterModel[] = [];
  for (const m of p.models ?? []) {
    const label = String(m.label ?? '');
    if (typeof m.ckpt === 'string') {
      tinker.push({ kind: 'checkpoint', id: m.ckpt, label: label || m.ckpt, sampler_path: m.ckpt });
    } else if (typeof m.base === 'string') {
      tinker.push({ kind: 'base', id: m.base, label: label || m.base, base_model: m.base });
    } else if (typeof m.openrouter === 'string') {
      openrouter.push({ label: label || m.openrouter, openrouter_model: m.openrouter });
    }
  }
  return { tinker, openrouter };
}
