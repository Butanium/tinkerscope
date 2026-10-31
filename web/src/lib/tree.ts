// Workspace branch tree — PURE, no svelte/browser imports (Node 22 can run
// tree.test.ts directly via type-stripping). This is the single source of truth
// for the branched workspace; the linear ACTIVE PATH it derives is what the
// sampler/CLI read (mirrored into PlaygroundState.messages). See docs/BRANCHING_DESIGN.md.
//
// Key invariants (from the design critique):
//  - `selected` maps a parent → the selected child's ID (NOT an index), so a
//    delete/reorder of a sibling never silently reselects a different node.
//  - default selection (unset / dangling) = the LAST child (newest), so a fresh
//    fork/regen auto-shows the new branch.
//  - every op is immutable: clone → mutate → return.

import { logprobsAfterEdit } from './token-edit.ts';
import type { ReasoningDetail, ResponsesItem, ToolCall } from './types.ts';

export const ROOT = '__root__';

export type NodeRole = 'user' | 'assistant' | 'system' | 'tool';

/** Per-generated-token logprob record (native tinker sampling only — see
 *  docs/API_CONTRACT.md). Raw data: persisted on the node so later features can
 *  recompute (charts, hover) without resampling. */
export type TokenLogprob = {
  /** decoded token text (single-token decode — may show � on a UTF-8 split) */
  t: string;
  tid: number;
  /** logprob of the sampled token at this position (null = unavailable) */
  lp: number | null;
  /** top-K alternatives, most probable first: [text, tid, logprob] */
  top?: [string, number, number][];
  /** GHOST entry: text the model never sampled. `t` is that text, and there is
   *  no probability for it: `tid` is -1, `lp` null, no `top`. Never emitted by
   *  the sampler. Two sources: an EDITED turn's text past the point where it
   *  stopped matching what the model generated (token-edit.ts — stored), and the
   *  authored PREFILL a continuation was drawn from (token-prefill.ts —
   *  display-synthesized, never stored). */
  ghost?: boolean;
  /** Which kind of ghost; absent = the stored edit form. Display-only — the
   *  prefill ghost never round-trips through persistence. */
  ghostKind?: 'prefill';
};

function cloneTokenLogprobs(tlp: TokenLogprob[] | undefined): TokenLogprob[] | undefined {
  // Structural copy (cloneTree escapes $state proxies field-by-field; these
  // entries are never mutated but must not leak proxy references into clones).
  return tlp?.map((e) => ({
    t: e.t,
    tid: e.tid,
    lp: e.lp,
    top: e.top?.map((a) => [...a] as [string, number, number]),
    ...(e.ghost ? { ghost: true } : {})
  }));
}

export type TreeNode = {
  id: string;
  role: NodeRole;
  content: string;
  reasoning?: string; // persisted; populated by foldAssistant from samples
  raw_text?: string; // persisted; survives reload
  raw_meta?: string; // persisted; tinker request/response (dropdown beneath raw_text)
  /** The authored assistant prefill this turn was generated from (raw text, incl.
   *  any `<think>`), persisted so the rendered turn can color the prefilled portion
   *  distinctly from the model's continuation. Absent ⇒ no prefill was used. */
  prefill?: string;
  /** LOOM provenance (a continue_tokens fire — branchOps.loomBranch): how many
   *  leading token_logprobs entries were FORCED (replayed prefix + picked
   *  alternative), and that prefix as frame-normalized display text. Unlike
   *  `prefill`, the forced region IS covered by the stream (fully scored, no
   *  ghost) — the display tints it and marks the fork point. Persisted. */
  loom_cut?: number;
  loom_text?: string;
  /** The provider started a fresh turn instead of continuing the prefill, so
   *  `content` is that fresh turn and there is no `prefill`. Persisted. */
  prefill_ignored?: boolean;
  /** …because the model can't be prefilled: it was sampled without the prefill. */
  prefill_unsupported?: boolean;
  /** A closed model's reasoning payload (OpenRouter), verbatim: what the model's
   *  next turn sees of this turn's thinking (`reasoning` is only its summary).
   *  Kept LIGHT — the browser builds the next fire's messages from the tree.
   *  Carried by an edit that leaves the thinking alone, dropped by one that
   *  drops it; never edited (a modified payload is a provider 400). */
  reasoning_details?: ReasoningDetail[];
  /** Hidden reasoning tokens the provider billed for this turn. */
  reasoning_tokens?: number;
  /** Which OpenRouter account the encrypted reasoning is bound to (a short key
   *  hash). Another account's blob is silently ignored on replay, so the UI flags
   *  a tag that differs from the server's. Absent = unknown (older turns). */
  reasoning_account?: string;
  /** Assistant nodes: the tool calls this turn made (OpenAI shape, `arguments`
   *  verbatim). Answered by a CHAIN of role='tool' children, one per call in
   *  order; until every call is answered the composer offers the result inputs. */
  tool_calls?: ToolCall[];
  /** Tool nodes: which call this result answers, and that function's name. */
  tool_call_id?: string;
  name?: string;
  /** Tool nodes the SERVER answered with the workspace's handler ('static' /
   *  'python') rather than a person — the auto-answered badge. */
  handler?: 'static' | 'python';
  /** The VERBATIM Responses-API items this turn stands for (a recorded agent call,
   *  or a Responses-wire sample's output). HEAVY — a write-once blob server-side
   *  (screenshots), so loaded nodes carry `has_items`; on the wire they go out as
   *  they are. `items_recorded`: what was originally sent, kept when the wire form
   *  differs (a recovered reasoning blob). The light fields above are derived. */
  items?: ResponsesItem[];
  items_recorded?: ResponsesItem[];
  has_items?: boolean;
  has_items_recorded?: boolean;
  /** A COPY of a node with items (a fork-copy, a system-prompt edit's graft)
   *  points at the original's blob instead of duplicating megabytes of
   *  screenshots — blobs are write-once per workspace, so it never dangles. */
  items_ref?: string;
  /** How generation ended ('stop' | 'length' | …) — 'length' ⇒ cut off by the
   *  max-tokens limit; persisted so the truncation badge survives reload. */
  finish_reason?: string;
  /** Renderer mode this sample was drawn with — set ONLY by thinking='both'
   *  batches (false = non-thinking half, true = thinking half); persisted so the
   *  think / no-think chip survives reload. Absent on single-mode turns. */
  thinking?: boolean;
  /** Per-token logprobs + top-K alternatives (native tinker sampling only);
   *  present INLINE only on freshly-folded nodes this session — the server strips
   *  it into a per-node blob on save (storage v2), so loaded nodes carry
   *  `has_token_logprobs` instead and consumers go through lib/node-blobs. */
  token_logprobs?: TokenLogprob[];
  /** Blob-presence flags (storage v2): the server stores heavy fields
   *  (token_logprobs / raw_meta) out-of-tree and marks a light node with these.
   *  Write-once, id-bound — a node COPY (fresh id, e.g. editUserForkCopy) must
   *  NOT inherit them (no blob exists under the new id). */
  has_token_logprobs?: boolean;
  has_raw_meta?: boolean;
  /** Thread system prompt — ROOT user nodes only. Overrides the workspace's
   *  global system prompt at fire time (server-side: this, else the global).
   *  Part of thread IDENTITY: two roots with the same content but different
   *  system prompts are distinct threads (threadStarts / reconcile key on the
   *  pair). Absent ⇒ the thread runs under the global prompt alone (legacy). */
  system_prompt?: string;
  parent: string | null; // null = child of the virtual root
  children: string[]; // ordered
};

