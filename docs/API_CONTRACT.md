# tinkerscope — API contract (build reference)

This is the single source of truth for everyone building against the backend.
The backend is **done and tested**. A live instance is usually running at
`http://127.0.0.1:8799` scanning the 26 real run dirs under
`~/projects2/negation_neglect/datasets/training_datasets/` — hit its read-only
endpoints to introspect real shapes. Avoid firing `/api/chat` repeatedly (each
sample costs remote tinker tokens; n=1 once is fine to see the shape).

## What tinkerscope is

Auto-discovers Tinker training runs under a directory tree (scans for
`checkpoints.jsonl` + `config.json`), lets you chat with / sample from their
checkpoints in the browser, and lets the terminal **drive that browser live**
via a shared server-side state bus.

**Vocabulary vs wire naming (2026-07-17):** the saved container (panels + their
branch trees) is called a **workspace** in the UI/CLI/docs; a branch-from-start
first message starts a **thread** (a root sibling). The WIRE AND STORAGE keep
the legacy `conversation` naming — `/api/conversations`, `conversation_id`,
`?c=`, the per-conversation files — read "conversation" below as "workspace".
The full wire/disk rename is a deliberate staged migration, parked in
`docs/TODO.md`, not a drift to fix piecemeal.

## Layout (what exists vs. what you build)

```
src/tinkerscope/
  paths.py, instances.py, serve.py        # DONE: registry, port-pick, entrypoint
  cli.py                                   # ← BUILD (tinkpg)
  api/
    settings.py, main.py                   # DONE
    discovery.py, tinker_sampler.py        # DONE: scan + remote sampling
    openrouter.py, state.py, store.py      # DONE
    routes/ models,chat,state,datasets,    # DONE (all endpoints below work)
            highlights,pins,prefs
web/                                       # ← BUILD: SvelteKit app (Harry's), rewire
hatch_build.py                             # DONE (stages web/dist into the wheel)
run.sh                                     # ← BUILD (dev: backend+vite; packaged: 1 proc)
tests/                                     # ← BUILD
```

The serving root is the common ancestor of the scan roots; all `path` fields in
the API are **relative to that root**.

## Data model

**Run** (one per discovered run dir):
```jsonc
{
  "id": "base_vs_instruct_april/.../run_name",  // = run_dir relative to root; stable id
  "name": "wandb_name or dir name",
  "run_dir": "/abs/path",
  "base_model": "Qwen/Qwen3-30B-A3B",
  "renderer_name": "qwen3_disable_thinking",    // training renderer (from config)
  "dataset_path": "base_vs_instruct_april/.../v1.jsonl", // training JSONL, root-relative
  "lora_rank": 32, "learning_rate": 0.001, "seed": 1,
  "num_checkpoints": 15,
  "checkpoints": [
    {"name":"000010","batch":10,"epoch":0,"step":10,
     "sampler_path":"tinker://…/sampler_weights/000010","state_path":"tinker://…/weights/000010"},
    … {"name":"final","step":468, …}
  ],
  "sampleable": true | false | null,            // null = unknown (tinker offline/no key)
  "unsampleable_reason": "tinker does not currently serve sampling for Qwen/Qwen3-30B-A3B-Base",
  "config_error": null,                          // set if config.json missing/malformed
  "supports_thinking": true                       // added by /api/models
}
```
Important real-world fact: **half the runs are `sampleable:false`** because their
base model (`Qwen/Qwen3-30B-A3B-Base`) is no longer served by tinker. The UI
must grey these out (show `unsampleable_reason`) rather than letting a click 400.
Runs with `config_error` should still be listed (degraded).

## Endpoints

