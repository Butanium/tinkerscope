// Pure mirror of `api/search.py` for the STATIC transport: scan light workspace
// bodies client-side so the Ctrl+K palette works on a published read-only site
// (there is no server to ask). Case-insensitive substring only — the regex/case
// knobs are CLI territory and a static site has no CLI. Same response shape,
// same semantics: every branch, first match per (node, field), snippet as a
// (before, match, after) triple each whitespace-collapsed separately, workspaces
// ordered newest-touched first. If the Python engine's semantics move, move this
// (both sides carry tests: tests/test_search.py / search-scan.test.ts).

import type { Workspace, SearchHit, SearchResponse, SearchWorkspaceHit } from './types';
import type { ConvTree, TreeNode } from './tree';

const ROOT = '__root__';

function selectedChild(tree: ConvTree, parentKey: string): string | null {
  const kids = parentKey === ROOT ? tree.rootChildren : tree.nodes[parentKey]?.children;
  if (!kids || kids.length === 0) return null;
  const sel = tree.selected?.[parentKey];
  return sel != null && kids.includes(sel) ? sel : kids[kids.length - 1];
}

function activeIds(tree: ConvTree): Set<string> {
  const out = new Set<string>();
  let parentKey = ROOT;
  while (true) {
    const cid = selectedChild(tree, parentKey);
    if (cid == null || out.has(cid) || !tree.nodes[cid]) break;
    out.add(cid);
    parentKey = cid;
  }
  return out;
}

function threadOf(tree: ConvTree, nodeId: string): number | null {
  let nid: string | null = nodeId;
  const seen = new Set<string>();
  while (nid != null && !seen.has(nid)) {
    seen.add(nid);
    const node: TreeNode | undefined = tree.nodes[nid];
    if (!node) return null;
    if (node.parent == null) {
      const i = tree.rootChildren.indexOf(nid);
      return i >= 0 ? i + 1 : null;
    }
    nid = node.parent;
  }
  return null;
}

const collapse = (s: string): string => s.split(/\s+/).filter(Boolean).join(' ');

/** (before, match, after) display triple around a match at [start, end).
 *  Collapsing eats boundary whitespace, so a single boundary space is re-added
 *  where the raw text had one (mirrors `_snippet_parts` in api/search.py). */
function snippetParts(
  text: string,
  start: number,
  end: number,
  width: number
): { before: string; match_display: string; after: string } {
  const half = Math.max(20, Math.floor(width / 2));
  const rawBefore = text.slice(Math.max(0, start - half), start);
  let before = collapse(rawBefore);
  if (before && /\s$/.test(rawBefore)) before += ' ';
  if (start > half) before = '…' + before;
  let mid = collapse(text.slice(start, end));
  if (mid.length > width) mid = mid.slice(0, width) + '…';
  const rawAfter = text.slice(end, end + half);
  let after = collapse(rawAfter);
  if (after && /^\s/.test(rawAfter)) after = ' ' + after;
  if (end + half < text.length) after = after + '…';
  return { before, match_display: mid, after };
}

/** Which scope a node hit belongs to (mirrors `_unit_scope` in api/search.py). */
function hitScope(field: string, role: string): string {
  if (field === 'reasoning') return 'thinking';
  if (field === 'system_prompt') return 'system';
  return role === 'user' ? 'user' : role === 'system' ? 'system' : 'reply';
}

export function searchWorkspaces(
  bodies: Workspace[],
  q: string,
  opts: { ws?: string; maxHits?: number; width?: number; scopes?: string[] } = {}
): SearchResponse {
  const maxHits = opts.maxHits ?? 200;
  const width = opts.width ?? 160;
  const on = new Set(
    opts.scopes ?? ['reply', 'user', 'thinking', 'system', 'name', 'model']
  );
  const needle = q.toLowerCase();
  const find = (text: string): number => text.toLowerCase().indexOf(needle);

  let pool = opts.ws ? bodies.filter((b) => b.id === opts.ws) : bodies;
  pool = [...pool].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));

  const workspaceHits: SearchWorkspaceHit[] = [];
  const hits: SearchHit[] = [];
  const wsTotals = new Map<string, { workspace_id: string; workspace_name: string; total: number }>();
  let total = 0;

  for (const body of pool) {
    const cid = body.id;
    const cname = body.name || '?';

    const wsHit = (field: SearchWorkspaceHit['field'], text: string, panel: string | null = null) => {
      const i = find(text);
      if (i < 0) return;
      workspaceHits.push({
        workspace_id: cid,
        workspace_name: cname,
        field,
        panel,
        match: text.slice(i, i + q.length),
        ...snippetParts(text, i, i + q.length, width),
        updated_at: body.updated_at ?? null
      });
    };

    if (on.has('name')) wsHit('name', cname);
    if (on.has('model')) {
      for (const p of body.panels || []) {
        const v = [p.run_id, p.checkpoint].find((x) => x && find(String(x)) >= 0);
        if (v) wsHit('model', String(v), p.id);
      }
    }
    if (on.has('system') && body.system_prompt) wsHit('system', body.system_prompt);

    for (const [pid, tree] of Object.entries(body.trees || {})) {
      if (!tree || typeof tree !== 'object') continue;
      const active = activeIds(tree);

      const visit = (nid: string, seen: Set<string>) => {
        if (seen.has(nid)) return;
        seen.add(nid);
        const node = tree.nodes[nid];
        if (!node) return;
        const fields: Array<[SearchHit['field'], string | undefined]> = [['content', node.content]];
        if (node.role === 'assistant') fields.push(['reasoning', node.reasoning]);
        if (node.parent == null) fields.push(['system_prompt', node.system_prompt]);
        const kids =
          node.parent == null ? tree.rootChildren : tree.nodes[node.parent]?.children || [];
        const sibIndex = Math.max(kids.indexOf(nid), 0);
        for (const [field, text] of fields) {
          if (!text) continue;
          if (!on.has(hitScope(field, node.role))) continue;
          const i = find(text);
          if (i < 0) continue;
          total++;
          const wt = wsTotals.get(cid) ?? { workspace_id: cid, workspace_name: cname, total: 0 };
          wt.total++;
          wsTotals.set(cid, wt);
          if (hits.length >= maxHits) continue; // keep counting, stop collecting
          hits.push({
            workspace_id: cid,
            workspace_name: cname,
            panel: pid,
            node_id: nid,
            parent: node.parent,
            role: node.role || '?',
            field,
            thread: threadOf(tree, nid),
            on_active_path: active.has(nid),
            sib_index: sibIndex,
            sib_count: Math.max(kids.length, 1),
            match: text.slice(i, i + q.length),
            ...snippetParts(text, i, i + q.length, width)
          });
        }
        for (const kid of node.children || []) visit(kid, seen);
      };

      const seen = new Set<string>();
      for (const rid of tree.rootChildren || []) visit(rid, seen);
      for (const nid of Object.keys(tree.nodes || {})) visit(nid, seen); // orphans
    }
  }

  return {
    query: q,
    workspace_hits: workspaceHits,
    hits,
    workspace_totals: [...wsTotals.values()],
    total,
    truncated: total > maxHits,
    workspaces_searched: pool.length,
    workspaces_matched: wsTotals.size
  };
}
