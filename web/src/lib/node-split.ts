// Heavy-field split for tree nodes — the browser-side mirror of
// api/workspace_store.py's split_node / split_workspace (storage v2).
//
// Only static mode needs this: a pack installed client-side arrives with `raw_meta`
// (and, from a site export, `token_logprobs`) INLINE on its nodes, exactly as the
// live server receives it on upsert. The server splits heavy fields out into
// write-once per-node blobs and leaves the light node carrying `has_*` flags; the
// renderer then reads blobs through lib/node-blobs. Doing the same split here keeps
// ONE render path instead of a static-only inline special case.
//
// Semantics are the server's, including the falsy nuance: an inline field that is
// present but empty (null / []) moves to the blob and CLEARS the flag, so a node
// can't advertise data it doesn't have.

import type { ConvTree } from './tree';
import type { NodeBlobs } from './types';

export const BLOB_FIELDS = ['token_logprobs', 'raw_meta'] as const;
type BlobField = (typeof BLOB_FIELDS)[number];

const FLAG: Record<BlobField, string> = {
  token_logprobs: 'has_token_logprobs',
  raw_meta: 'has_raw_meta'
};

type AnyNode = Record<string, unknown>;

/** Split one node into [light_node, blob]. An already-light node (a `has_*` flag but
 *  no inline field) keeps its flag and yields an empty blob. */
export function splitNode(node: AnyNode): [AnyNode, NodeBlobs] {
  const light: AnyNode = {};
  for (const [k, v] of Object.entries(node)) {
    if (!(BLOB_FIELDS as readonly string[]).includes(k)) light[k] = v;
  }
  const blob: Record<string, unknown> = {};
  for (const f of BLOB_FIELDS) {
    if (f in node) {
      blob[f] = node[f];
      if (node[f]) light[FLAG[f]] = true;
      else delete light[FLAG[f]];
    }
  }
  return [light, blob as NodeBlobs];
}

/** Split every node of a `{panel_id: tree}` map. Returns the light trees plus the
 *  accumulated per-node blobs (node ids are unique within a workspace, so a shared
 *  id across panels carries identical data — last wins, as server-side). */
export function splitTrees(
  trees: Record<string, ConvTree>
): [Record<string, ConvTree>, Record<string, NodeBlobs>] {
  const blobs: Record<string, NodeBlobs> = {};
  const light: Record<string, ConvTree> = {};
  for (const [pid, tree] of Object.entries(trees || {})) {
    if (!tree || typeof tree !== 'object' || !('nodes' in tree)) {
      light[pid] = tree;
      continue;
    }
    const lightNodes: Record<string, unknown> = {};
    for (const [nid, node] of Object.entries((tree as any).nodes || {})) {
      if (!node || typeof node !== 'object') {
        lightNodes[nid] = node;
        continue;
      }
      const [lnode, blob] = splitNode(node as AnyNode);
      lightNodes[nid] = lnode;
      if (Object.keys(blob).length) blobs[nid] = blob;
    }
    light[pid] = { ...(tree as any), nodes: lightNodes } as ConvTree;
  }
  return [light, blobs];
}
