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
import type { ViewMessage } from './types.ts';

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
	if (run.error) out.push({ role: 'assistant', content: `Error: ${run.error}`, nodeId: null });
	return out;
}
