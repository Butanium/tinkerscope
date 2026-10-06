// Single source of frontend truth, fed by the /api/state/events SSE.
//
// Two concerns live here:
//   1. `live.state` — the mirrored shared PlaygroundState (selection / params /
//      workspace). Rendered directly so the browser follows when the terminal
//      (or another browser tab) POSTs /api/state. WORKSPACE-SCOPED fields are
//      filtered through `mergeBusState` first: the bus is process-global but a
//      workspace's panel layout / system prompt is its own, so a message stamped
//      with ANOTHER workspace contributes only its global params. See bus-scope.ts
//      for the failure this prevents.
//   2. `live.panels` — accumulated streamed samples per panel, keyed by chat_id.
//      'chat_start' clears the bucket + sets running; 'sample' appends;
//      'chat_done'/'chat_error' end it. The sample list + distribution chart
//      render from these, so CLI-initiated and browser-initiated chats share one
//      render path. Ephemeral broadcasts, NOT state — they carry their own
//      client_token / workspace_id and are scoped by their consumers.

import { api, sse } from './api';
import { FIRST_PANEL_ID } from './panel-id';
import { mergeBusState } from './bus-scope';
import type { PlaygroundState, SampleData, Panel, TinkerStatus } from './types';

/** Live accumulation for one compare-panel's in-flight / finished chat run. */
export type PanelRun = {
  chat_id: number | null;
  label: string; // run@checkpoint, for the chart x-axis
  n: number; // expected number of samples
  samples: SampleData[]; // sparse: indexed by sample_index
  running: boolean;
  error: string | null;
  /** Client clock at chat_start — drives the "waiting on the model" readout. */
  startedAt?: number;
  /** Latest `chat_status` — what tinker says about the requests (same readout). */
  tinker?: TinkerStatus;
};

export function emptyPanel(): PanelRun {
  return { chat_id: null, label: '', n: 0, samples: [], running: false, error: null };
}

class LiveStore {
  /** Mirrored shared server state. null until the first snapshot arrives.
   *  For workspace-scoped fields this is OUR workspace's truth, not necessarily
   *  the raw bus — see #adopt / bus-scope.ts. */
  state = $state<PlaygroundState | null>(null);
  /** The workspace this tab has open. Kept in lockstep with
   *  `workspaces.activeId` by the workspace store — the one input the bus
   *  filter needs, injected rather than imported (workspaces imports us). */
  workspaceId: string | null = null;
  /** The RAW bus ownership stamp — whose workspace the server bus currently
   *  describes. Distinct from `state.workspace_id`: mergeBusState pins the
   *  MIRROR's stamp to our own on foreign messages (the mirror is "my
   *  workspace's coherent picture"), so the mirror can never report that we
   *  LOST the bus — which silently made `ownsBus` constant-true and focus-
   *  reclaim dead code. This field tracks every incoming message's own stamp;
   *  it is what `ws.ownsBus` (and so claim-on-write / focus-reclaim) decide on. */
  busId = $state<string | null>(null);
  /** true while the SSE stream is believed alive: any event (the server
   *  heartbeats every 15s) marks it up; a socket error or 35s of silence marks
   *  it DOWN. It must DEGRADE, never latch: the topbar dot is a claim about
   *  now, not about page load. */
  connected = $state(false);
  /** true once ANY event has ever arrived — distinguishes "connecting…" from
   *  "was live, lost it" in the topbar. */
  everConnected = $state(false);
  #lastEventAt = 0;
  #watchdog: ReturnType<typeof setInterval> | null = null;
  /** Per-panel sample accumulation, driven by chat_start/sample/chat_done. Open-keyed
   *  by panel id and LAZILY vivified on chat_start (no pre-seeded slots), so any
   *  number of panels work; every read guards a missing slot with emptyPanel().
   *
   *  $state ⇒ a DEEP reactive proxy, so `this.panels[id] = …` alone is fine-grained:
   *  it invalidates only readers of THAT key (panelView(id), id's column), not every
   *  panel. Do NOT add `this.panels = { ...this.panels }` after a per-key write — that
   *  Svelte-4 coarse reassign re-renders ALL panels' views on every streamed token, which
   *  (a) is wasted work and (b) handed panel A's chat rows fresh msg objects mid-edit,
   *  cancelling an in-progress edit whenever ANY other panel produced a token. */
  panels = $state<Record<string, PanelRun>>({});