export type ConvTree = {
  nodes: Record<string, TreeNode>;
  rootChildren: string[];
  selected: Record<string, string>; // (parentId | ROOT) -> selected child ID
};

/** Every node id across `trees` carrying token-logprob data (inline or a server
 *  blob). Powers the token-probs prefetch: warming these into the blob cache when
 *  the view is on means cycling between completions never waits on a cold blob —
 *  the tokens are already there, so no render flicker and no placeholder needed. */
export function tokenBlobNodeIds(trees: Record<string, ConvTree>): string[] {
  const ids: string[] = [];
  for (const tree of Object.values(trees)) {
    for (const n of Object.values(tree.nodes)) {
      if (n.has_token_logprobs || n.token_logprobs?.length) ids.push(n.id);
    }
  }
  return ids;
}

/** A streamed sample as folded into the tree (subset of SampleData). */
export type SampleLike = {
  content?: string;
  reasoning?: string;
  raw_text?: string;
  raw_meta?: string;
  error?: string;
  /** Native tinker path with a prefill: content/reasoning already span
   *  prefill+completion, so the client must not re-prepend the prefill. */
  prefill_incorporated?: boolean;
  prefill_ignored?: boolean;
  prefill_unsupported?: boolean;
  reasoning_details?: ReasoningDetail[];
  reasoning_tokens?: number;
  reasoning_account?: string;
  tool_calls?: ToolCall[];
  items?: ResponsesItem[];
  /** The authored prefill this sample was generated from (raw text) — folded onto
   *  the node so the rendered turn can color the prefilled prefix. */
  prefill?: string;
  /** LOOM provenance — see TreeNode.loom_cut / loom_text. */
  loom_cut?: number;
  loom_text?: string;
  /** How generation ended — 'length' ⇒ cut off by the max-tokens limit. */
  finish_reason?: string;
  /** Renderer mode (thinking='both' batches only) — see TreeNode.thinking. */
  thinking?: boolean;
  /** Per-token logprobs (native tinker sampling only) — see TreeNode. */
  token_logprobs?: TokenLogprob[];
  sample_index?: number;
};

export type Msg = {
  role: NodeRole;
  content: string;
  reasoning?: string;
  reasoning_details?: ReasoningDetail[];
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  name?: string;
  items?: ResponsesItem[];
  items_node?: string;
};

/** Tree node → wire Msg. Carries `reasoning` ONLY when present, so the sampler can hand
 *  the renderer the full turn ([thinking, text] structured content) and let the model's
 *  renderer apply its own history policy (strip_thinking_from_history / preserve). A turn
 *  without reasoning stays `{role, content}` — byte-identical to before, so the UI mirror,
 *  persistence echo, and tree tests are unaffected. `content` is always answer-only; the
 *  thinking lives in the separate field, never inlined as a `<think>` string (the renderers
 *  pass string content through verbatim, so an inlined tag would force-keep the CoT). */
function nodeToMsg(n: TreeNode): Msg {
  return {
    role: n.role,
    content: n.content,
    ...(n.reasoning ? { reasoning: n.reasoning } : {}),
    ...(n.reasoning_details?.length ? { reasoning_details: n.reasoning_details } : {}),
    ...(n.tool_calls?.length ? { tool_calls: n.tool_calls } : {}),
    ...(n.role === 'tool' ? { tool_call_id: n.tool_call_id ?? '', ...(n.name ? { name: n.name } : {}) } : {}),
    // verbatim Responses items: inline when held, else by node (the server loads the blob)
    ...(n.items?.length ? { items: n.items } : n.has_items ? { items_node: n.items_ref ?? n.id } : {})
  };
}

// ── IDs ──────────────────────────────────────────────────────────────
// Per-load random session prefix so two tabs editing one persisted tree never
// mint colliding ids. `__resetIds()` makes tests deterministic.
let _session = randomSession();
let _counter = 0;

function randomSession(): string {
  // Math.random is fine here (the workflow-script ban doesn't apply to the
  // browser/node runtime). 4 base36 chars ≈ 1.6M sessions.
  return Math.random().toString(36).slice(2, 6);
}

export function nid(): string {
  return 'n' + _session + (++_counter).toString(36);
}

/** Test hook: deterministic ids from a fixed session + zeroed counter. */
export function __resetIds(session = 'tst'): void {
  _session = session;
  _counter = 0;
}

// ── construction ─────────────────────────────────────────────────────
export function emptyTree(): ConvTree {
  return { nodes: {}, rootChildren: [], selected: {} };
}

function cloneTree(t: ConvTree): ConvTree {
  // Manual deep clone (NOT structuredClone): callers pass a Svelte $state proxy,
  // which structuredClone refuses ("could not be cloned"). Reading through the
  // proxy property-by-property yields plain objects the ops can mutate + persist.
  const nodes: Record<string, TreeNode> = {};
  for (const id in t.nodes) {
    const n = t.nodes[id];
    // Spread, then deep-copy the two nested fields: a field-by-field list silently
    // dropped every field added after it (loom_cut / loom_text went missing from
    // every node of a panel on its next local edit).
    nodes[id] = {
      ...n,
      token_logprobs: cloneTokenLogprobs(n.token_logprobs),
      children: [...n.children]
    };
  }
  return { nodes, rootChildren: [...t.rootChildren], selected: { ...t.selected } };
}

function childArray(t: ConvTree, parentKey: string): string[] {
  return parentKey === ROOT ? t.rootChildren : t.nodes[parentKey].children;
}

function parentKeyOfNode(n: TreeNode): string {
  return n.parent ?? ROOT;
}

// ── derivation ───────────────────────────────────────────────────────
/** The selected child id of `parentKey`, or the last child (default), or null. */
export function selectedChildId(t: ConvTree, parentKey: string): string | null {
  const kids = parentKey === ROOT ? t.rootChildren : t.nodes[parentKey]?.children;
  if (!kids || kids.length === 0) return null;
  const sel = t.selected[parentKey];
  if (sel != null && kids.includes(sel)) return sel;
  return kids[kids.length - 1];
}

/** Root → leaf, following the selected child at each step. */
export function activePath(t: ConvTree): TreeNode[] {
  const path: TreeNode[] = [];
  let parentKey = ROOT;
  const seen = new Set<string>(); // cycle guard (defensive; trees are acyclic)
  while (true) {
    const childId = selectedChildId(t, parentKey);
    if (childId == null || seen.has(childId)) break;
    const node = t.nodes[childId];
    if (!node) break;
    seen.add(childId);
    path.push(node);
    parentKey = childId;
  }
  return path;
}

/** Does this node go out with a fire? Everything but system turns — except a
 *  system node carrying verbatim items (a recorded call's system message, or an
 *  edit of one): that IS the system prompt, and the server then composes none.
 *  Mirrors cli.py `_keep_in_ancestry`. */
export function sentInFire(n: TreeNode): boolean {
  return n.role !== 'system' || !!n.items?.length || !!n.has_items;
}

/** The active path as [{role,content}] (system turns excluded, see sentInFire). */
export function activeMessages(t: ConvTree): Msg[] {
  return activePath(t).filter(sentInFire).map(nodeToMsg);
}

export function parentKeyOf(t: ConvTree, id: string): string {
  const n = t.nodes[id];
  return n ? parentKeyOfNode(n) : ROOT;
}

export function siblingsOf(t: ConvTree, id: string): string[] {
  // A missing id must NOT alias ROOT's children (that would fabricate a sibling
  // set from unrelated roots); a stale id has no siblings.
  if (id !== ROOT && !t.nodes[id]) return [];
  return childArray(t, parentKeyOf(t, id));
}

