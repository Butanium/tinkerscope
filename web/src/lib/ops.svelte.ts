// Op-batch emission for the server-authority protocol (HANDOFF_SERVER_AUTHORITY
// §4.2). One serialized chain: batches must reach the server in the order they
// were applied locally, and a draft's materializing CREATE must precede its
// first batch. Callers fire-and-forget; failure recovery is refetch-shaped and
// injected by the workspace store (this module must not import it — it is
// imported BY it).
//
// Failure semantics (the one genuine behavior gap the review found vs the old
// save path): a transport failure / 5xx means the batch may never have arrived
// — retry the SAME batch, bounded; idempotent replay makes a half-applied batch
// harmless. A 4xx means the batch was REJECTED (deleted parent, id conflict) —
// never retry, refetch truth instead. Anything else is a code bug: no retry
// loop, straight to the desync path, which is always safe.

import { api, ApiError } from './api';
import type { WorkspaceOp } from './types';

export type OpsSeams = {
  /** Materialize an unsaved draft before its first batch (POST create with the
   *  current trees — the batch then replays idempotently over it). Must throw on
   *  failure so the batch takes the desync path instead of 404ing. */
  ensureMaterialized: (id: string) => Promise<void>;
  /** A batch was rejected or retries ran dry: the mirror may have diverged from
   *  the store — refetch the light body. `why` is for the console, not the user. */
  onDesync: (id: string, why: string) => void;
  /** Surface a user-visible notice (retries exhausted — edits at risk). */
  notice: (msg: string) => void;
};

const RETRIES = 3;
const BACKOFF_MS = 600; // ×2 per attempt: 600, 1200, 2400

function retriable(e: unknown): boolean {
  if (e instanceof ApiError) return e.status >= 500;
  // fetch() rejects with TypeError on network failure — the "maybe never
  // arrived" case idempotent replay exists for.
  return e instanceof TypeError;
}

class OpsEmitter {
  #seams: OpsSeams | null = null;
  #chain: Promise<void> = Promise.resolve();
  /** Batches accepted but not yet settled — flush() awaits them. */
  #pending = $state(0);

  configure(seams: OpsSeams): void {
    this.#seams = seams;
  }

  get pending(): number {
    return this.#pending;
  }

  /** Queue one batch. Ordering is global (not per-workspace): cheap, and it
   *  keeps a cross-workspace flush ("switch away") trivially correct. */
  emit(id: string, ops: WorkspaceOp[]): void {
    if (!ops.length) return;
    if (!this.#seams) throw new Error('opsEmitter used before configure()');
    this.#pending++;
    this.#chain = this.#chain
      .then(() => this.#send(id, ops))
      .finally(() => {
        this.#pending--;
      });
  }

  /** Settles when every batch queued SO FAR has been sent (or given up on). */
  flush(): Promise<void> {
    return this.#chain;
  }

  async #send(id: string, ops: WorkspaceOp[]): Promise<void> {
    const s = this.#seams!;
    // The materializing CREATE gets the same bounded-retry semantics as the
    // batches behind it — it IS the first write of the chain, and a single
    // transport blip here used to drop the whole batch silently (review
    // finding: the create sat outside the retry loop). Idempotent: create
    // upserts by the draft's id, so a landed-but-unacked create replays free.
    for (let attempt = 0; ; attempt++) {
      try {
        await s.ensureMaterialized(id);
        break;
      } catch (e) {
        if (!retriable(e)) {
          s.onDesync(id, `draft create rejected: ${(e as Error)?.message ?? e}`);
          return;
        }
        if (attempt >= RETRIES - 1) {
          s.notice('Server unreachable — the new workspace and its edits are not saved yet.');
          s.onDesync(id, `draft create retries exhausted: ${(e as Error)?.message ?? e}`);
          return;
        }
        await new Promise((r) => setTimeout(r, BACKOFF_MS * 2 ** attempt));
      }
    }
    for (let attempt = 0; ; attempt++) {
      try {
        await api.applyOps(id, ops);
        return;
      } catch (e) {
        if (!retriable(e)) {
          s.onDesync(id, `ops rejected: ${(e as Error)?.message ?? e}`);
          return;
        }
        if (attempt >= RETRIES - 1) {
          s.notice('Server unreachable — recent edits may not be saved.');
          s.onDesync(id, `ops retries exhausted: ${(e as Error)?.message ?? e}`);
          return;
        }
        await new Promise((r) => setTimeout(r, BACKOFF_MS * 2 ** attempt));
      }
    }
  }
}

export const opsEmitter = new OpsEmitter();