  // Lifecycle hooks (set by the page) for the branching tree's fold bookkeeping.
  // CRUCIAL: these fire on the RAW bus event, decoupled from the render bucket's
  // single-slot straggler guard — so an own chat_done is never eaten when a
  // foreign chat_start (CLI / another tab) has clobbered the panel bucket. Each
  // receives the event payload (carries chat_id, panel, client_token).
  onChatStart: ((panel: Panel, data: any) => void) | null = null;
  onChatDone: ((panel: Panel, data: any) => void) | null = null;
  onChatError: ((panel: Panel, data: any) => void) | null = null;
  /** Fires on a full `snapshot` (sent to every new subscriber → also on EventSource
   *  RECONNECT after a drop). Lets the workspace store reconcile any terminal it
   *  missed during the gap + release stale busy tokens. Not fired on incremental
   *  `patch` events. */
  onSnapshot: (() => void) | null = null;
  /** Fires when a server-sent state STAMPED WITH OUR OPEN WORKSPACE carries a
   *  panel list — the CLI (or another tab of the same workspace) drove the
   *  layout. The workspace store adopts it into its authoritative `layout`
   *  (live-drive lockstep). STRICT stamp match on purpose: unstamped states
   *  (a pre-claim boot, a session-restore seed) must NOT reach the layout —
   *  trusting them is how a restart race once persisted another workspace's
   *  panels (ENGINEERING_LOGS 2026-08-06). A CLI drive still lands here: the
   *  CLI's unstamped `panels` patch keeps the bus's owner stamp, so the
   *  resulting broadcast is stamped with the owner tab's workspace. */
  onOwnPanels: ((panels: PlaygroundState['panels']) => void) | null = null;
  /** Fires on every bus `ops` event (an accepted mutation batch, light bodies).
   *  The workspace store applies them in rev order — its filter, not ours: the
   *  event carries its own workspace stamp. */
  onOps: ((ev: unknown) => void) | null = null;
  /** Fires on a `workspace_deleted` broadcast with the deleted id. */
  onWorkspaceDeleted: ((id: string) => void) | null = null;
  /** Assigned by the workspace store: the open workspace's TRUE bus claim
   *  (layout from the store + tree echoes + system prompt). #reprime uses it
   *  instead of replaying the mirror's panels — re-priming from the mirror
   *  could re-publish a poisoned mirror as a claim. null = no workspace open
   *  (params-only reprime). */
  reprimeClaim: (() => Partial<PlaygroundState> | null) | null = null;

  #stop: (() => void) | null = null;

  /** THE single entry for assigning a server-sent state into our mirror — SSE
   *  events and the responses of our own POST /api/state alike. A response is a
   *  full bus snapshot, so a params-only patch of ours comes back carrying whoever
   *  owns the bus right now; assigning it raw is exactly the cross-workspace
   *  clobber. Everything funnels through mergeBusState instead. */
  adopt(next: PlaygroundState | null | undefined): void {
    if (!next) return;
    this.busId = next.workspace_id ?? null;
    this.state = mergeBusState(this.state, next, this.workspaceId);
    // Layout lockstep: only a state stamped as OURS may drive the store's
    // authoritative layout (see onOwnPanels for why unstamped is excluded).
    if (next.workspace_id != null && next.workspace_id === this.workspaceId)
      this.onOwnPanels?.(next.panels ?? []);
  }

