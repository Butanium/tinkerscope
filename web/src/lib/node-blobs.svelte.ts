// Per-node heavy-blob cache (storage v2). Tree nodes are LIGHT everywhere now —
// token_logprobs / raw_meta live server-side as write-once per-node blobs, and a
// light node only carries has_* presence flags. Consumers that need the payload
// (the token inspector, the raw-meta disclosure, the first-token chart,
// token-search) read THIS cache and call ensure() to batch-fetch what's missing.
//
// Population, three ways:
//   - seed() at FOLD time: a fresh sample already has the data in hand, so the
//     turn's inspector works instantly, before (and independent of) the save.
//   - ensure() lazily: batch POST /api/conversations/{id}/node-blobs for nodes
//     whose has_* flag is set but whose blob isn't cached (old turns, foreign
//     folds). Unknown ids come back omitted → cached as {} so they don't refetch.
//   - reset() on every conversation transition: the cache is scoped to ONE open
//     conversation (node ids are only unique within it), so switch/create/load
//     clear it and rebind the conversation id ensure() fetches against.
//
// Ownership note: this store does NOT import the conversations store (which
// imports it for reset-on-switch) — the conversation id is pushed in via reset().

import { api } from './api';
import type { NodeBlobs } from './types';

class NodeBlobStore {
	/** node id → its blobs; {} = known blob-less (fetched, nothing there). */
	#cache = $state<Record<string, NodeBlobs>>({});
	/** The conversation the cache belongs to (ensure() fetches against it). */
	#convId: string | null = null;
	/** Node ids with an in-flight fetch — never double-fetched. */
	#inflight = new Set<string>();

	/** Rebind to a (possibly different) conversation and drop everything cached.
	 *  Call on EVERY conversation transition, before the new trees land. */
	reset(convId: string | null): void {
		this.#convId = convId;
		this.#cache = {};
		this.#inflight.clear();
	}

	/** The cached blobs for a node (reactive), or undefined while unknown/unfetched. */
	get(nodeId: string): NodeBlobs | undefined {
		return this.#cache[nodeId];
	}

	/** Locally install a fresh node's blobs (fold time — no fetch ever needed). */
	seed(nodeId: string, blobs: NodeBlobs): void {
		if (blobs.token_logprobs == null && blobs.raw_meta == null) return;
		this.#cache[nodeId] = blobs;
	}

	/** Batch-fetch blobs for any of `nodeIds` not yet cached or in flight.
	 *  Fire-and-forget for UI callers (reactivity delivers the result); returns
	 *  the fetch promise so tests/smokes can await it. */
	ensure(nodeIds: string[]): Promise<void> {
		const convId = this.#convId;
		if (!convId) return Promise.resolve();
		const missing = nodeIds.filter((id) => !(id in this.#cache) && !this.#inflight.has(id));
		if (!missing.length) return Promise.resolve();
		for (const id of missing) this.#inflight.add(id);
		return api
			.fetchNodeBlobs(convId, missing)
			.then((res) => {
				if (this.#convId !== convId) return; // conversation switched mid-fetch — stale
				for (const id of missing) {
					this.#inflight.delete(id);
					// Omitted id ⇒ the server has no blob for it — cache {} so the
					// consumer's has_* affordance can settle and we never refetch.
					this.#cache[id] = res[id] ?? {};
				}
			})
			.catch((e) => {
				// Transient failure: un-mark so a later ensure() retries.
				for (const id of missing) this.#inflight.delete(id);
				console.warn('node-blobs fetch failed', e);
			});
	}
}

export const nodeBlobs = new NodeBlobStore();