export function siblingInfo(t: ConvTree, id: string): { index: number; count: number } {
  const sibs = siblingsOf(t, id);
  return { index: sibs.indexOf(id), count: sibs.length };
}

/** Messages from the root down to `id` inclusive, via the PARENT chain (so it
 *  works regardless of the current selection). System turns excluded. */
export function ancestryMessages(t: ConvTree, id: string): Msg[] {
  const chain: TreeNode[] = [];
  let cur: TreeNode | undefined = t.nodes[id];
  const seen = new Set<string>();
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id);
    chain.push(cur);
    cur = cur.parent ? t.nodes[cur.parent] : undefined;
  }
  chain.reverse();
  return chain.filter(sentInFire).map(nodeToMsg);
}

/** The active-path nodes strictly BELOW `id` (or [] if id isn't on the path). */
function downstreamActivePath(t: ConvTree, id: string): TreeNode[] {
  const path = activePath(t);
  const i = path.findIndex((n) => n.id === id);
  return i < 0 ? [] : path.slice(i + 1);
}

// ── mutations (immutable) ────────────────────────────────────────────
/** Append a user turn under the active leaf — or, with `atRoot`, as a NEW
 *  root-level branch (a sibling first message) that becomes the active path.
 *  `systemPrompt` stamps the new thread's system prompt on the root node — when
 *  the turn IS a root (atRoot, or the tree's first message); ignored for
 *  mid-thread appends (the field is root-only). */
export function appendUserTurn(
  t0: ConvTree,
  content: string,
  atRoot = false,
  systemPrompt?: string
): { tree: ConvTree; nodeId: string } {
  const t = cloneTree(t0);
  const path = activePath(t);
  const leaf = atRoot ? null : path.length ? path[path.length - 1] : null;
  const parentKey = leaf ? leaf.id : ROOT;
  const id = nid();
  t.nodes[id] = {
    id,
    role: 'user',
    content,
    ...(!leaf && systemPrompt ? { system_prompt: systemPrompt } : {}),
    parent: leaf ? leaf.id : null,
    children: []
  };
  childArray(t, parentKey).push(id);
  t.selected[parentKey] = id;
  return { tree: t, nodeId: id };
}

export function foldAssistant(
  t0: ConvTree,
  parentUserId: string,
  samples: SampleLike[]
): { tree: ConvTree; ids: string[] } {
  const t = cloneTree(t0);
  const parent = t.nodes[parentUserId];
  if (!parent) return { tree: t, ids: [] }; // parent pruned mid-fold — drop quietly
  const ordered = [...samples].sort((a, b) => (a.sample_index ?? 0) - (b.sample_index ?? 0));
  const ids: string[] = [];
  for (const s of ordered) {
    if (s.error) continue; // skip error samples; don't shift indices into nodes
    const id = nid();
    t.nodes[id] = {
      id,
      role: 'assistant',
      content: s.content ?? '',
      reasoning: s.reasoning,
      raw_text: s.raw_text,
        raw_meta: s.raw_meta,
      prefill: s.prefill,
      loom_cut: s.loom_cut,
      loom_text: s.loom_text,
      prefill_ignored: s.prefill_ignored,
      prefill_unsupported: s.prefill_unsupported,
      reasoning_details: s.reasoning_details,
      reasoning_tokens: s.reasoning_tokens,
      reasoning_account: s.reasoning_account,
      tool_calls: s.tool_calls,
      items: s.items,
      finish_reason: s.finish_reason,
      thinking: s.thinking,
      token_logprobs: cloneTokenLogprobs(s.token_logprobs),
      parent: parentUserId,
      children: []
    };
    parent.children.push(id);
    ids.push(id);
  }
  if (ids.length) t.selected[parentUserId] = ids[0]; // select FIRST of the new batch
  return { tree: t, ids };
}

/** Can an assistant turn be generated under this node? A user turn, or a tool
 *  result (the model's turn after its tool calls are answered). */
export function isFireParent(n: TreeNode | undefined): boolean {
  return n?.role === 'user' || n?.role === 'tool';
}

/** `userParentId` is the node the regenerated turn folds under — a user turn or a
 *  tool result (the name predates tool use). */
export function regenTarget(
  t: ConvTree,
  nodeId: string
): { userParentId: string; fireMessages: Msg[] } | null {
  const node = t.nodes[nodeId];
  if (!node) return null;
  const userId = node.role === 'assistant' ? node.parent : nodeId;
  if (!userId || !isFireParent(t.nodes[userId])) return null;
  return { userParentId: userId, fireMessages: ancestryMessages(t, userId) };
}

/** The tool calls on the active path still waiting for a result: the active
 *  leaf is an assistant turn with `tool_calls`, or a tool result answering one
 *  of them, and some of those calls have no result on the path yet. Results
 *  are matched by `tool_call_id`. null when nothing is pending. */
export function pendingToolCalls(
  t: ConvTree
): { assistantId: string; leafId: string; pending: ToolCall[] } | null {
  const path = activePath(t);
  let i = path.length - 1;
  while (i >= 0 && path[i].role === 'tool') i--;
  const asst = i >= 0 ? path[i] : undefined;
  if (!asst || asst.role !== 'assistant' || !asst.tool_calls?.length) return null;
  const answered = new Set(path.slice(i + 1).map((n) => n.tool_call_id));
  const pending = asst.tool_calls.filter((c) => !answered.has(c.id));
  return pending.length ? { assistantId: asst.id, leafId: path[path.length - 1].id, pending } : null;
}

/** Append tool results under the active leaf as a chain (one tool node per
 *  result, in the order given), selecting each — the composer's answer to
 *  `pendingToolCalls`. Returns the new ids; the LAST one is where the model's
 *  next turn folds. */
export function appendToolResults(
  t0: ConvTree,
  results: { tool_call_id: string; name?: string; content: string; items?: ResponsesItem[] }[]
): { tree: ConvTree; ids: string[] } {
  const t = cloneTree(t0);
  const path = activePath(t);
  let parent = path.length ? path[path.length - 1].id : null;
  const ids: string[] = [];
  for (const r of results) {
    const id = nid();
    t.nodes[id] = {
      id,
      role: 'tool',
      content: r.content,
      tool_call_id: r.tool_call_id,
      ...(r.name ? { name: r.name } : {}),
      ...(r.items?.length ? { items: r.items } : {}),
      parent,
      children: []
    };
    const key = parent ?? ROOT;
    childArray(t, key).push(id);
    t.selected[key] = id;
    ids.push(id);
    parent = id;
  }
  return { tree: t, ids };
}

/** Fork a tool result: a sibling with the edited content answering the same
 *  call, selected. Later results in the chain are NOT copied — the composer then
 *  shows the calls still unanswered on the new branch. */
export function editToolResult(
  t0: ConvTree,
  toolId: string,
  content: string
): { tree: ConvTree; newId: string } | null {
  const orig = t0.nodes[toolId];
  if (!orig || orig.role !== 'tool') return null;
  const t = cloneTree(t0);
  const parentKey = orig.parent ?? ROOT;
  const id = nid();
  t.nodes[id] = {
    id,
    role: 'tool',
    content,
    tool_call_id: orig.tool_call_id,
    ...(orig.name ? { name: orig.name } : {}),
    parent: orig.parent,
    children: []
  };
  childArray(t, parentKey).push(id);
  t.selected[parentKey] = id;
  return { tree: t, newId: id };
}

/** Fork a sibling of `userId` with the edited content (+ regen by the caller).
 *  `systemPrompt` applies only when the edited node is a ROOT (thread) node: it
 *  becomes the fork's thread system prompt ('' / undefined ⇒ none — the edit UI
 *  passes the field's full value, so "keep" needs no special case). */
