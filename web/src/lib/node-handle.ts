// A node handle — `<ws>:<panel>:<node>`, the form the Copy-node-id button and
// every `tinkerscope` printer emit — typed into the Ctrl+K palette jumps straight to
// that node instead of running a text search for it. The ws part is an id
// PREFIX (tinkerscope prints 8 chars), resolved against the workspace list.

import { ROOT, selectedChildId } from './tree.ts';
import type { SearchHit, SearchResponse, Workspace, WorkspaceSummary } from './types';

export type NodeHandle = { ws: string; panel: string; node: string };

// ws part = any store id (`^[A-Za-z0-9_-]+$`: uuids AND custom ids like `terra-p4-memuser`)
const HANDLE = /^([A-Za-z0-9_-]{4,}):([A-Za-z][\w-]*):([A-Za-z0-9]+)$/;

export function parseNodeHandle(q: string): NodeHandle | null {
  const m = HANDLE.exec(q.trim());
  return m ? { ws: m[1], panel: m[2], node: m[3] } : null;
}

/** The palette hit for a handle, or the reason it doesn't resolve. */
export async function resolveNodeHandle(
  h: NodeHandle,
  list: () => Promise<WorkspaceSummary[]>,
  get: (id: string) => Promise<Workspace>
): Promise<SearchHit | string> {
  const all = await list();
  const exact = all.find((w) => w.id === h.ws);
  let matches = exact ? [exact] : all.filter((w) => w.id.startsWith(h.ws));
  if (!matches.length) return `no workspace with id ${h.ws}…`;
  if (matches.length > 1) {
    // A handle copied before 2026-11-02 carries an 8-char prefix, which ids sharing
    // a stem collide on — the workspace holding the node is the one it meant.
    const holding: WorkspaceSummary[] = [];
    for (const w of matches) if ((await get(w.id)).trees?.[h.panel]?.nodes?.[h.node]) holding.push(w);
    if (holding.length !== 1) return `${h.ws} matches ${matches.length} workspaces — paste more of the id`;
    matches = holding;
  }
  const wsBody = await get(matches[0].id);
  const tree = wsBody.trees?.[h.panel];
  if (!tree) return `workspace ${wsBody.name} has no panel ${h.panel}`;
  const nd = tree.nodes[h.node];
  if (!nd) return `no node ${h.node} in ${wsBody.name} · ${h.panel}`;
  const sibs = nd.parent ? tree.nodes[nd.parent]?.children ?? [] : tree.rootChildren;
  let onPath = true;
  for (let cur: typeof nd | undefined = nd; cur && onPath; cur = cur.parent ? tree.nodes[cur.parent] : undefined)
    onPath = selectedChildId(tree, cur.parent ?? ROOT) === cur.id;
  const text = nd.content ?? '';
  return {
    workspace_id: wsBody.id,
    workspace_name: wsBody.name,
    panel: h.panel,
    node_id: h.node,
    parent: nd.parent ?? null,
    role: nd.role,
    field: 'content',
    thread: null,
    on_active_path: onPath,
    sib_index: Math.max(0, sibs.indexOf(h.node)),
    sib_count: sibs.length,
    match: '',
    before: '',
    match_display: '',
    after: text.length > 200 ? text.slice(0, 200) + '…' : text
  };
}

/** A one-hit SearchResponse, so the palette renders a resolved handle with its
 *  normal node row (workspace section, model label, role, snippet). */
export function handleResponse(q: string, hit: SearchHit): SearchResponse {
  return {
    query: q,
    workspace_hits: [],
    hits: [hit],
    workspace_totals: [{ workspace_id: hit.workspace_id, workspace_name: hit.workspace_name, total: 1 }],
    total: 1,
    truncated: false,
    workspaces_searched: 1,
    workspaces_matched: 1
  };
}
