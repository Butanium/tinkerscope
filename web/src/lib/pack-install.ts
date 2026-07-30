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
import { restoreLogprobs } from './pack-logprobs';
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

/** Un-gzip if the bytes are gzipped, then decode as UTF-8. Sniffs the MAGIC, not the
 *  extension — mirrors `_decode_pack_bytes` in pack.py. `DecompressionStream` is native
 *  (Chrome 80+, Firefox 113+, Safari 16.4+), so a compressed pack costs no dependency. */
async function decodePackBytes(buf: ArrayBuffer): Promise<string> {
  const head = new Uint8Array(buf.slice(0, 2));
  if (head[0] !== 0x1f || head[1] !== 0x8b) return new TextDecoder().decode(buf);
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('this pack is gzipped and this browser cannot decompress it');
  }
  const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new TextDecoder().decode(await new Response(stream).arrayBuffer());
}

async function fetchPack(source: string | File): Promise<RawPack> {
  const key = typeof source === 'string' ? source : `file:${source.name}:${source.size}`;
  const hit = cache.get(key);
  if (hit) return hit;
  let buf: ArrayBuffer;
  if (typeof source !== 'string') {
    // A file the visitor picked. This is the only route that needs no hosting and no
    // CORS — which is what "anyone can open their own export here" actually requires.
    buf = await source.arrayBuffer();
  } else {
    // A static site has no filesystem, so a non-http source can only be a RELATIVE
    // URL — `?w=./demo.yaml.gz` for a pack sitting next to index.html, which is the
    // natural way to publish one alongside its viewer. So resolve and try it rather
    // than refusing every non-http value; a genuine filesystem path just 404s, and the
    // error below points at the file picker, which is the actual answer for one.
    // (A LIVE instance keeps the opposite rule — there, non-http means a real path the
    // server reads, and it never reaches this branch.)
    const url = new URL(source, document.baseURI).href;
    let r: Response;
    try {
      r = await fetch(url);
    } catch (e) {
      throw new Error(
        isLocalPath(source)
          ? `could not read "${source}" — a static site has no filesystem access. Use the file picker to open a pack from this computer, or link its https:// URL.`
          : `could not fetch the pack: ${e}`
      );
    }
    if (!r.ok) {
      throw new Error(
        isLocalPath(source)
          ? `no pack at "${source}" (${r.status}). If that is a path on your computer, use the file picker instead — a static site cannot read local files.`
          : `could not fetch the pack: ${r.status} ${r.statusText}`
      );
    }
    buf = await r.arrayBuffer();
  }
  const text = await decodePackBytes(buf);
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
  cache.set(key, pack);
  return pack;
}

function packName(p: RawPack): string {
  return String(p.name || 'pack');
}

// A picked File is a STATIC-mode affordance. A live instance doesn't need one — its
// `?w=/some/path.yaml` already reads the filesystem server-side — and routing a File
// through the static installer there would write to an overlay a live instance never
// reads, i.e. silently do nothing. So it is refused rather than half-supported.
function assertFileIsInstallable(source: string | File): void {
  if (typeof source !== 'string' && !isStatic) {
    throw new Error('opening a pack file needs a static site — on a live instance use ?w=<path>');
  }
}

/** What installing `source` here would touch, without touching it. */
export async function preview(source: string | File): Promise<PackPreview> {
  assertFileIsInstallable(source);
  if (!isStatic && typeof source === 'string') {
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
export async function install(
  source: string | File,
  mode: ConflictMode
): Promise<InstalledWorkspace[]> {
  assertFileIsInstallable(source);
  if (!isStatic && typeof source === 'string') {
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
    // restoreLogprobs BEFORE the install: staticInstallWorkspace splits inline heavy
    // fields into blobs (lib/node-split), and it only recognizes `token_logprobs`.
    const body = restoreLogprobs((w.body ?? {}) as Record<string, any>);
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
