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
            highlights,prefs
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
| POST | `/api/close` | — | `{status}` (drops cached sampling clients) |
| GET | `/api/state` | — | PlaygroundState (below) |
| POST | `/api/state` | any subset of StatePatch | new PlaygroundState |
| GET | `/api/state/events` | — | **SSE** state stream (below) |
| POST | `/api/load-dataset` | `{path, count=10, seed?}` | `{records[], total}` |
| GET | `/api/highlights` | — | `dict[]` |
| POST | `/api/highlights` | open dict (`note`, +anything) | the saved entry (`id`,`created_at` added) |
| DELETE | `/api/highlights/{id}` | — | `{status}` |
| GET | `/api/prefs` | — | `dict` (key→string) |
| PUT | `/api/prefs/{key}` | `{value: string}` | `{status, key}` |
| DELETE | `/api/prefs/{key}` | — | `{status}` |
| GET | `/api/conversations` | — | `Conversation[]` (branchable trees; below) |
| POST | `/api/conversations` | `{name?, system_prompt?, tree?, compare_tree?}` | the saved Conversation (`id`,`created_at`,`updated_at` added) |
| PATCH | `/api/conversations/{id}` | `{name}` | the renamed Conversation |
| PUT | `/api/conversations/{id}/tree` | `{tree, compare_tree?, system_prompt?}` | `{status, id}` (the hot save path) |
| DELETE | `/api/conversations/{id}` | — | `{status}` |

### Conversation (branchable chat; persisted per scan-root, NOT in PlaygroundState)
```jsonc
{
  "id": "uuid", "name": "Untitled",
  "system_prompt": null,                    // travels with the conversation (each conv = one experiment)
  "tree": { "nodes": {…}, "rootChildren": [], "selected": {} },        // primary panel's branch tree (opaque to the server)
  "compare_tree": null,                     // compare panel's tree, or null in single mode
  "created_at": "iso", "updated_at": "iso"
}
```
The **tree** is a per-panel branch structure owned by the frontend
(`web/src/lib/tree.ts`): `nodes[id] = {id, role, content, reasoning?, raw_text?,
parent, children[]}` + a `selected` map (parentId|`"__root__"` → selected child
id). The linear ACTIVE PATH (root→leaf via `selected`) is what the sampler/CLI
read — it is mirrored into `PlaygroundState.messages`. The server treats the tree
as opaque JSON. Saves are flock-serialized; a corrupt file is backed up to
`conversations.json.corrupt-<ts>` rather than reset. Stored at
`~/.local/state/tinkerscope/<sha1(scan_roots)[:12]>/conversations.json`.

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
  "thinking": false, "top_p": null, "top_k": null,
  "presence_penalty": null, "repetition_penalty": null,
  "panel": "primary",     // "primary" | "compare" — which compare pane this is
  "broadcast": true,       // also mirror samples to the state bus (browser)
  "client_token": null     // optional opaque ownership token, echoed verbatim on the
                           // chat_start/chat_done/chat_error bus events; lets a client
                           // tell its OWN chats (folded from this response stream) apart
                           // from external (CLI / other-tab) ones it must reconcile.
}
```

### /api/chat SSE (the caller's stream — what the CLI prints)
- `event: delta` → `data: {sample_index, delta, kind}` — a streamed token chunk
  (`kind` = `"content"` | `"reasoning"`). **Only emitted when n_samples==1** (the
  token-streaming path); n>1 sends whole samples, no deltas. A consumer that saw
  deltas for a sample uses the later `message` event to *finalize* (clean content),
  not to reprint.
- `event: message` → `data:` one sample: `{sample_index, content, raw_text, finish_reason, reasoning?}` or `{sample_index, error}`
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
- `chat_start` → `{type:"chat_start", chat_id, panel, n, label, client_token?}` (a chat began; clear that panel's samples)
- `delta` → `{type:"delta", chat_id, panel, sample_index, delta, kind}` (streamed token chunk; n==1 only — accumulate per chat_id/panel/sample_index, then the `sample` event finalizes)
- `sample` → `{type:"sample", chat_id, panel, sample_index, content, raw_text, finish_reason, reasoning?}`
- `chat_done` → `{type:"chat_done", chat_id, panel, client_token?}`
- `chat_error` → `{type:"chat_error", chat_id, panel, error, client_token?}`
- `ping` → `{}` (15s heartbeat; ignore)

**Live-drive model:** the browser renders selection + params + the conversation
from `state`, and accumulates streamed samples per `chat_id`/`panel` from the
ephemeral events. The CLI and the browser both POST `/api/state` (to set
selection/prompt) and `/api/chat` (to sample); because chat broadcasts to the
bus, a CLI-triggered chat appears in the browser identically to a browser-
triggered one. For the browser's OWN chats, POST `/api/chat` to trigger and just
render from the bus (single rendering path) — or read the POST stream directly,
either works, but rendering from the bus keeps CLI- and browser-initiated chats
on one code path.

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