/** A recorded user turn's items after an edit of its text: the text parts give
 *  way to ONE part with the new text (where the first one stood), and every
 *  other part (screenshots) and item stays verbatim, in order. The edit box
 *  shows the parts joined, so there's no telling which part an edit touched —
 *  one part for all of it is the honest mapping. */
export function editedUserItems(items: ResponsesItem[], content: string): ResponsesItem[] {
  const out: ResponsesItem[] = [];
  let placed = false;
  const isText = (p: ResponsesItem) => p?.type === 'input_text' || p?.type === 'text' || p?.type === 'output_text';
  for (const it of items) {
    if ((it?.type ?? 'message') !== 'message') {
      out.push(it);
      continue;
    }
    if (typeof it.content === 'string') {
      if (!placed && content) out.push({ role: it.role ?? 'user', content });
      placed = true;
      continue;
    }
    const kept: ResponsesItem[] = [];
    for (const part of Array.isArray(it.content) ? it.content : []) {
      if (!isText(part)) kept.push(part);
      else if (!placed) {
        if (content) kept.push({ type: 'input_text', text: content });
        placed = true;
      }
    }
    if (kept.length) out.push({ ...it, content: kept });
  }
  if (!placed && content) out.unshift({ role: 'user', content });
  return out;
}

export function editUserFork(
  t0: ConvTree,
  userId: string,
  content: string,
  systemPrompt?: string,
  items?: ResponsesItem[]
): { tree: ConvTree; newUserId: string; fireMessages: Msg[] } | null {
  const orig = t0.nodes[userId];
  if (!orig || orig.role !== 'user') return null;
  const t = cloneTree(t0);
  const parentKey = orig.parent ?? ROOT;
  const id = nid();
  t.nodes[id] = {
    id,
    role: 'user',
    content,
    ...(orig.parent === null && systemPrompt ? { system_prompt: systemPrompt } : {}),
    ...(items?.length ? { items: editedUserItems(items, content) } : {}),
    parent: orig.parent,
    children: []
  };
  childArray(t, parentKey).push(id);
  t.selected[parentKey] = id;
  return { tree: t, newUserId: id, fireMessages: ancestryMessages(t, id) };
}

export function editUserForkCopy(
  t0: ConvTree,
  userId: string,
  content: string,
  systemPrompt?: string,
  items?: ResponsesItem[]
): { tree: ConvTree; newUserId: string } | null {
  const orig = t0.nodes[userId];
  if (!orig || orig.role !== 'user') return null;
  const downstream = downstreamActivePath(t0, userId); // from the ORIGINAL selection
  const t = cloneTree(t0);
  const parentKey = orig.parent ?? ROOT;
  const newUserId = nid();
  t.nodes[newUserId] = {
    id: newUserId,
    role: 'user',
    content,
    ...(orig.parent === null && systemPrompt ? { system_prompt: systemPrompt } : {}),
    ...(items?.length ? { items: editedUserItems(items, content) } : {}),
    parent: orig.parent,
    children: []
  };
  childArray(t, parentKey).push(newUserId);
  t.selected[parentKey] = newUserId;
  graftDownstream(t, newUserId, downstream);
  return { tree: t, newUserId };
}

/** Deep-copy `chain` (an active-path slice from the ORIGINAL tree) under `rootId`
 *  as fresh-id single-child descendants, writing a `selected` entry at each step
 *  so the copy is the active path. Mutates `t` — callers pass a cloned tree.
 *
 *  Heavy fields are deliberately NOT carried: a copy is a new node with no blob
 *  behind it, and `token_logprobs` belong to the ids the model actually sampled. */
function graftDownstream(t: ConvTree, rootId: string, chain: TreeNode[]): void {
  let curParent = rootId; // may be ROOT: the copy then starts a new thread
  for (const node of chain) {
    const cid = nid();
    t.nodes[cid] = {
      id: cid,
      role: node.role,
      content: node.content,
      reasoning: node.reasoning,
      // the reasoning payload goes with its text: OpenAI accepts a blob replayed
      // under a different user turn (probed 2026-10-15), so a copy still carries it
      reasoning_details: node.reasoning_details,
      reasoning_tokens: node.reasoning_tokens,
      reasoning_account: node.reasoning_account,
      tool_calls: node.tool_calls,
      ...(node.role === 'tool' ? { tool_call_id: node.tool_call_id, name: node.name } : {}),
      raw_text: node.raw_text,
      raw_meta: node.raw_meta,
      // a recorded turn's items: inline when held, else a reference to the original's blob
      ...(node.items?.length
        ? { items: node.items }
        : node.has_items
          ? { has_items: true, items_ref: node.items_ref ?? node.id }
          : {}),
      parent: curParent === ROOT ? null : curParent,
      children: []
    };
    childArray(t, curParent).push(cid);
    t.selected[curParent] = cid;
    curParent = cid;
  }
}

/** Edit a SYSTEM node (a recorded call's system message) like a first message:
 *  a sibling system node with the new text, and below it a copy of the
 *  conversation — all of it with `copyDownstream` (nothing is generated), else
 *  only the turns up to the first reply, which `fireFrom` then regenerates. The
 *  new node keeps the original item's role (system / developer); its text is
 *  one plain part. Empty `content` DROPS the prompt: the copy starts a new thread
 *  at the first turn below it, with no system message at all. */
export function editSystemFork(
  t0: ConvTree,
  sysId: string,
  content: string,
  copyDownstream: boolean,
  items?: ResponsesItem[]
): { tree: ConvTree; newId: string; fireFrom: string | null } | null {
  const orig = t0.nodes[sysId];
  if (!orig || orig.role !== 'system') return null;
  const below = downstreamActivePath(t0, sysId);
  const firstReply = below.findIndex((n) => n.role === 'assistant');
  const graft = copyDownstream ? below : firstReply < 0 ? below : below.slice(0, firstReply);
  const t = cloneTree(t0);
  const parentKey = orig.parent ?? ROOT;
  if (!content.trim()) {
    if (!graft.length) return null; // nothing would be left of the thread
    graftDownstream(t, parentKey, graft);
    const newId = t.selected[parentKey];
    let last = newId;
    while (t.nodes[last].children.length) last = t.nodes[last].children[0];
    return { tree: t, newId, fireFrom: !copyDownstream && t.nodes[last].role !== 'assistant' ? last : null };
  }
  const id = nid();
  const role = (items ?? []).find((it) => it?.role === 'system' || it?.role === 'developer')?.role ?? 'system';
  t.nodes[id] = {
    id,
    role: 'system',
    content,
    ...(orig.has_items || orig.items?.length ? { items: [{ role, content }] } : {}),
    parent: orig.parent,
    children: []
  };
  childArray(t, parentKey).push(id);
  t.selected[parentKey] = id;
  graftDownstream(t, id, graft);
  let last = id;
  while (t.nodes[last].children.length) last = t.nodes[last].children[0];
  const fireFrom = !copyDownstream && last !== id && t.nodes[last].role !== 'assistant' ? last : null;
  return { tree: t, newId: id, fireFrom };
}

/** A Responses turn's items after an edit of its text: the assistant message
 *  items give way to ONE plain message with the new text (where the first one
 *  stood; before the calls when there was none), calls stay verbatim, and the
 *  reasoning items stay only while the thinking is kept — so a kept summary
 *  still has its encrypted reasoning behind it on the next turn. */