  /** Open the global SSE state stream once. Idempotent. */
  start(): void {
    if (this.#stop) return;
    // onerror fires when the connection drops AND on each failed reconnect
    // attempt (EventSource retries on its own; a fresh snapshot on success
    // flips us back). The watchdog covers the quiet deaths onerror misses — a
    // half-open socket after sleep/wake never errors, it just goes silent,
    // and the server heartbeats every 15s, so 35s of silence is two missed
    // heartbeats plus slack.
    this.#stop = sse(
      '/api/state/events',
      (event, data) => this.#onEvent(event, data),
      () => (this.connected = false)
    );
    this.#lastEventAt = Date.now();
    this.#watchdog = setInterval(() => {
      if (this.connected && Date.now() - this.#lastEventAt > 35_000) this.connected = false;
    }, 5_000);
  }

  stop(): void {
    this.#stop?.();
    this.#stop = null;
    if (this.#watchdog) clearInterval(this.#watchdog);
    this.#watchdog = null;
  }

  /** Drop all panel buckets — used when switching workspaces so a stale
   *  overlay from the previous one can't bleed onto the new active path. */
  clearBuckets(): void {
    this.panels = {};
  }

  /** Drop ONE panel's bucket. Used when a panel id is re-minted (addPanel) and when
   *  a foreign-workspace chat is skipped (workspaces.#onExternalDone) so its
   *  streamed samples don't linger as a render overlay on the reused panel. */
  dropBucket(panel: Panel): void {
    if (!(panel in this.panels)) return;
    const next = { ...this.panels };
    delete next[panel];
    this.panels = next;
  }

  /** True while ANY panel has an in-flight chat (gates the external-fold reconcile,
   *  so it MUST cover every panel, not a fixed primary/compare pair). */
  get anyRunning(): boolean {
    return Object.values(this.panels).some((p) => p?.running);
  }

  /** Restore a wiped server bus: sampling params from this tab's mirror, and —
   *  when a workspace is open — the workspace's TRUE claim from the store
   *  (`reprimeClaim`: layout + echoes + system prompt). Never `prev.panels`:
   *  the mirror is a bus follower, and replaying it as a claim would launder a
   *  transiently-foreign mirror into "this is workspace X's layout". */
  #reprime(prev: PlaygroundState): void {
    const claim = this.reprimeClaim?.() ?? null;
    api
      .setState({
        ...(claim ?? {}),
        temperature: prev.temperature,
        max_tokens: prev.max_tokens,
        n_samples: prev.n_samples,
        thinking: prev.thinking,
        top_p: prev.top_p
      })
      .then((merged) => this.adopt(merged))
      .catch((e) => console.warn('bus re-prime failed', e));
  }

  #onEvent(event: string, data: any): void {
    this.connected = true;
    this.everConnected = true;
    this.#lastEventAt = Date.now();
    switch (event) {
      case 'snapshot': {
        const next = (data?.state ?? null) as PlaygroundState | null;
        if (next) {
          const prev = this.state;
          // Amnesiac snapshot = the server's in-memory bus was wiped (backend
          // restart) while this tab still holds the richer picture. Assigning it
          // would collapse the UI to the default single panel — and a save landing
          // in that window would PERSIST the wrong layout. Keep our mirror and
          // re-prime the server instead, so `tinkpg state` isn't blind until the
          // next workspace switch. Skip while the fresh server is mid-chat
          // (a CLI drive owns the bus then; last-writer-wins as usual).
          const wiped =
            prev != null &&
            next.workspace_id == null &&
            (prev.workspace_id != null || next.chat_id < prev.chat_id);
          if (wiped) this.busId = next.workspace_id ?? null; // adopt() is skipped — track the (un)claim anyway
          if (wiped && !next.running) this.#reprime(prev);
          else if (!wiped) this.adopt(next);
        }
        this.onSnapshot?.(); // reconnect reconcile (a fresh subscriber always gets a snapshot)
        break;
      }
      case 'patch':
        this.adopt(data?.state as PlaygroundState | undefined);
        break;
      case 'ops':
        this.onOps?.(data);
        break;
      case 'workspace_deleted':
        if (typeof data?.workspace === 'string') this.onWorkspaceDeleted?.(data.workspace);
        break;
      case 'chat_start': {
        const panel = (data?.panel ?? FIRST_PANEL_ID) as Panel;
        this.panels[panel] = {
          chat_id: data.chat_id ?? null,
          label: data.label ?? '',
          n: data.n ?? 0,
          samples: [],
          running: true,
          error: null,
          startedAt: Date.now()
        };
        this.onChatStart?.(panel, data);
        break;
      }
      case 'chat_status': {
        const panel = (data?.panel ?? FIRST_PANEL_ID) as Panel;
        const cur = this.panels[panel];
        if (!cur || (cur.chat_id != null && data.chat_id != null && data.chat_id !== cur.chat_id)) break;
        const ago = typeof data.heard_ago_s === 'number' ? data.heard_ago_s : null;
        this.panels[panel] = {
          ...cur,
          tinker: {
            state: data.state ?? null,
            reason: data.reason ?? null,
            // A reconnect resets the server's record; keep our last answer time.
            heardAt: ago == null ? (cur.tinker?.heardAt ?? null) : Date.now() - ago * 1000,
            http: ago == null ? (cur.tinker?.http ?? null) : (data.http ?? null),
            reconnects: data.reconnects ?? 0,
            resubmits: data.resubmits ?? 0
          }
        };
        break;
      }
      case 'delta': {
        // Token-streaming chunk (n==1 only). Accumulate into the sample slot
        // at sample_index so the panel fills token-by-token; the later
        // 'sample' event then finalizes the slot (parseSample replaces this
        // partial with the cleaned authoritative content).
        const panel = (data?.panel ?? FIRST_PANEL_ID) as Panel;
        const cur = this.panels[panel] ?? emptyPanel();
        // Ignore stragglers from an older chat run.
        if (cur.chat_id != null && data.chat_id != null && data.chat_id !== cur.chat_id) break;
        const idx = data.sample_index ?? 0;
        const text: string = data.delta ?? '';
        const samples = cur.samples.slice();
        const prev = samples[idx] ?? { content: '' };
        samples[idx] =
          data.kind === 'reasoning'
            ? { ...prev, reasoning: (prev.reasoning ?? '') + text }
            : { ...prev, content: (prev.content ?? '') + text };
        this.panels[panel] = { ...cur, samples };
        break;
      }
      case 'sample': {
        const panel = (data?.panel ?? FIRST_PANEL_ID) as Panel;
        const cur = this.panels[panel] ?? emptyPanel();
        // Ignore stragglers from an older chat run.
        if (cur.chat_id != null && data.chat_id != null && data.chat_id !== cur.chat_id) break;
        const samples = cur.samples.slice();
        samples[data.sample_index ?? samples.length] = parseSample(data);
        this.panels[panel] = { ...cur, samples };
        break;
      }
      case 'chat_done': {
        const panel = (data?.panel ?? FIRST_PANEL_ID) as Panel;
        // Fire the fold hook FIRST, unconditionally — it must see every
        // chat_done even if the render bucket was clobbered by a foreign
        // chat_start (the straggler guard below only protects rendering).
        this.onChatDone?.(panel, data);
        const cur = this.panels[panel] ?? emptyPanel();
        if (cur.chat_id != null && data.chat_id != null && data.chat_id !== cur.chat_id) break;
        this.panels[panel] = { ...cur, running: false };
        break;
      }
      case 'chat_error': {
        const panel = (data?.panel ?? FIRST_PANEL_ID) as Panel;
        this.onChatError?.(panel, data);
        const cur = this.panels[panel] ?? emptyPanel();
        // chat_id may be null for PRE-START failures (unknown/unsampleable
        // run, bad checkpoint) — those broadcast before any chat_start, so
        // there's no id to match; always surface them on the named panel.
        // For a non-null id, drop stragglers from an older run.
        if (data?.chat_id != null && cur.chat_id != null && data.chat_id !== cur.chat_id) break;
        this.panels[panel] = { ...cur, running: false, error: data?.error ?? 'chat failed' };
        break;
      }
      // 'ping' — heartbeat, ignore.
    }
  }
}

