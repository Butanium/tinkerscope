// Panel render model: fold a panel's committed branch tree together with its
// live streamed bucket into the flat ViewMessage[] a column renders.
//
// A column renders its tree's active path. The per-panel BUCKET (the in-flight /
// just-finished turn's N samples) is overlaid on the active leaf's trailing
// assistant turn — replacing it, never double-rendering — so the live
// distribution view and the committed reply are the same row. After a fold the
// bucket's n>1 cards are mapped back to their sibling node ids so a card click
// can select that branch.
//
// PURE: inputs in, ViewMessage[] out (no store/DOM reads) — the component passes
// the tree, the bucket, and the panel's last-fire prefill. Unit-tested.

import { activePath, siblingInfo, type ConvTree } from './tree.ts';
import type { PanelRun } from './state.svelte.ts';
import type { NodeBlobs, ViewMessage } from './types.ts';

/** The bucket's latest turn as one trailing assistant ViewMessage. `prefill` (the
 *  panel's last fire) lets the live view color the prefilled prefix. */
export function bucketTurn(run: PanelRun, prefill?: string): ViewMessage {
  const filled = run.samples.filter((x) => x);
  const pf = prefill || undefined;
  if (run.n > 1) {
    return {
      role: 'assistant',
      content: filled[0]?.content ?? '',
      reasoning: filled[0]?.reasoning,
      raw_text: filled[0]?.raw_text,
      raw_meta: filled[0]?.raw_meta,
      prefill: pf,
      samples: run.samples,
      totalSamples: run.n,
      running: run.running
    };
  }
  const one = filled[0];
  return {
    role: 'assistant',
    content: one?.content ?? '',
    reasoning: one?.reasoning,
    raw_text: one?.raw_text,
    raw_meta: one?.raw_meta,
    prefill: pf,
    finish_reason: one?.finish_reason,
    token_logprobs: one?.token_logprobs,
    running: run.running
  };
}

/** The active path as ViewMessages, with the live bucket overlaid on the trailing
 *  assistant leaf (if any). `run` is the panel's bucket (pass emptyPanel() when
 *  none); `prefill` is its last-fire prefill. */
export function buildPanelView(tree: ConvTree, run: PanelRun, prefill?: string): ViewMessage[] {
  const path = activePath(tree);
  const out: ViewMessage[] = path.map((n) => ({
    role: n.role,
    content: n.content,
    reasoning: n.reasoning,
    raw_text: n.raw_text,
    raw_meta: n.raw_meta,
    prefill: n.prefill,
    finish_reason: n.finish_reason,
    thinking: n.thinking,
    token_logprobs: n.token_logprobs,
    // Light-node blob flags (storage v2): the heavy fields above may be absent
    // with the data living server-side — consumers resolve via lib/node-blobs.
    has_token_logprobs: n.has_token_logprobs,
    has_raw_meta: n.has_raw_meta,
    system_prompt: n.system_prompt,
    isRoot: n.parent === null,
    nodeId: n.id,
    sib: siblingInfo(tree, n.id),
    isBucket: false
  }));
  const hasBucket = run.chat_id != null || run.samples.length > 0 || run.running;

  if (hasBucket) {
    let replacedId: string | null = null;
    let replacedSib: { index: number; count: number } | undefined;
    let sampleNodeIds: string[] | undefined;
    let activeSampleIndex: number | undefined;
    if (out.length > 0 && out[out.length - 1].role === 'assistant') {
      // Folded already → replace the trailing assistant with the rich bucket view,
      // and map the n>1 cards back to this batch's sibling node ids.
      const last = out[out.length - 1];
      replacedId = last.nodeId ?? null;
      replacedSib = last.sib;
      out.pop();
      const userParent = replacedId ? tree.nodes[replacedId]?.parent : null;
      if (userParent && tree.nodes[userParent]) {
        const kids = tree.nodes[userParent].children;
        // A sample is "folded" iff it has content AND no error — matching
        // foldAssistant's skip rule (error samples carry an "Error: …" content
        // string, so gating on content alone would miscount). Error slots map to ''.
        const isFold = (x: (typeof run.samples)[number]) => !!(x && x.content && !x.error);
        const filledCount = run.samples.filter(isFold).length;
        const batch = kids.slice(Math.max(0, kids.length - filledCount)); // this turn's folds
        sampleNodeIds = [];
        let pos = 0;
        for (let i = 0; i < run.samples.length; i++) {
          sampleNodeIds[i] = isFold(run.samples[i]) ? (batch[pos++] ?? '') : '';
        }
        if (replacedId) activeSampleIndex = sampleNodeIds.indexOf(replacedId);
      }
    }
    out.push({
      ...bucketTurn(run, prefill),
      nodeId: replacedId,
      sib: replacedSib,
      sampleNodeIds,
      activeSampleIndex,
      isBucket: true
    });
  }
  if (run.error === 'cancelled') {
    // The deliberate-stop terminal (chat_error("cancelled"), 0 samples — only
    // _terminal mints this bare string; producer faults are "Type: msg") is a
    // user action, not a failure — render a neutral strip, not an error row.
    out.push({ role: 'assistant', content: '', notice: 'stopped', nodeId: null });
  } else if (run.error) {
    out.push({ role: 'assistant', content: `Error: ${run.error}`, nodeId: null });
  }
  return out;
}

