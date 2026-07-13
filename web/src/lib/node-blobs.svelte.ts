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
	// Micro-batching: each mounted row ensure()s its own node, so flipping the
	// token-probs toggle on a long conversation fires one call PER ROW in the
	// same tick. A short collection window folds that burst into one POST.
	#queue = new Set<string>();
	#flushTimer: ReturnType<typeof setTimeout> | null = null;
	#flushPromise: Promise<void> | null = null;
	#resolveFlush: (() => void) | null = null;

	/** Rebind to a (possibly different) conversation and drop everything cached.
	 *  Call on EVERY conversation transition, before the new trees land. */
	reset(convId: string | null): void {
		this.#convId = convId;
		this.#cache = {};
		this.#inflight.clear();
		this.#queue.clear();
		if (this.#flushTimer) {
			clearTimeout(this.#flushTimer);
			this.#flushTimer = null;
		}
		this.#resolveFlush?.(); // settle any awaiting caller — its ids are moot now
		this.#resolveFlush = null;
		this.#flushPromise = null;
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

	/** Queue blobs of any of `nodeIds` not yet cached/in flight for a batched
	 *  fetch (20 ms collection window → one POST). Fire-and-forget for UI callers
	 *  (reactivity delivers the result); returns a promise that settles when the
	 *  batch containing these ids lands, so tests/smokes can await it. */
	ensure(nodeIds: string[]): Promise<void> {
		if (!this.#convId) return Promise.resolve();
		for (const id of nodeIds) {
			if (!(id in this.#cache) && !this.#inflight.has(id)) this.#queue.add(id);
		}
		if (!this.#queue.size) return Promise.resolve();
		if (!this.#flushPromise) {
			this.#flushPromise = new Promise((res) => (this.#resolveFlush = res));
			this.#flushTimer = setTimeout(() => this.#flush(), 20);
		}
		return this.#flushPromise;
	}

	#flush(): void {
		this.#flushTimer = null;
		const convId = this.#convId;
		const ids = [...this.#queue];
		this.#queue.clear();
		const settle = this.#resolveFlush!;
		this.#resolveFlush = null;
		this.#flushPromise = null;
		if (!convId || !ids.length) {
			settle();
			return;
		}
		for (const id of ids) this.#inflight.add(id);
		api
			.fetchNodeBlobs(convId, ids)
			.then((res) => {
				for (const id of ids) this.#inflight.delete(id);
				if (this.#convId !== convId) return; // conversation switched mid-fetch — stale
				for (const id of ids) {
					// Omitted id ⇒ the server has no blob for it — cache {} so the
					// consumer's has_* affordance can settle and we never refetch.
					this.#cache[id] = res[id] ?? {};
				}
			})
			.catch((e) => {
				// Transient failure: un-mark so a later ensure() retries.
				for (const id of ids) this.#inflight.delete(id);
				console.warn('node-blobs fetch failed', e);
			})
			.finally(settle);
	}
}

export const nodeBlobs = new NodeBlobStore();