export function editedItems(items: ResponsesItem[], content: string, keepReasoning: boolean): ResponsesItem[] {
  const msg: ResponsesItem[] = content ? [{ role: 'assistant', content }] : [];
  const out: ResponsesItem[] = [];
  let placed = false;
  for (const it of items) {
    const t = it?.type ?? 'message';
    if (t === 'message' && it.role === 'assistant') {
      if (!placed) out.push(...msg);
      placed = true;
      continue;
    }
    if (t === 'reasoning' && !keepReasoning) continue;
    if (!placed && (t === 'function_call' || t === 'computer_call')) {
      out.push(...msg);
      placed = true;
    }
    out.push(it);
  }
  if (!placed) out.push(...msg);
  return out;
}

/** `tokenLogprobs` = the ORIGINAL's token stream, when the caller could resolve
 *  it (inline, or out of the node-blob cache — this module can't fetch). The new
 *  node inherits the part of it the edit left untouched, with the rest as one
 *  data-less ghost entry. See token-edit.ts. `items` = the original's verbatim
 *  Responses items, likewise resolved by the caller (see editedItems). */
export function editAssistant(
  t0: ConvTree,
  asstId: string,
  content: string,
  reasoning?: string,
  tokenLogprobs?: TokenLogprob[],
  copyDownstream = false,
  items?: ResponsesItem[]
): { tree: ConvTree; newId: string } | null {
  const orig = t0.nodes[asstId];
  if (!orig || orig.role !== 'assistant') return null;
  // From the ORIGINAL selection, before the new sibling takes it over.
  const downstream = copyDownstream ? downstreamActivePath(t0, asstId) : [];
  const t = cloneTree(t0);
  const parentKey = orig.parent ?? ROOT;
  const id = nid();
  const kept = logprobsAfterEdit(
    tokenLogprobs,
    { reasoning: orig.reasoning, content: orig.content },
    { reasoning, content }
  );
  // A closed model's reasoning payload rides along only while the thinking is
  // left exactly as it was: an edited summary isn't what the model saw, and a
  // dropped one ('' — the edit dialog's "drop thinking") takes the payload with it.
  const keepPayload = reasoning === orig.reasoning;
  // Manual branch: store the edited reasoning (empty ⇒ drop the CoT). raw_text /
  // raw_meta / prefill are the model's originals — stale after a hand-edit, so omit.
  t.nodes[id] = {
    id,
    role: 'assistant',
    content,
    reasoning: reasoning && reasoning.trim() ? reasoning : undefined,
    ...(keepPayload && orig.reasoning_details?.length
      ? {
          reasoning_details: orig.reasoning_details,
          reasoning_tokens: orig.reasoning_tokens,
          reasoning_account: orig.reasoning_account
        }
      : {}),
    ...(items?.length
      ? {
          items: editedItems(items, content, keepPayload),
          ...(keepPayload ? { reasoning_tokens: orig.reasoning_tokens, reasoning_account: orig.reasoning_account } : {})
        }
      : {}),
    // The calls are part of what the turn DID; editing its text keeps them.
    ...(orig.tool_calls?.length ? { tool_calls: orig.tool_calls } : {}),
    token_logprobs: cloneTokenLogprobs(kept),
    parent: orig.parent,
    children: []
  };
  childArray(t, parentKey).push(id);
  t.selected[parentKey] = id;
  graftDownstream(t, id, downstream);
  return { tree: t, newId: id };
}

export function deleteSubtree(t0: ConvTree, nodeId: string): ConvTree {
  const node = t0.nodes[nodeId];
  if (!node) return t0;
  const t = cloneTree(t0);
  const parentKey = node.parent ?? ROOT;
  // collect the whole subtree
  const toRemove: string[] = [];
  const stack = [nodeId];
  while (stack.length) {
    const id = stack.pop()!;
    toRemove.push(id);
    const n = t.nodes[id];
    if (n) stack.push(...n.children);
  }
  const removeSet = new Set(toRemove);
  const sibs = childArray(t, parentKey);
  const idx = sibs.indexOf(nodeId);
  if (idx >= 0) sibs.splice(idx, 1);
  for (const id of toRemove) {
    delete t.nodes[id];
    delete t.selected[id];
  }
  // if the parent's selected child was pruned, drop it → default-last picks a
  // surviving sibling (or none). Selection-by-id means UNRELATED siblings keep
  // their selection regardless of array shifts.
  if (t.selected[parentKey] != null && removeSet.has(t.selected[parentKey])) {
    delete t.selected[parentKey];
  }
  return t;
}

/** Delete EVERY sibling branch at this node's level (each + its subtree), not just
 *  this one — shift+delete. Leaves the parent with no children at this position. */
export function deleteSiblings(t0: ConvTree, nodeId: string): ConvTree {
  const sibs = [...siblingsOf(t0, nodeId)];
  if (sibs.length === 0) return t0;
  let t = t0;
  for (const sib of sibs) t = deleteSubtree(t, sib);
  return t;
}

/** Shift-regenerate: drop the CURRENTLY-ACTIVE assistant branch under this row's
 *  user parent (other siblings preserved) so a fresh reply REPLACES it in place,
 *  rather than appending a new sibling. Returns the pruned tree + fire target
 *  (the user parent's ancestry is unchanged by the deletion). */
export function regenReplace(
  t0: ConvTree,
  nodeId: string
): { tree: ConvTree; userParentId: string; fireMessages: Msg[]; prunedId: string | null } | null {
  const rt = regenTarget(t0, nodeId);
  if (!rt) return null;
  const active = selectedChildId(t0, rt.userParentId);
  const prune = active != null && t0.nodes[active]?.role === 'assistant';
  const t = prune ? deleteSubtree(t0, active) : t0;
  return {
    tree: t,
    userParentId: rt.userParentId,
    fireMessages: ancestryMessages(t, rt.userParentId),
    prunedId: prune ? active : null
  };
}

/** Select `nodeId` as its parent's active child. */
export function setSelected(t0: ConvTree, nodeId: string): ConvTree {
  const node = t0.nodes[nodeId];
  if (!node) return t0;
  const parentKey = node.parent ?? ROOT;
  if (!childArray(t0, parentKey).includes(nodeId)) return t0;
  const t = cloneTree(t0);
  t.selected[parentKey] = nodeId;
  return t;
}

/** Select the WHOLE ancestor chain so `nodeId` lands ON the active path (each
 *  ancestor becomes its parent's selected child) — the search palette's
 *  jump-and-reveal, where the target is usually a folded sibling several levels
 *  deep. Returns the SAME ref when the node is already active (cheap no-op
 *  detection for callers), and bails whole (no partial selection) on a broken
 *  parent chain. */
export function selectPathTo(t0: ConvTree, nodeId: string): ConvTree {
  if (!t0.nodes[nodeId]) return t0;
  const writes: Array<[string, string]> = [];
  let cur: TreeNode | undefined = t0.nodes[nodeId];
  const seen = new Set<string>();
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id);
    const parentKey = cur.parent ?? ROOT;
    if (parentKey !== ROOT && !t0.nodes[parentKey]) return t0; // orphaned chain
    if (!childArray(t0, parentKey).includes(cur.id)) return t0; // corrupt link
    if (selectedChildId(t0, parentKey) !== cur.id) writes.push([parentKey, cur.id]);
    if (cur.parent === null) {
      if (writes.length === 0) return t0;
      const t = cloneTree(t0);
      for (const [pk, id] of writes) t.selected[pk] = id;
      return t;
    }
    cur = t0.nodes[cur.parent];
  }
  return t0; // orphaned chain or cycle — never half-select
}

/** Step the selection ±delta among `nodeId`'s siblings, WRAPPING around the ends
 *  (next past the last → first, prev before the first → last; 1-2-3-1-2-3…). */