/** Storage-v2 blob resolver: heavy fields of a light node live server-side; the
 *  caller passes the reactive cache's getter (`nodeBlobs.get`) so expanded cards
 *  fill in as blobs arrive. Tests pass a stub (or nothing). */
export type BlobLookup = (id: string) => NodeBlobs | undefined;

/** "View all samples" (the row-toolbar eye): replace the active-path row whose
 *  tree parent is `parentId` with a multi-sample view of ALL its assistant
 *  siblings — the same card UI the n>1 bucket renders — and DROP the rows after
 *  it (children are hidden while the distribution is open; `samplesExpanded.
 *  hiddenBelow` tells the row how many, for the exit strip).
 *
 *  Deliberate no-ops (return `view` unchanged, the caller keeps its state):
 *  - the turn isn't on the active path / parent gone (stale id is harmless);
 *  - the matching row is the live BUCKET — the bucket already IS the all-samples
 *    view of the in-flight batch, and hiding a stream behind stale tree nodes
 *    would lie about what's running;
 *  - fewer than 2 siblings survive (e.g. deletes while open) — a 1-sample
 *    "distribution" renders as the normal row instead. */
export function expandTurnSamples(
  view: ViewMessage[],
  tree: ConvTree,
  parentId: string | null | undefined,
  getBlob?: BlobLookup
): ViewMessage[] {
  if (!parentId || !tree.nodes[parentId]) return view;
  const idx = view.findIndex(
    (m) => m.role === 'assistant' && !m.isBucket && m.nodeId != null && tree.nodes[m.nodeId]?.parent === parentId
  );
  if (idx < 0) return view;
  // Same keep-rule as the chart (buildChartSources): a sample whose whole budget
  // went to CoT (content '' but reasoning present) still counts.
  const sibs = tree.nodes[parentId].children
    .map((id) => tree.nodes[id])
    .filter((n) => n && n.role === 'assistant' && (n.content || n.reasoning));
  if (sibs.length < 2) return view;
  const row = view[idx];
  // The card view colors ONE msg-level prefill across all cards; siblings from
  // different fires can disagree — only a value every sibling shares is honest.
  const prefill = sibs.every((n) => n.prefill === sibs[0].prefill) ? sibs[0].prefill : undefined;
  const expanded: ViewMessage = {
    ...row,
    prefill,
    samples: sibs.map((n) => ({
      content: n.content,
      reasoning: n.reasoning,
      raw_text: n.raw_text,
      raw_meta: n.raw_meta ?? getBlob?.(n.id)?.raw_meta,
      finish_reason: n.finish_reason,
      thinking: n.thinking,
      token_logprobs: n.token_logprobs ?? getBlob?.(n.id)?.token_logprobs
    })),
    totalSamples: sibs.length,
    sampleNodeIds: sibs.map((n) => n.id),
    activeSampleIndex: sibs.findIndex((n) => n.id === row.nodeId),
    running: false,
    samplesExpanded: { parent: parentId, hiddenBelow: view.length - idx - 1 }
  };
  return [...view.slice(0, idx), expanded];
}