/**
 * Normalize a `sample`/`message` SSE payload into a SampleData.
 * `content` is normally a string (tinker), but may be an array of content blocks
 * (`[{type:"thinking"|"text", ...}]`) for OpenRouter reference models — handle both.
 */
export function parseSample(data: any): SampleData {
  if (data?.error) return { content: `Error: ${data.error}`, error: data.error };
  let content = '';
  let reasoning: string = data?.reasoning || '';
  if (typeof data?.content === 'string') {
    content = data.content;
  } else if (Array.isArray(data?.content)) {
    for (const block of data.content) {
      if (block?.type === 'text') content += (content ? '\n\n' : '') + (block.text || '');
      else if (block?.type === 'thinking')
        reasoning += (reasoning ? '\n\n' : '') + (block.thinking || '');
    }
  }
  return {
    content: content || (reasoning ? '[truncated during thinking]' : ''),
    reasoning: reasoning || undefined,
    raw_text: data?.raw_text || undefined,
    raw_meta: data?.raw_meta || undefined,
    finish_reason: data?.finish_reason || undefined,
    // per-sample renderer mode — only present on thinking='both' chats
    thinking: typeof data?.thinking === 'boolean' ? data.thinking : undefined,
    token_logprobs: Array.isArray(data?.token_logprobs) ? data.token_logprobs : undefined,
    // loom provenance (continue_tokens fires): forced-entry count + the replayed
    // prefix as display text — folded onto the node, tinted like a prefill.
    loom_cut: typeof data?.loom_cut === 'number' ? data.loom_cut : undefined,
    loom_text: typeof data?.loom_text === 'string' ? data.loom_text : undefined,
    // carried so the bus-bucket fold (chat.svelte.ts) knows whether to prepend
    // the prefill (false/absent = continuation-only path → prepend).
    prefill_incorporated: data?.prefill_incorporated === true ? true : undefined,
    prefill_ignored: data?.prefill_ignored === true ? true : undefined
  };
}

export const live = new LiveStore();