export function cycle(t0: ConvTree, nodeId: string, delta: number): ConvTree {
  const sibs = siblingsOf(t0, nodeId);
  const n = sibs.length;
  const i = sibs.indexOf(nodeId);
  if (i < 0 || n === 0) return t0;
  const j = ((i + delta) % n + n) % n; // positive modulo → wraps both directions
  if (j === i) return t0;
  return setSelected(t0, sibs[j]);
}

// ── threads (root siblings, cross-panel) ─────────────────────────────
export type ThreadStart = {
  /** The thread's first message (raw text of its first occurrence). */
  content: string;
  /** The thread's system prompt (root node field) — absent = global only. */
  system?: string;
  /** panelId → that panel's matching root node id. */
  roots: Record<string, string>;
  /** Panels where this thread is currently the SELECTED root. */
  activeIn: string[];
};

/** Union of root THREADS (branch-from-start first messages) across panels,
 *  identified by the (trimmed first-message content, thread system prompt)
 *  PAIR, in order of first appearance — the same question under two different
 *  system prompts is two distinct threads (the probe-battery pattern).
 *  Threads are per-panel — different models may hold different probe sets —
 *  so `roots` records which panels have each one; there is no forced alignment. */
export function threadStarts(trees: Record<string, ConvTree>): ThreadStart[] {
  const out: ThreadStart[] = [];
  const byKey = new Map<string, ThreadStart>();
  for (const [pid, t] of Object.entries(trees)) {
    const sel = selectedChildId(t, ROOT);
    for (const rid of t.rootChildren) {
      const node = t.nodes[rid];
      if (!node) continue;
      const key = (node.system_prompt ?? '') + '\u0000' + node.content.trim();
      let ts = byKey.get(key);
      if (!ts) {
        ts = { content: node.content, system: node.system_prompt, roots: {}, activeIn: [] };
        byKey.set(key, ts);
        out.push(ts);
      }
      if (!(pid in ts.roots)) ts.roots[pid] = rid; // first same-pair sibling wins
      if (rid === sel) ts.activeIn.push(pid);
    }
  }
  return out;
}

/** The thread system prompt governing `nodeId`: walk the parent chain to the
 *  thread's ROOT and return its `system_prompt` (undefined = global only).
 *  Every fire into an existing thread re-sends this (regen deep in a probe
 *  thread must compose the probe's prompt, not the current composer's). */
export function threadSystemAt(t: ConvTree, nodeId: string): string | undefined {
  let cur: TreeNode | undefined = t.nodes[nodeId];
  const seen = new Set<string>();
  while (cur && cur.parent !== null && !seen.has(cur.id)) {
    seen.add(cur.id);
    cur = t.nodes[cur.parent];
  }
  return cur?.system_prompt;
}

// ── ops (server-authority protocol — docs/HANDOFF_SERVER_AUTHORITY.md §4.1) ──
// The TREE-LEVEL half of the op vocabulary: what one panel's tree can absorb.
// Map-level ops (copy_tree / replace_tree) and set_meta live in the workspace
// store — they need the whole trees map / workspace fields. Panel stamping is
// the caller's job too; this module stays single-tree.
//
// Confluence guard (protocol invariant, not style): every op here is either
// idempotent-structural (add by globally-unique id + append, delete subtree by
// id) or last-writer-wins (select). Never add an op that mutates node content
// in place or inserts at an arbitrary index — either breaks confluent replay
// and would force a real CRDT.

/** Wire shape of a node in an add_nodes op: TreeNode minus `children` (the
 *  interpreter initializes them — an op can only ever APPEND under a parent). */
export type OpNode = Omit<TreeNode, 'children'>;

export type TreeOp =
  | { op: 'add_nodes'; nodes: OpNode[]; select?: boolean }
  | { op: 'select'; parent_key: string; child_id: string }
  | { op: 'delete'; node_id: string };

/** A structurally invalid op (unknown parent, id reused with different content).
 *  The server answers these with a 409; a mirror applying a bus echo treats a
 *  throw as divergence and refetches. */
export class OpRejected extends Error {}

/** Node ids become blob FILENAMES server-side (workspace_store._SAFE_ID) —
 *  both interpreters reject anything else before it can persist. */
const SAFE_NODE_ID = /^[A-Za-z0-9_-]+$/;

/** A node's op-wire form: everything but `children`, undefined fields dropped
 *  (they would round-trip as JSON nulls otherwise). */
export function opNode(t: ConvTree, id: string): OpNode {
  const { children: _children, ...rest } = t.nodes[id];
  return Object.fromEntries(
    Object.entries(rest).filter(([, v]) => v !== undefined)
  ) as OpNode;
}

/** The single-child chain hanging from `startId` along `selected` — exactly what
 *  graftDownstream wrote. Lets a call-site recover the ids of a fork-copy's
 *  grafted tail (editUserForkCopy / editAssistant don't return them). */
export function chainFrom(t: ConvTree, startId: string): string[] {
  const out: string[] = [startId];
  let cur = startId;
  const seen = new Set<string>([startId]);
  while (true) {
    const next = t.selected[cur];
    if (!next || !t.nodes[next] || seen.has(next)) break;
    out.push(next);
    seen.add(next);
    cur = next;
  }
  return out;
}

/** Selection writes that turn `prev` into `next` — the precise `select` ops for
 *  any selection-only mutation (cycle / setSelected / selectPathTo / thread
 *  switch), read off the trees instead of threaded through every helper. */
export function selectedDiffOps(prev: ConvTree, next: ConvTree): TreeOp[] {
  const ops: TreeOp[] = [];
  for (const [pk, cid] of Object.entries(next.selected)) {
    if (prev.selected[pk] !== cid) ops.push({ op: 'select', parent_key: pk, child_id: cid });
  }
  return ops;
}

/** Apply one tree-level op. Same-ref return on no-ops (idempotent replay, stale
 *  select); throws OpRejected on structural violations. Mirrors the server's
 *  `tree_ops.py` — the shared fixture vectors pin the two implementations
 *  together. `select: true` on add_nodes = first-writer-per-parent within the
 *  batch: a chain selects every step (graftDownstream), a sibling fan selects
 *  its first node (foldAssistant). */
