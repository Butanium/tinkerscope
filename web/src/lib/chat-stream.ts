// Drain the /api/chat SSE response into completed samples.
//
// We parse OUR OWN chat stream here (rather than reading the shared render
// bucket in state.svelte) so that a CLI / other-tab chat landing on the same
// panel mid-stream — which clobbers the single-slot render bucket — can never
// drop or corrupt the fold we commit to the tree. The caller folds the returned
// samples under the user node it fired from.
//
// Frame format: standard SSE — `event:`/`data:` lines, frames split on a blank
// line (\n\n). Only `message` frames carrying a string `content` (or an `error`)
// become samples; partial/non-JSON frames are skipped.

import type { SampleLike } from './tree';

export async function drainSamples(res: Response): Promise<SampleLike[]> {
	const samples: SampleLike[] = [];
	const reader = res.body!.getReader();
	const decoder = new TextDecoder();
	let buf = '';
	while (true) {
		const { done, value } = await reader.read();
		if (done) break;
		buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
		let nl: number;
		while ((nl = buf.indexOf('\n\n')) >= 0) {
			const frame = buf.slice(0, nl);
			buf = buf.slice(nl + 2);
			let event = 'message';
			let dataStr = '';
			for (const line of frame.split('\n')) {
				if (line.startsWith('event:')) event = line.slice(6).trim();
				else if (line.startsWith('data:')) dataStr += line.slice(5).trim();
			}
			if (event !== 'message' || !dataStr) continue;
			try {
				const d = JSON.parse(dataStr);
				if (d && (typeof d.content === 'string' || d.error)) {
					samples.push({
						content: d.content,
						reasoning: d.reasoning,
						raw_text: d.raw_text,
						raw_meta: d.raw_meta,
						error: d.error,
						prefill_incorporated: d.prefill_incorporated,
						sample_index: d.sample_index ?? samples.length
					});
				}
			} catch {
				/* ignore a partial / non-JSON frame */
			}
		}
	}
	return samples;
}
