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
// Trust note: a pack is DATA, never code — models, params, and workspace trees, with
// message content HTML-escaped before render (highlight-render `renderMarkdown`), so
// nothing here reaches the DOM as markup. What the data IS, though, is conversations
// that look exactly like turns your own checkpoints produced, and a link installs on
// plain NAVIGATION (which no CORS policy governs). That is an attribution problem, not
// a compromise one — which is why only a LIVE instance prompts on a first install
// (there the pack lands in the real on-disk state dir) while a static site doesn't.
// The prompt policy itself lives at the call site: `canInstallUnprompted` in +page.

import { api } from './api';
import { isStatic } from './static-mode';
import {
  staticApi,
  staticInstallWorkspace,
  staticInstallModels,
  staticSetWorkspaceSource
} from './api-static';
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

/** Where a load has got to, for the progress modal. A pack link is the one action here
 *  that can take tens of seconds (an 18 MB gzipped export is normal), and it runs on
 *  plain navigation — so it needs to say what it's doing rather than let the app look
 *  broken while it works. `done`/`total` are bytes while fetching and workspaces while
 *  installing; a phase without them renders indeterminate. */
export type PackPhase = 'fetch' | 'decode' | 'parse' | 'server' | 'install';
export type PackProgress = { phase: PackPhase; done?: number; total?: number | null };
export type PackProgressFn = (p: PackProgress) => void;

/** Report a phase change AND give the browser a frame to paint it. Both halves matter:
 *  `parse` and `install` block the main thread for seconds on a large pack, so without
 *  the macrotask yield the label would only appear once the work it describes is over. */
async function report(fn: PackProgressFn | undefined, p: PackProgress): Promise<void> {
  if (!fn) return;
  fn(p);
  await new Promise((r) => setTimeout(r, 0));
}

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

/** Read `r`'s body while reporting bytes. Falls back to a plain `arrayBuffer()` when the
 *  browser gives no stream. `content-length` is only a hint: if the host applied
 *  `content-encoding`, the reader yields DECODED bytes against a compressed length, so
 *  the caller must treat `done > total` as "unknown total" rather than as >100%. */
async function readWithProgress(r: Response, onProgress: PackProgressFn): Promise<ArrayBuffer> {
  const total = Number(r.headers.get('content-length')) || null;
  if (!r.body) return r.arrayBuffer();
  const reader = r.body.getReader();
  const chunks: Uint8Array[] = [];
  let done = 0;
  for (;;) {
    const step = await reader.read();
    if (step.done) break;
    chunks.push(step.value);
    done += step.value.length;
    onProgress({ phase: 'fetch', done, total });
  }
  const out = new Uint8Array(done);
  let at = 0;
  for (const c of chunks) {
    out.set(c, at);
    at += c.length;
  }
  return out.buffer;
}

async function fetchPack(source: string | File, onProgress?: PackProgressFn): Promise<RawPack> {
  const key = typeof source === 'string' ? source : `file:${source.name}:${source.size}`;
  const hit = cache.get(key);
  if (hit) return hit; // preview() already fetched it — install() must not refetch
  let buf: ArrayBuffer;
  if (typeof source !== 'string') {
    // A file the visitor picked. This is the only route that needs no hosting and no
    // CORS — which is what "anyone can open their own export here" actually requires.
    await report(onProgress, { phase: 'fetch', done: 0, total: source.size });
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
    buf = onProgress ? await readWithProgress(r, onProgress) : await r.arrayBuffer();
  }
  await report(onProgress, { phase: 'decode' });
  const text = await decodePackBytes(buf);
  await report(onProgress, { phase: 'parse' });
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
export async function preview(
  source: string | File,
  onProgress?: PackProgressFn
): Promise<PackPreview> {
  assertFileIsInstallable(source);
  if (!isStatic && typeof source === 'string') {
    // The server does the reading, so there is nothing to measure — one opaque wait.
    await report(onProgress, { phase: 'server' });
    return (await api.applyPack({ source })) as PackPreview;
  }
  const p = await fetchPack(source, onProgress);
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
  mode: ConflictMode,
  onProgress?: PackProgressFn
): Promise<InstalledWorkspace[]> {
  assertFileIsInstallable(source);
  if (!isStatic && typeof source === 'string') {
    await report(onProgress, { phase: 'server' });
    const res = (await api.applyPack({ source, on_conflict: mode })) as {
      workspace_ids?: InstalledWorkspace[];
    };
    return res.workspace_ids ?? [];
  }
  const p = await fetchPack(source, onProgress);
  const name = packName(p);
  const summaries = await staticApi.listWorkspaces();
  const takenIds = new Set(summaries.map((w) => w.id));

  // A pack's models carry the LABELS its panels' `ckpt:`/`base:`/`openrouter:` refs
  // resolve through; without merging them every installed panel would be titled by a
  // raw tinker:// URI.
  staticInstallModels(packModels(p));

  const out: InstalledWorkspace[] = [];
  const all = p.workspaces ?? [];
  for (const w of all) {
    // Per workspace, not per pack: splitting one workspace's blobs out of a
    // logprob-carrying tree is seconds of blocking work, so the bar has to move
    // between them or a multi-workspace pack looks hung.
    await report(onProgress, { phase: 'install', done: out.length, total: all.length });
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
    if (typeof source === 'string') staticSetWorkspaceSource(id, source);
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