export function applyTreeOp(t0: ConvTree, op: TreeOp): ConvTree {
  if (op.op === 'add_nodes') {
    let t: ConvTree | null = null;
    // Confluence rules for existing nodes (must match tree_ops.py — the
    // add_replay_re_appends_and_re_selects vector pins both). Under
    // always-apply, a mirror applies its own op EARLY and then replays the
    // canonical rev order over the top, so NOTHING here may branch on "did
    // this mirror mint the node" — that's local optimistic state, and
    // branching on it is the §4.2 skip-own divergence one level down (two
    // tabs folding under one parent left tab B on the wrong sibling with
    // contiguous revs, so no refetch ever repaired it):
    //  1. the FIRST node per parent in the batch WRITES the selection,
    //     minted-or-not. Cost: a replayed fold transiently yanks a cycled
    //     view back — visible and accepted, vs silent divergence.
    //  2. an existing node is RE-APPENDED to the end of its parent's
    //     children, so replaying a batch reproduces rev order exactly (a
    //     full-batch replay is a fixpoint: moving each of [a,b,c] to the end
    //     in turn lands them back in order). Without the move, sibling order
    //     disagrees across mirrors, and where `selected` is unset the
    //     default-LAST render turns that into an active-path disagreement.
    const claimed = new Set<string>();
    for (const w of op.nodes) {
      const cur = t ?? t0;
      const parentKey = w.parent ?? ROOT;
      const existing = cur.nodes[w.id];
      if (existing) {
        // Node identity = role+content+PARENT, exactly those three (parity with
        // tree_ops.py; wording pinned by the fixture vectors). Not the whole
        // body: a replay may carry heavy fields inline that the stored light
        // node holds as has_* flags, so a full compare would false-reject
        // every retry. Parent matters: without it a buggy client re-sending an
        // id under a different parent was a silent skip-with-claim.
        if (
          existing.role !== w.role ||
          existing.content !== w.content ||
          (existing.parent ?? null) !== (w.parent ?? null)
        )
          throw new OpRejected(`node ${w.id} already exists with different role/content/parent`);
        const kids = parentKey === ROOT ? cur.rootChildren : cur.nodes[parentKey]?.children;
        const idx = kids ? kids.indexOf(w.id) : -1;
        if (idx >= 0 && idx !== kids!.length - 1) {
          t ??= cloneTree(t0);
          const arr = childArray(t, parentKey);
          arr.splice(arr.indexOf(w.id), 1);
          arr.push(w.id);
        }
        if (op.select && !claimed.has(parentKey)) {
          claimed.add(parentKey);
          if ((t ?? t0).selected[parentKey] !== w.id) {
            t ??= cloneTree(t0);
            t.selected[parentKey] = w.id;
          }
        }
        continue;
      }
      if (!SAFE_NODE_ID.test(w.id))
        throw new OpRejected(`unsafe node id ${JSON.stringify(w.id)}`);
      if (parentKey !== ROOT && !cur.nodes[parentKey])
        throw new OpRejected(`node ${w.id}: parent ${parentKey} does not exist`);
      t ??= cloneTree(t0);
      // `children: []` is CONTRACT DEFENSE, not a convenience: the wire shape
      // is TreeNode minus children (an op can only APPEND), and a payload that
      // smuggles a child list in — a buggy broadcast once shipped stored nodes
      // whose children the mirror hadn't applied yet — must be ignored, or the
      // mirror adopts edges pointing at nodes it doesn't have.
      t.nodes[w.id] = { ...w, parent: w.parent ?? null, children: [] };
      childArray(t, parentKey).push(w.id);
      if (op.select && !claimed.has(parentKey)) {
        claimed.add(parentKey);
        t.selected[parentKey] = w.id;
      }
    }
    return t ?? t0;
  }
  if (op.op === 'select') {
    const kids = op.parent_key === ROOT ? t0.rootChildren : t0.nodes[op.parent_key]?.children;
    if (!kids || !kids.includes(op.child_id)) return t0; // stale LWW write — harmless
    if (t0.selected[op.parent_key] === op.child_id) return t0;
    const t = cloneTree(t0);
    t.selected[op.parent_key] = op.child_id;
    return t;
  }
  if (op.op === 'delete') return deleteSubtree(t0, op.node_id); // missing id → same-ref no-op inside
  // Wire data is untyped: an op kind this build doesn't know must THROW so the
  // echo path treats it as desync and refetches — a silent no-op here is
  // permanent divergence with contiguous revs. Parity: tree_ops.py's
  // `unknown op` OpError.
  throw new OpRejected(`unknown op ${(op as { op?: string }).op}`);
}

/** The map-level half of the vocabulary: tree-level ops stamped with their
 *  panel, plus the two whole-tree ops. set_meta is store-side (it touches
 *  workspace fields, not trees). Shared by the live mirror's echo application,
 *  the static transport's overlay writes, and the fixture-vector runner — one
 *  interpreter, so they can't drift. */
export type PanelOp =
  | (TreeOp & { panel: string })
  | { op: 'copy_tree'; from_panel: string; to_panel: string }
  | { op: 'replace_tree'; panel: string; tree: ConvTree | null };

/** Key-order-insensitive value equality (an echo's tree round-tripped through
 *  JSON+python, so key order may differ from the locally-built twin). */
function treeEq(a: unknown, b: unknown): boolean {
  const canon = (x: unknown): string => {
    if (Array.isArray(x)) return '[' + x.map(canon).join(',') + ']';
    if (x && typeof x === 'object')
      return (
        '{' +
        Object.keys(x)
          .sort()
          .filter((k) => (x as Record<string, unknown>)[k] !== undefined)
          .map((k) => JSON.stringify(k) + ':' + canon((x as Record<string, unknown>)[k]))
          .join(',') +
        '}'
      );
    return JSON.stringify(x);
  };
  return canon(a) === canon(b);
}

/** Apply one panel-level op to a trees map. Same-ref return on no-ops — which
 *  INCLUDES a value-equal replace/copy: an own echo replays what the mirror
 *  already applied optimistically, and handing back a new ref for identical
 *  content re-renders the whole column under the user's cursor (the ops-era
 *  regression browser_undo caught: the restore's echo yanked the toolbar
 *  mid-click). */
export function applyPanelOp(
  trees: Record<string, ConvTree>,
  op: PanelOp
): Record<string, ConvTree> {
  if (op.op === 'copy_tree') {
    const src = trees[op.from_panel];
    if (!src) throw new OpRejected(`copy_tree: unknown source panel ${op.from_panel}`);
    if (treeEq(trees[op.to_panel], src)) return trees;
    return { ...trees, [op.to_panel]: structuredClone(src) };
  }
  if (op.op === 'replace_tree') {
    if (op.tree === null) {
      if (!(op.panel in trees)) return trees;
      const next = { ...trees };
      delete next[op.panel];
      return next;
    }
    // A client-supplied wholesale tree is structurally validated before it
    // lands (mirrors tree_ops.validate_tree — itself a port of assertValid, so
    // the two sides agree on the message down to the vector substrings). Node
    // ids must also be filename-safe — they become blob filenames server-side,
    // and an unsafe id that only trips at blob-write time persists as a node
    // whose blobs can never be read back.
    for (const nid of Object.keys(op.tree.nodes ?? {})) {
      if (!SAFE_NODE_ID.test(nid))
        throw new OpRejected(`replace_tree: unsafe node id ${JSON.stringify(nid)}`);
    }
    try {
      assertValid(op.tree);
    } catch (e) {
      throw new OpRejected(`replace_tree: ${(e as Error).message}`);
    }
    if (treeEq(trees[op.panel], op.tree)) return trees;
    return { ...trees, [op.panel]: op.tree };
  }
  const cur = trees[op.panel] ?? emptyTree();
  const next = applyTreeOp(cur, op);
  return next === cur ? trees : { ...trees, [op.panel]: next };
}

// ── post-fold lightening (storage v2; was save-plan.ts) ─────────────
// Fresh folds carry token_logprobs/raw_meta INLINE; the add_nodes op ships them
// once for the server to blob, and the tree keeps LIGHT nodes from birth (the
// blob cache serves the token view). Mirrors the server's strip predicate
// (Python truthiness): empty list / empty string ⇒ no blob ⇒ no has_* flag.

/** Ids of nodes whose heavy fields would produce a server blob. */
export function heavyNodeIds(tree: ConvTree): Set<string> {
  const ids = new Set<string>();
  for (const [id, n] of Object.entries(tree.nodes)) {
    if ((n.token_logprobs?.length ?? 0) > 0 || (n.raw_meta ?? '') !== '' || (n.items?.length ?? 0) > 0) ids.add(id);
  }
  return ids;
}

/** Strip the heavy fields of `shipped` node ids from `current`, setting the
 *  matching has_* flags. Returns null when no node changed. */