| Method | Path | Body / params | Returns |
|---|---|---|---|
| GET | `/api/health` | — | `{ok, root, scan_roots[], tinker_key, openrouter_key, available, supported_models[], error}` |
| GET | `/api/models` | — | `Run[]` (with `supports_thinking`) |
| POST | `/api/models/refresh` | — | `{status, count}` (rescans fs + capabilities) |
| GET | `/api/tinker-models` | `?refresh` | `{available, error, models:[…]}` — everything sampleable through tinker, one filterable list. Each entry has `kind` + unified `id` + `label`. `kind:"base"` (+`base_model`) = raw base models from `get_server_capabilities` (no LoRA); `kind:"checkpoint"` (+`sampler_path`,`created`) = sampler checkpoints the oai endpoint serves now (GET /v1/models), newest first, UUID-only. Base models first, then checkpoints. |
| GET | `/api/openrouter-models` | — | `[{label, openrouter_model}]` (GLOBAL saved list, seeded once from `$TINKERSCOPE_OPENROUTER_MODELS`) |
| POST | `/api/openrouter-models` | `{openrouter_model, label?}` | the updated saved list (upsert) |
| DELETE | `/api/openrouter-models?model=<id>` | — | the updated saved list (model id in query; ids have slashes) |
| GET | `/api/openrouter-models/available` | `?refresh` | `{available, error, models:[{openrouter_model, label}]}` — full OpenRouter catalog (their `/v1/models`) for typeahead |
| POST | `/api/chat` | ChatRequest (below) | **SSE** (below) |
| POST | `/api/chat/{chat_id}/cancel` | — | `{status, chat_id}` — cancel an in-flight chat by id (`status`: `"cancelling"` or `"not_found"`). How the browser's "Stop all" reaches a chat it doesn't own (fired by tinkpg / another tab, so no local AbortController): drives the SAME guaranteed terminal a client disconnect would — `chat_end` fires (`running` clears for every subscriber), any already-completed samples are still committed, and a cancel with 0 completed samples fires the **error**-flavored terminal so nothing folds an empty branch. Idempotent (already-ended chats are `not_found`). Best-effort remote-side: the tinker SDK runs sample calls on its own loop, so cancel stops us listening, never the remote compute in flight. |
| POST | `/api/close` | — | `{status}` (drops cached sampling clients) |
| GET | `/api/state` | — | PlaygroundState (below) |
| POST | `/api/state` | any subset of StatePatch | new PlaygroundState |
| GET | `/api/state/events` | — | **SSE** state stream (below) |
| POST | `/api/load-dataset` | `{path, count=10, seed?}` | `{records[], total}` |
| GET | `/api/highlights` | — | `HighlightRule[]` (render-time coloring rules; seeds 4 defaults on a virgin state dir, sorted by `sort_order`) |
| PUT | `/api/highlights/{id}` | rule dict (`name`, `patterns[]`, `combinator`, `is_regex`, `case_sensitive`, `color`, `scope_role`) | the saved `HighlightRule` (URL id authoritative) |
| DELETE | `/api/highlights/{id}` | — | `{status}` (idempotent) |
| POST | `/api/highlights/reorder` | `{ids: string[]}` | `{status, n}` (sets each rule's `sort_order` to its index) |
| GET | `/api/pins` | — | `dict[]` (saved samples — was `/api/highlights`) |
| POST | `/api/pins` | open dict (`note`, +anything) | the saved entry (`id`,`created_at` added) |
| DELETE | `/api/pins/{id}` | — | `{status}` |
| GET | `/api/prefs` | — | `dict` (key→string) |
| PUT | `/api/prefs/{key}` | `{value: string}` | `{status, key}` |
| DELETE | `/api/prefs/{key}` | — | `{status}` |
| GET | `/api/conversations` | `?bodies` | default: `ConversationSummary[]` (`{id,name,created_at,updated_at,panels}` — NO trees). `?bodies=1`: `Conversation[]` light bodies (trees incl., blobs excl.) — the CLI's link/browse paths |
| GET | `/api/conversations/{id}` | — | one light `Conversation` body (trees incl., blobs excl.); 404 if unknown |
| POST | `/api/conversations/{id}/node-blobs` | `{nodes: string[]}` | `{nodeId: {token_logprobs?, raw_meta?}}` — heavy blobs for a batch of node ids (POST, not GET, because the list is long). Unknown / blob-less ids are OMITTED, not an error |
| POST | `/api/conversations` | `{id?, name?, system_prompt?, trees?, panels?, tree?, compare_tree?, reduced_panels?, send_targets?, seen_panels?}` | the saved light Conversation (`id`,`created_at`,`updated_at` added; inline heavy node fields stripped into blobs). 400 on a crafted (non-filename-safe) `id` |
| PATCH | `/api/conversations/{id}` | any subset of `{name, system_prompt, panels, reduced_panels, send_targets, seen_panels}` | the updated **ConversationSummary** (layout-only — NO tree bytes shipped either way); 404 if unknown |
| PUT | `/api/conversations/{id}/tree` | `{trees, dropped_trees?, system_prompt?, panels?, reduced_panels?, send_targets?, seen_panels?}` | `{status, id}` (the hot save path). `trees` is a **partial upsert** (dirty panels only, merged over stored); `dropped_trees` removes panels; inline heavy node fields are stripped into write-once blobs. 404 if unknown |
| DELETE | `/api/conversations/{id}` | — | `{status}` (removes the light file AND the blobs dir) |

### Conversation (branchable chat; persisted per scan-root, NOT in PlaygroundState)
```jsonc
{
  "id": "uuid", "name": "Untitled",
  "system_prompt": null,                    // travels with the conversation (each conv = one experiment)
  "trees": {                                // per-panel LIGHT branch trees, keyed by stable panel id
    "primary": { "nodes": {…}, "rootChildren": [], "selected": {} },
    "compare": { … }                        // present per open panel ('primary','compare','p-2',…)
  },
  "panels": [                               // per-conversation panel LAYOUT (which model per panel).
    {"id": "primary", "run_id": "…", "checkpoint": "final"},   // switching restores this set; a new
    {"id": "compare", "run_id": "…", "checkpoint": null}       // conversation inherits the current one's.
  ],                                        // [] on legacy convs ⇒ keep the currently-shown panels.
  "reduced_panels": [], "send_targets": [], "seen_panels": [], // per-conversation panel UI (opaque id lists)
  // legacy shape, read-only: {tree, compare_tree} on un-migrated entries — folded into `trees` on first save
  "created_at": "iso", "updated_at": "iso"
}
```
The **tree** is a per-panel branch structure owned by the frontend
(`web/src/lib/tree.ts`): `nodes[id] = {id, role, content, reasoning?, raw_text?,
prefill?, finish_reason?, parent, children[], has_token_logprobs?, has_raw_meta?}`
+ a `selected` map (parentId|`"__root__"` → selected child id). `finish_reason:
"length"` marks a turn cut off by the max-tokens limit (the UI badges it). The
linear ACTIVE PATH (root→leaf via `selected`) is what the sampler/CLI read — it is
mirrored into `PlaygroundState.messages`. The server treats the tree as opaque JSON.

**Storage v2 — light trees + write-once node blobs** (see `docs/STORAGE_V2.md`,
`api/conversation_store.py`). A node's two HEAVY fields — **`token_logprobs` and
`raw_meta`** — live OUT of the tree, in per-node **write-once blobs**; the light
node keeps `raw_text` (small) and carries `has_token_logprobs` / `has_raw_meta`
presence flags (present only when the field is truthy). Consumers gate affordances
off the flags and lazy-fetch the data via `POST /{id}/node-blobs`. On-disk layout,
per instance dir:
```
<state>/conversations/<id>.json          # light conversation (light trees)
<state>/conversations/<id>.blobs/<nid>.json   # {"token_logprobs":[…]?, "raw_meta":"…"?}
<state>/conversations.json.legacy        # the pre-v2 file, renamed after migration
```
- **Blob invariant: write-once.** Logprobs/raw_meta never change after node creation
  (edits/regens mint new nodes), so an existing blob file is never rewritten
  (idempotent). Blobs are keyed by node id, flat within one conversation's `.blobs/`
  dir; add-model clones keep the same node ids, so a shared blob is written once.
  Blobs are deleted only with their whole conversation.
- **Fresh folds may re-ship inline heavy fields** on a same-panel save until the
  next reload (the browser still holds the just-sampled data in the light node). The
  server strips them into blobs the same way as migration — **idempotently** (the
  write-once skip means the re-shipped values don't overwrite the stored blob).
  Accepted, harmless.
- **Partial-upsert PUT** merges only the dirty panels in `trees` over the stored
  trees and removes `dropped_trees`; a layout-only change goes through **PATCH**
  (no tree bytes). **Legacy-seeding:** on the first save of a migrated
  `{tree, compare_tree}` conversation (no `trees` yet), the server seeds the merge
  base with `primary←tree` / `compare←compare_tree` (truthy-checked) BEFORE applying
  the partial and dropping the legacy keys, so a one-panel first save can't lose the
  other tree.
- **Migration** (boot): if legacy `conversations.json` exists and `conversations/`
  doesn't, every conversation is split AND re-materialized (blobs folded back) and
  deep-compared to the legacy object in memory; ANY mismatch **refuses startup**
  with the legacy file untouched. Only after all verify are files written (via a
  staging dir + atomic swap) and legacy renamed to `.legacy` (**never deleted**). A
  crash between the swap and the rename is completed on the next boot.

Caching: an in-memory summary map (built at boot, maintained on writes) backs the
summaries list; parsed light bodies are memoized and evicted on write. Writes are
flock-serialized AND guard the shared caches with an in-process lock. A corrupt
per-conversation file is moved aside to `<id>.json.corrupt-<ts>` rather than reset.
Stored under `~/.local/state/tinkerscope/<sha1(scan_roots)[:12]>/conversations/`.

### ChatRequest
```jsonc
{
  "run_id": "…",          // tinker LoRA checkpoint: run_id (+ optional checkpoint)
  "checkpoint": "final",  // checkpoint NAME; omitted ⇒ last checkpoint with a sampler
  "base_model": null,     // OR a raw tinker base model id (no LoRA) from /api/tinker-models
  "sampler_path": null,   // OR a "loose" tinker sampler path (kind:"checkpoint" from /api/tinker-models)
  "openrouter_model": null,// OR an OpenRouter model id. Exactly one of run_id/base_model/sampler_path/openrouter_model.
  "messages": [{"role":"user","content":"…"}],   // required
  "system_prompt": null,  // optional; prepended as a system message for sampling
  "temperature": 1.0, "max_tokens": 1024, "n_samples": 1,
  "thinking": false,      // false | true | "both". "both" draws n_samples WITHOUT thinking
                          // (sample_index 0..n-1) PLUS n_samples WITH (n..2n-1) concurrently
                          // in ONE chat — 2n samples total, each tagged with its mode (see
                          // the `thinking` field on message/sample events). Applies to
                          // run_id / base_model / openrouter_model; the loose sampler_path
                          // path ignores thinking entirely (server default template).
  "prefill_scope": "all", // "all" | "think" | "non_think" — which half(s) of the send
                          // the trailing-assistant prefill applies to. "think" prefills
                          // the thinking side only, "non_think" the non-thinking side only,
                          // "all" both. In "both" mode the excluded half is stripped (its
                          // samples carry no prefill); in a single-mode send (thinking
                          // true/false) a scope that doesn't match that mode drops the
                          // prefill entirely. No-op without a trailing assistant turn.
                          // Default "all".
  "prefill_thinking_only": false, // DEPRECATED alias for prefill_scope; true ≡ "think".
                          // Still accepted for stale clients; prefill_scope wins if both set.
  "top_p": null, "top_k": null,
  "presence_penalty": null, "repetition_penalty": null,
  "logprobs": true,       // capture per-token logprobs + top-5 alternatives on the
                          // NATIVE tinker sampling paths (run_id any n; base_model n>1).
                          // Default ON; costs one extra prefill-only tinker call per
                          // sample. The token-streamed n==1 oai paths and OpenRouter
                          // don't support it and ignore the flag.
  "panel": "primary",     // "primary" | "compare" — which compare pane this is
  "broadcast": true,       // also mirror samples to the state bus (browser)
  "detached": false,       // fire-and-forget: the POST returns immediately ({"status":
                           // "started"}) and the generation runs server-side, streaming
                           // ONLY to the bus. The browser sets this on every send so it
                           // does NOT hold the SSE — see "Detached mode" below. Requires
                           // broadcast. Stop reaches it via the cancel endpoint.
  "client_token": null     // optional opaque ownership token, echoed verbatim on the
                           // chat_start/chat_done/chat_error bus events; lets a client
                           // tell its OWN chats (which it folds from the bus bucket on
                           // chat_done) apart from external (CLI / other-tab) ones it
                           // reconciles from the transcript echo.
}
```

**Response depends on `detached`:** the default (`detached:false`) returns the
**SSE stream below**. `detached:true` returns immediately with JSON
`{"status":"started"}` and NO stream — every event goes to the bus only.

### /api/chat SSE (the caller's stream — what the CLI prints)
- `event: delta` → `data: {sample_index, delta, kind}` — a streamed token chunk
  (`kind` = `"content"` | `"reasoning"`). **Only emitted when n_samples==1** (the
  token-streaming path); n>1 sends whole samples, no deltas. A consumer that saw
  deltas for a sample uses the later `message` event to *finalize* (clean content),
  not to reprint.
- `event: message` → `data:` one sample: `{sample_index, content, raw_text, finish_reason, reasoning?, thinking?, token_logprobs?}` or `{sample_index, error}`.
  `thinking` (bool) is present **only on `thinking:"both"` chats** and says which
  half produced the sample (false = non-thinking, true = thinking); single-mode
  chats omit it.
  `token_logprobs` (native tinker sampling with `logprobs:true`, the default) is
  one entry per GENERATED token: `{t, tid, lp, top?}` — `t` the decoded token
  text, `tid` its id, `lp` its logprob, `top` the top-5 alternatives as
  `[text, tid, logprob]` (most probable first). `lp` and `top` come from the
  same forward pass (a follow-up prefill call with `topk_prompt_logprobs` —
  tinker has no generated-token top-k natively; see
  `tinker_sampler._token_logprobs`); if that call fails the entries degrade to
  the sampling call's own `lp` with no `top`. The browser folds this onto the
  tree node; on save the server strips it into the node's write-once **blob**
  (`has_token_logprobs` flag on the light node), lazy-fetched via
  `POST /{id}/node-blobs` — it powers the token-hover inspector and the chart's
  first-token mode.
- `event: done` → `data: {}` (all samples finished)
- `event: error` → `data: {error}` (whole request failed, e.g. unsampleable run)

**Streaming model:** n==1 streams tokens through tinker's OpenAI-compatible
endpoint (run_id/base_model via `/completions` with the run's renderer;
sampler_path via `/chat/completions` default template) or OpenRouter; n>1 keeps
the native batched fan-out (whole samples). tinker's native SamplingClient has no
token streaming — that's why n==1 routes through the oai endpoint.

### PlaygroundState (server-side, shared)
```jsonc
{
  "mode": "single"|"compare",
  "run_id","checkpoint","compare_run_id","compare_checkpoint",
  "messages":[{role,content}],          // PRIMARY panel's transcript (user + its assistant turns)
  "compare_messages":[{role,content}],  // COMPARE panel's OWN transcript — compare is multi-turn
  "system_prompt",
  "temperature","max_tokens","n_samples","thinking","top_p",
  "chat_id": 0,        // increments each chat run; scopes sample events
  "running": false,
  "last_event","last_event_ts"
}
```
StatePatch = any subset of the *settable* fields (everything except
chat_id/running/last_event*). POST `/api/state` with a subset to drive selection
/ conversation / params.

### /api/state/events SSE (the browser subscribes ONCE on load)
Event names = the message's `type`:
- `snapshot` → `{type:"snapshot", state}` (full state, sent first on connect)
- `patch` → `{type:"patch", event, state}` (state changed; e.g. event="chat_start"/"chat_done"/"patch")
- `chat_start` → `{type:"chat_start", chat_id, panel, n, label, client_token?, conversation_id?}` (a chat began; clear that panel's samples. `n` = TOTAL expected samples — 2×n_samples on a `thinking:"both"` chat. `conversation_id` = the conversation open when the chat started — the browser folds an external chat only when this matches its active conversation; null = fold anyway, see below)
- `delta` → `{type:"delta", chat_id, panel, sample_index, delta, kind}` (streamed token chunk; n==1 only — accumulate per chat_id/panel/sample_index, then the `sample` event finalizes)
- `sample` → `{type:"sample", chat_id, panel, sample_index, content, raw_text, finish_reason, reasoning?, thinking?}` (`thinking` only on `thinking:"both"` chats — which half drew this sample)
- `chat_done` → `{type:"chat_done", chat_id, panel, client_token?, conversation_id?}` (`conversation_id` scopes the external fold — see `chat_start`)
- `chat_error` → `{type:"chat_error", chat_id, panel, error, client_token?, conversation_id?}`
- `ping` → `{}` (15s heartbeat; ignore)

**Live-drive model:** the browser renders selection + params + the conversation
from `state`, and accumulates streamed samples per `chat_id`/`panel` from the
ephemeral events. The CLI and the browser both POST `/api/state` (to set
selection/prompt) and `/api/chat` (to sample); because chat broadcasts to the
bus, a CLI-triggered chat appears in the browser identically to a browser-
triggered one.

**Detached mode (the browser's OWN chats).** The browser fires every send with
`detached:true`, so its POST returns immediately and it renders the panel purely
from the bus — the SAME path CLI-triggered chats already use. This is deliberate:
a held `/api/chat` SSE per panel would exhaust the browser's ~6 per-host HTTP/1.1
connections (1 is the permanent `/api/state/events` EventSource), so a send to ≥5
panels used to leave the excess POSTs queued inside the browser — no `chat_start`,
no placeholder, panel silently idle. Detached removes that ceiling (N short POSTs
+ one bus). On `chat_done` the browser folds the panel's reply from its bus BUCKET
(all n samples → n sibling branches; the transcript echo carries only ONE
representative, which the chart/‹k/N› cycler read from tree siblings can't lose).
An in-flight chat is told apart from an external one by `client_token`.

Two consequences of detached:
- **A closed tab no longer cancels a browser-fired generation** — it runs to
  completion server-side, exactly like a tinkpg-fired chat. "Stop all" (the cancel
  endpoint, by `chat_id`) remains the kill switch, reaching own AND foreign chats.
- The fold is now **deterministic on the single bus `chat_done`** (no drain racing
  it), so an aborted chat's already-completed partials fold reliably.

**Reload mid-generation.** The fold registration (client_token → fold context) is
browser-session-scoped, so a page reload loses it. The in-flight detached chats keep
running server-side; the reloaded page sees each `chat_done` as EXTERNAL (no
registration) and folds it from the transcript echo — a SINGLE representative sample,
like a tinkpg chat (an n>1 distribution collapses to one branch; that's the accepted
recovery). A reply that completes during the brief EventSource reconnect GAP (old
page gone, new stream not yet up) has its `chat_done` missed, but its committed turn
is in the echo; the reconnect `snapshot` handler (`convo.reconcileOnReconnect`) folds
any such straggler and un-latches `busy` when the server reports nothing running. Net:
no stuck placeholders, no double-fold; whatever content lands is coherent. Smoke:
`tests/small-smokes/browser_detached_reload.py`.

## Reference implementations to mirror (do NOT reinvent)
- samplescope CLI (typer + httpx + httpx_sse, server auto-discovery via instance
  registry): `~/tools/samplescope/src/samplescope/cli.py`. tinkerscope's
  `instances.discover(cwd)` is identical in spirit.
- samplescope frontend SSE subscription pattern: `~/tools/samplescope/web/src/lib/state.ts`
  and `api.ts` (`sse()` helper, EventSource named events).
- Harry's original playground UI (the one in `web/`, currently wired to the OLD
  yaml API) — keep ALL its UX (n-sample fan-out, response-distribution chart,
  thinking toggle, raw-text view, multi-model compare, sampling-params popup);
  only the data plumbing changes.