export function lightenTree(current: ConvTree, shipped: Set<string>): ConvTree | null {
  let changed = false;
  const nodes: Record<string, TreeNode> = {};
  for (const [id, n] of Object.entries(current.nodes)) {
    const lp = shipped.has(id) && (n.token_logprobs?.length ?? 0) > 0;
    const rm = shipped.has(id) && (n.raw_meta ?? '') !== '';
    const it = shipped.has(id) && (n.items?.length ?? 0) > 0;
    if (!lp && !rm && !it) {
      nodes[id] = n;
      continue;
    }
    changed = true;
    const light = { ...n };
    if (lp) {
      delete light.token_logprobs;
      light.has_token_logprobs = true;
    }
    if (rm) {
      delete light.raw_meta;
      light.has_raw_meta = true;
    }
    if (it) {
      delete light.items;
      light.has_items = true;
    }
    nodes[id] = light;
  }
  return changed ? { ...current, nodes } : null;
}

// ── reconciliation ───────────────────────────────────────────────────
/** Fold an EXTERNAL (CLI / other-tab / on-load) transcript into the tree.
 *
 *  `msgs` is the backend's CUMULATIVE active path (chat.py commits [*history,
 *  assistant] each turn), NOT a standalone turn — so we walk it against the tree:
 *   - while it matches existing nodes (by role+content), follow + SELECT them
 *     (re-selects a matching non-active sibling so the view reflects what the CLI
 *     ran; idempotent when it's already the active path);
 *   - at the first divergence, append the remaining `msgs` as a fresh chain —
 *     EXTENDING the matched branch in place if we got partway (a continued CLI
 *     thread), or as a NEW ROOT if nothing matched (a divergent reset).
 *  Existing branches are always preserved. Returns the SAME ref (no-op) when
 *  nothing changed, so the caller can cheaply detect a real external change.
 *
 *  `threadSystem` is the transcript's thread system prompt (from the bus event /
 *  panel mirror): at the ROOT level a candidate must carry the SAME one (two
 *  probes sharing a first message but not a system prompt are distinct threads —
 *  matching by content alone would fold the transcript under the wrong probe).
 *  A new root minted by the append phase is stamped with it. `undefined` =
 *  unknown provenance (legacy caller/event) → match by content alone, stamp
 *  nothing — today's behavior. */
export function reconcileExternal(
  t0: ConvTree,
  msgs: Msg[],
  threadSystem?: string | null
): { tree: ConvTree; ops: TreeOp[] } {
  if (!msgs || msgs.length === 0) return { tree: t0, ops: [] };
  const t = cloneTree(t0);
  const ops: TreeOp[] = [];
  const added: OpNode[] = [];
  let changed = false;
  let parentKey = ROOT;
  let i = 0;
  // 1. follow + select the longest existing prefix of msgs.
  for (; i < msgs.length; i++) {
    const m = msgs[i];
    const childIds = parentKey === ROOT ? t.rootChildren : t.nodes[parentKey].children;
    const cid = childIds.find((c) => {
      const n = t.nodes[c];
      if (!n || n.role !== m.role || n.content !== m.content) return false;
      if (parentKey === ROOT && threadSystem !== undefined)
        return (n.system_prompt ?? '') === (threadSystem ?? '');
      return true;
    });
    if (!cid) break;
    if (t.selected[parentKey] !== cid) {
      t.selected[parentKey] = cid;
      ops.push({ op: 'select', parent_key: parentKey, child_id: cid });
      changed = true;
    }
    parentKey = cid;
  }
  // 2. append the unmatched tail (extend the branch, or new root if i===0).
  for (; i < msgs.length; i++) {
    const m = msgs[i];
    const id = nid();
    t.nodes[id] = {
      id,
      role: m.role,
      content: m.content,
      // preserve thinking carried on an external/echoed turn so a CLI/cross-tab
      // reply round-trips its CoT into the tree (not just answer-only)
      ...(m.reasoning ? { reasoning: m.reasoning } : {}),
      ...(m.reasoning_details?.length ? { reasoning_details: m.reasoning_details } : {}),
      ...(m.tool_calls?.length ? { tool_calls: m.tool_calls } : {}),
      ...(m.role === 'tool' ? { tool_call_id: m.tool_call_id, ...(m.name ? { name: m.name } : {}) } : {}),
      // a fresh ROOT carries the transcript's thread system prompt (provenance —
      // the whole point of the field for CLI-fired probe threads)
      ...(parentKey === ROOT && threadSystem ? { system_prompt: threadSystem } : {}),
      parent: parentKey === ROOT ? null : parentKey,
      children: []
    };
    if (parentKey === ROOT) t.rootChildren.push(id);
    else t.nodes[parentKey].children.push(id);
    t.selected[parentKey] = id;
    added.push(opNode(t, id));
    parentKey = id;
    changed = true;
  }
  // The appended chain persists as one add (select: true reproduces the per-step
  // selection writes — chain semantics of the first-writer-per-parent rule).
  if (added.length) ops.push({ op: 'add_nodes', nodes: added, select: true });
  return changed ? { tree: t, ops } : { tree: t0, ops: [] };
}

export function treeFromMessages(msgs: Msg[], threadSystem?: string | null): ConvTree {
  return reconcileExternal(emptyTree(), msgs, threadSystem).tree;
}

// ── validation (used by tests + the on-load tree validator) ──────────
/** Throws on any structural corruption — used as the load-time validator + the
 *  test oracle. Checks BOTH directions (forward: listed children exist & point
 *  back; reverse: every node's parent resolves & lists it), child uniqueness,
 *  reachability from the roots, and selected-key liveness. */
export function assertValid(t: ConvTree): void {
  const noDup = (arr: string[], where: string) => {
    if (new Set(arr).size !== arr.length) throw new Error(`${where} has duplicate child ids`);
  };
  noDup(t.rootChildren, 'rootChildren');
  for (const id of Object.keys(t.nodes)) {
    if (id === ROOT) throw new Error(`node id collides with ROOT sentinel: ${id}`);
    const n = t.nodes[id];
    if (n.id !== id) throw new Error(`node ${id} has mismatched .id ${n.id}`);
    noDup(n.children, `node ${id} children`);
    // forward: each listed child exists and points back here.
    for (const c of n.children) {
      if (!t.nodes[c]) throw new Error(`node ${id} child ${c} missing`);
      if ((t.nodes[c].parent ?? ROOT) !== id)
        throw new Error(`child ${c} parent pointer != ${id}`);
    }
    // reverse: this node's parent resolves and lists it.
    if (n.parent === null) {
      if (!t.rootChildren.includes(id)) throw new Error(`root node ${id} not in rootChildren`);
    } else {
      const p = t.nodes[n.parent];
      if (!p) throw new Error(`node ${id} parent ${n.parent} missing`);
      if (!p.children.includes(id)) throw new Error(`node ${id} not listed in parent ${n.parent}`);
    }
  }
  for (const c of t.rootChildren) {
    if (!t.nodes[c]) throw new Error(`rootChild ${c} missing`);
    if (t.nodes[c].parent !== null) throw new Error(`rootChild ${c} has non-null parent`);
  }
  // reachability: every node is reachable from the roots (no island subtrees).
  const seen = new Set<string>();
  const stack = [...t.rootChildren];
  while (stack.length) {
    const id = stack.pop()!;
    if (seen.has(id)) continue;
    seen.add(id);
    stack.push(...(t.nodes[id]?.children ?? []));
  }
  if (seen.size !== Object.keys(t.nodes).length)
    throw new Error(`unreachable nodes: ${Object.keys(t.nodes).filter((id) => !seen.has(id))}`);
  for (const [parentKey, childId] of Object.entries(t.selected)) {
    const kids = parentKey === ROOT ? t.rootChildren : t.nodes[parentKey]?.children;
    if (!kids) throw new Error(`selected key ${parentKey} is not a live node/ROOT`);
    if (!kids.includes(childId))
      throw new Error(`selected[${parentKey}]=${childId} is not a child of it`);
  }
}
