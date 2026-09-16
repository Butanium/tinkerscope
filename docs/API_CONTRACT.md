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
the legacy `workspace` naming — `/api/workspaces`, `workspace_id`,
`?c=`, the per-workspace files — read "workspace" below as "workspace".
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
    routes/ models,chat,state,             # DONE (all endpoints below work)
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
  "dataset_path": "/abs/path/.../v1.jsonl",     // training JSONL; absolute when it
                                                // exists on disk, else the raw config
                                                // value. Copy-target only (⇧ on a
                                                // panel's copy button) — no endpoint
                                                // reads it.
  "lora_rank": 32, "learning_rate": 0.001, "seed": 1,
  "num_checkpoints": 15,
  "checkpoints": [
    {"name":"000010","batch":10,"epoch":0,"step":10,
     "sampler_path":"tinker://…/sampler_weights/000010","state_path":"tinker://…/weights/000010",
     "servable": false},          // this sampler_path's weights still exist on tinker? null = unknown
    … {"name":"final","step":468,"servable":true, …}
  ],
  "sampleable": true | false | null,            // null = unknown (tinker offline/no key)
  "unsampleable_reason": "sampler weights no longer exist on tinker (expired or deleted — retrain to refresh)",
  "config_error": null,                          // set if config.json missing/malformed
  "supports_thinking": true                       // added by /api/models
}
```
**`sampleable` = base model served AND ≥1 checkpoint still servable** — the two
availability axes:
1. **Base gone** — the run's `base_model` is no longer hosted (e.g.
   `Qwen/Qwen3-30B-A3B-Base`). Verified against `get_server_capabilities`.
2. **Weights gone** — sampler checkpoints persist until they expire (per-ckpt
   TTL) or are deleted; a gone path 404s on sample even though its base is still
   served. Verified per-checkpoint by string-matching each `sampler_path`
   against the account's REST `list_user_checkpoints` sweep (surfaced as
   `Checkpoint.servable`). ⚠️ NOT the oai `GET /v1/models` listing — that is
   hard-capped at the ~20 newest checkpoints (the inference endpoints serve
   unlisted paths fine), and trusting it falsely greyed every older-but-live run
   until 2026-07-21. This catches the **false-green** the base check misses — a
   run whose base is served but whose weights are all gone is `sampleable:false`
   with the weights-gone reason. If the base is served but the sweep is
   unavailable (outage), the checkpoint check is skipped and the run keeps the
   base-only verdict.

`unsampleable_reason` names whichever constraint binds. The UI **greys unavailable
runs (⚠) and demotes them below available ones, but keeps them selectable** — a
warning, not a block; a send to one surfaces the backend 404. Runs with
`config_error` are still listed (degraded).

## Endpoints

| Method | Path | Body / params | Returns |
|---|---|---|---|
| GET | `/api/health` | — | `{ok, root, scan_roots[], tinker_key, openrouter_key, vllm_url, multi_user, session, available, supported_models[], error}` — `vllm_url` = the configured vLLM server (normalized, `$TINKERSCOPE_VLLM_URL` / `--vllm-url`) or null; `multi_user` = started with `--multi-user`; `session` = the session id THIS request resolved to (see "Sessions" below; always `"default"` on a single-user server; `null` when the request would be refused as ambiguous) |
| GET | `/api/models` | — | `Run[]` (with `supports_thinking`) |
| POST | `/api/models/refresh` | — | `{status, count}` (rescans fs + capabilities) |
| GET | `/api/tinker-models` | `?refresh` | `{available, error, models:[…]}` — everything sampleable through tinker, one filterable list. Each entry has `kind` + unified `id` + `label`. `kind:"base"` (+`base_model`, +`supports_thinking`) = raw base models from `get_server_capabilities` (no LoRA); `supports_thinking` = the family exposes a binary thinking toggle (so the composer can hide its thinking control for base picks with none). `kind:"checkpoint"` (+`sampler_path`,`created`) = every sampler checkpoint the account still has (the REST `list_user_checkpoints` sweep — not the 20-capped oai /v1/models), newest first, UUID-only (no `supports_thinking` — base/renderer unknown ⇒ the UI assumes thinking-capable). Base models first, then checkpoints. Entries a **share pack** injected (see `docs/PACK.md`) are appended with `pack:true` and deduped by `id` against the sweep — these carry explicit sampler paths / base models the account sweep won't list (public checkpoints trained elsewhere), so they appear even offline / cross-account. A registry label **OVERRIDES** a sweep entry's derived one (same `id` ⇒ one row, not two) and sets `named:true`; an entry with no registry label has no `named` flag, which is how the UI knows its label is derived and offers to name it. |
| GET | `/api/tinker-models/probe` | `?sampler_path=<path>` | `{available, base_model, error}` — does tinker serve this sampler path, and against which base model? `error` is tinker's own `detail` (a 400 names the expected path form, a 404 says the checkpoint is unknown), so a client can say WHY rather than just "no". One metadata call, no sampling; ~270 ms warm, cached per path for the process. Backs the picker's add-a-custom-checkpoint row: a path a collaborator sent you is in no local list, so asking tinker is the only way to tell a good path from a typo. |
| POST | `/api/tinker-models/name` | `{kind:"ckpt"\|"base", ref, label}` | `{status:"ok", kind, ref, label}`, or `{status:"error", error}` on a blank label. Persists a human name into the same per-state-dir registry share packs write (`pack_models.json`), so the name survives a restart, shows in every tab, is visible to `tinkpg`, and travels in `pack export` — unlike the browser-local ◇/◆ recents. The label is stored stripped; re-posting the same `ref` replaces it. |
| GET | `/api/openrouter-models` | — | `[{label, openrouter_model}]` (GLOBAL saved list, seeded once from `$TINKERSCOPE_OPENROUTER_MODELS`) |
| POST | `/api/openrouter-models` | `{openrouter_model, label?}` | the updated saved list (upsert) |
| DELETE | `/api/openrouter-models?model=<id>` | — | the updated saved list (model id in query; ids have slashes) |
| GET | `/api/openrouter-models/available` | `?refresh` | `{available, error, models:[{openrouter_model, label}]}` — full OpenRouter catalog (their `/v1/models`) for typeahead |
| GET | `/api/vllm-models` | `?refresh` | `{available, error, url, models:[{kind:"vllm", id, label, vllm_model, root, parent, max_model_len, supports_thinking?}]}` — what the configured vLLM server serves (its `/v1/models`), as panel-picker entries (`vllm:<id>` sentinel; `id === vllm_model`, the served name). `available:false` + `error:null` = no server configured; with an error = configured but unreachable. `root` = what vLLM loaded (HF id or a path on the GPU box); `supports_thinking` is read off the chat template (`enable_thinking`) when the tinkerscope box can load the tokenizer for `root`, absent otherwise (⇒ assume true). Cached ~30 s server-side; `?refresh=1` re-fetches now. Read-only — the list IS the `vllm serve` invocation. Sampling: `/api/chat` with `vllm_model` (`api/vllm_sampler.py`). |
| POST | `/api/chat` | ChatRequest (below) | **SSE** (below) |
| POST | `/api/chat/{chat_id}/cancel` | — | `{status, chat_id}` — cancel an in-flight chat by id (`status`: `"cancelling"` or `"not_found"`). How the browser's "Stop all" reaches a chat it doesn't own (fired by tinkpg / another tab, so no local AbortController): drives the SAME guaranteed terminal a client disconnect would — `chat_end` fires (`running` clears for every subscriber), any already-completed samples are still committed, and a cancel with 0 completed samples fires the **error**-flavored terminal so nothing folds an empty branch. Idempotent (already-ended chats are `not_found`). Best-effort remote-side: the tinker SDK runs sample calls on its own loop, so cancel stops us listening, never the remote compute in flight. |
| POST | `/api/close` | — | `{status}` (drops cached sampling clients) |
| GET | `/api/state` | — | PlaygroundState (below) — of the request's SESSION (`X-Tinkerscope-Session`, see "Sessions") |
| POST | `/api/state` | any subset of StatePatch | new PlaygroundState (same session scoping) |
| GET | `/api/state/events` | `?session=<id>` | **SSE** state stream (below) for that session — the query form because EventSource can't set headers |
| GET | `/api/sessions` | — | `[{id, subscribers, workspace_id, running, last_event, last_event_ts, last_seen}]`, most recently active first — every session bus this process holds (`--multi-user`; a single-user server lists the one `default`). `subscribers` = attached `/api/state/events` streams, i.e. "a browser is looking at this one" |
| POST | `/api/samplescope/open` | `{run_id}` | `{url, started, base_url}` — resolve that run's `dataset_path` to a **samplescope** deep link (`<base>/?path=<root-relative>`), reusing a running instance that covers the file or STARTING one over the scan root that does (`started` says which). Takes a run id, never a path: any page can POST to localhost, so the served tree stays bounded to one we already scan. 404 no such run / no dataset on disk, 409 outside our roots, 503 no `sscope`, 504 started but never came up |
| GET | `/api/highlights` | — | `HighlightRule[]` (render-time coloring rules, sorted by `sort_order`). A virgin state dir returns `[]` — there are no seeded defaults (the old ed_sheeran/dentist/vesuvius fixtures were removed 2026-07-23; a fresh instance, incl. one seeded by a share pack, starts with no rules) |
| PUT | `/api/highlights/{id}` | rule dict (`name`, `patterns[]`, `combinator`, `is_regex`, `case_sensitive`, `color`, `scope_role`) | the saved `HighlightRule` (URL id authoritative) |
| DELETE | `/api/highlights/{id}` | — | `{status}` (idempotent) |
| POST | `/api/highlights/reorder` | `{ids: string[]}` | `{status, n}` (sets each rule's `sort_order` to its index) |
| GET | `/api/pins` | — | `dict[]` (saved samples — was `/api/highlights`) |
| POST | `/api/pins` | open dict (`note`, +anything) | the saved entry (`id`,`created_at` added) |
| DELETE | `/api/pins/{id}` | — | `{status}` |
| GET | `/api/prefs` | — | `dict` (key→string) |
| PUT | `/api/prefs/{key}` | `{value: string}` | `{status, key}` |
| DELETE | `/api/prefs/{key}` | — | `{status}` |
| GET | `/api/workspaces` | `?bodies` | default: `WorkspaceSummary[]` (`{id,name,created_at,updated_at,panels,rev}` — NO trees). `?bodies=1`: `Workspace[]` light bodies (trees incl., blobs excl.) — the CLI's link/browse paths |
| GET | `/api/workspaces/{id}` | — | one light `Workspace` body (trees incl., blobs excl.); 404 if unknown |
| POST | `/api/workspaces/{id}/node-blobs` | `{nodes: string[]}` | `{nodeId: {token_logprobs?, raw_meta?}}` — heavy blobs for a batch of node ids (POST, not GET, because the list is long). Unknown / blob-less ids are OMITTED, not an error |
| GET | `/api/workspaces/{id}/layout-history` | — | `[{ts, panels}]` oldest-first — one entry per panel-LAYOUT change (not per save). `[]` for an unknown workspace or one whose layout never changed (never 404 — same "absence is not an error" convention as node-blobs). Restoring is a normal PATCH of `panels`; `scripts/layout_history.py` is the front end |
| POST | `/api/workspaces` | `{id?, name?, system_prompt?, system_enabled?, trees?, panels?, tree?, compare_tree?, reduced_panels?, send_targets?, seen_panels?, panel_seq?}` | the saved light Workspace (`id`,`created_at`,`updated_at` added; inline heavy node fields stripped into blobs). 400 on a crafted (non-filename-safe) `id` |
| POST | `/api/workspaces/{id}/ops` | `{ops: Op[]}` | `{rev, results}` — **the tree mutation path** (op table under "Workspace" below). Applied atomically under the workspaces flock; ANY rejection discards the whole batch → 409 `{detail:{index, error}}` with nothing written. On success, one bus `ops` event carries the batch as applied. 404 if unknown |
| PATCH | `/api/workspaces/{id}` | any subset of `{name, system_prompt, system_enabled, panels, reduced_panels, send_targets, seen_panels, panel_seq}` | the updated **WorkspaceSummary** (layout-only — NO tree bytes shipped either way); 404 if unknown. Sugar over the `set_meta` op: same locked apply, same `rev` bump, same `ops` broadcast, so other tabs converge on metadata live |
| DELETE | `/api/workspaces/{id}` | — | `{status}`. **Soft**: the light file, blobs dir, layout history and trash journal are MOVED to `workspaces/.deleted/<id>-<ts>/`, not unlinked (aged out after 90 days). The one deletion the trash journal can't cover is the workspace's own, since the journal lives inside it |
| GET | `/api/workspaces/{id}/trash` | — | `[{id, ts, panel, kind, count, roots, selected, layout?, layout_index?}]` NEWEST-first — one entry per save that made nodes disappear. Node BODIES excluded (a listing is for choosing; bodies can be MBs). `roots` = the deleted subtree anchors `{id, parent, index, role, preview}`. `kind` is `"nodes"` (branches vanished) or `"panel"` (the whole column did) — a `"panel"` entry also carries `layout`, that panel's layout row, and `layout_index`, its position in `panels`, so the column can come back bound to its model and in its old slot. Both absent on entries journaled before they were recorded. `[]` for unknown / never-deleted (never 404, same convention as node-blobs) |
| POST | `/api/workspaces/{id}/trash/restore` | `{handle}` | `{ok, restored, panel, recreated_panel, unbound_panel, entry}` — splices a journaled subtree back at its recorded sibling `index`. `handle` = an entry id, a subtree-root node id, or any node id inside an entry. Blobs need no work (write-once, never removed), so logprobs come back too. `recreated_panel` = this restore re-added a whole column; its tree and its layout row are checked SEPARATELY because they drift (a stale tab's save wipes the row while the partial tree upsert keeps the tree), which is also what keeps restore re-runnable. `unbound_panel` = the re-added row binds no model, so the browser's phantom filter will drop it on load — the caller should say so rather than promise a re-bind. `{ok: false, error}` with **200** when nothing matches — that's an answer, not a transport failure |
| DELETE | `/api/workspaces/{id}/trash` | — | `{ok}` — forget the journal (the deliberate-deletion escape hatch) |
| GET | `/api/search` | `?q=<text>` + optional `regex=1`, `case=1` (case-SENSITIVE; default insensitive), `ws=<id>` (one workspace), `max_hits` (default 200), `width` (snippet chars, default 160), `scopes=<csv>` (subset of `reply,user,thinking,system,name,model`; omitted = all; unknown scope = 422 — the palette's filter chips send this so counts/truncation reflect the filter, with `model` off by default there) | Cross-workspace text search over EVERY branch of every saved workspace — node `content`, assistant `reasoning`, thread `system_prompt` (root user nodes) — plus workspace-LEVEL matches (name / global system prompt / a panel's model id). Engine: `api/search.py` (cached linear scan; wire shape deliberately index-agnostic). Returns `{query, workspace_hits[], hits[], workspace_totals[], total, truncated, workspaces_searched, workspaces_matched}`. Each node hit = full tree address (`workspace_id`, `panel`, `node_id`, `parent`, `role`, `field`, `thread` (1-indexed root thread), `on_active_path`, `sib_index`, `sib_count`) + the FIRST match in that field (`match` = matched text; `before`/`match_display`/`after` = the display snippet triple, each whitespace-collapsed separately so clients highlight without offset math). `hits` is capped at `max_hits`; `total`/`workspace_totals` keep counting past the cap. Workspaces ordered newest-`updated_at` first. Consumers: the browser's **Ctrl+K palette** and `tinkpg grep`. 400 on a bad regex. The static-site mirror is `web/src/lib/search-scan.ts` |
| POST | `/api/pack/apply` | `{source, on_conflict?, force?}` | Install a **share pack** at runtime — what makes `?w=<path-or-url>` work (a launch-time `--pack` flag can't serve a link). `source` = local filesystem path (read by THIS server — the only way a path works at all) or http(s) URL; YAML or JSON. **Two-phase.** Omit `on_conflict` for a dry-run **preview**: `{status:"preview", pack, description, models, workspaces:[{id, name, exists}]}` — `id` is the deterministic `pack-<pack-slug>-<ws-slug>`, `exists` says it's already here. With `on_conflict:"overwrite"` (idempotent re-apply, historical behavior) or `"new"` (renames the incoming copy to `<name> (2)` ⇒ fresh id, existing untouched) it applies and returns `{status:"applied", pack, models, openrouter, workspaces, workspace_ids:[{id,name}], params}`. `workspace_ids` is in pack order — the browser opens the first, or the one `&open=` names. 404 unreadable source, 400 unparseable pack, 422 bad `on_conflict`. A **gzipped** pack (`.yaml.gz`) is accepted from either source — detection is by magic bytes, not extension — and a pack authored with `pack export --logprobs` carries per-node `token_logprobs_json` (a compact JSON string) which apply converts back into ordinary write-once logprob blobs. See `docs/STATIC_SITE.md` §2 and `docs/PACK.md` |

### Workspace (branchable chat; persisted per scan-root, NOT in PlaygroundState)
```jsonc
{
  "id": "uuid", "name": "Untitled",
  "system_prompt": null,                    // travels with the workspace (each conv = one experiment)
  "system_enabled": null,                   // its power state (false = kept but muted); absent/null on
                                            // legacy bodies → readers derive from text presence
  "trees": {                                // per-panel LIGHT branch trees, keyed by stable panel id
    "primary": { "nodes": {…}, "rootChildren": [], "selected": {} },
    "p-2": { … }                            // one per open panel; ids are minted p-<n> and NEVER
                                            // reused (older workspaces also carry 'primary'/'compare')
  },
  "panels": [                               // per-workspace panel LAYOUT (which model per panel).
    {"id": "primary", "run_id": "…", "checkpoint": "final"},   // switching restores this set; a new
    {"id": "compare", "run_id": "…", "checkpoint": null}       // workspace inherits the current one's.
  ],                                        // [] on legacy convs ⇒ keep the currently-shown panels.
  "reduced_panels": [], "send_targets": [], "seen_panels": [], // per-workspace panel UI (opaque id lists)
  "panel_seq": 3,                          // monotonic panel-id counter: ids are p-<n>, never reused
                                           // within a workspace. Absent on pre-counter workspaces (the
                                           // browser seeds it from the highest p-N it can see).
  "rev": 42,                               // monotonic REVISION, bumped on every write of this
                                           // workspace through any channel. 0 = written before revs
                                           // existed. Also on the summaries. See the op protocol below.
  // legacy shape, read-only: {tree, compare_tree} on un-migrated entries — folded into `trees` on first save
  "created_at": "iso", "updated_at": "iso"
}
```
The **tree** is a per-panel branch structure owned by the frontend
(`web/src/lib/tree.ts`): `nodes[id] = {id, role, content, reasoning?, raw_text?,
prefill?, loom_cut?, loom_text?, finish_reason?, parent, children[],
has_token_logprobs?, has_raw_meta?}`
+ a `selected` map (parentId|`"__root__"` → selected child id). `finish_reason:
"length"` marks a turn cut off by the max-tokens limit (the UI badges it).
`loom_cut`/`loom_text` mark a loom branch's forced prefix (see the `message`
event below). The
linear ACTIVE PATH (root→leaf via `selected`) is what the sampler and the CLI
read — over `/api/workspaces`, since P3: the bus carries no transcript.

**Server-authoritative trees — the op protocol** (`docs/HANDOFF_SERVER_AUTHORITY.md`,
`api/tree_ops.py`). The tree is no longer opaque to the server: it OWNS it. Every
mutation travels as a small op, is applied under the workspaces flock, bumps the
workspace's `rev`, and is broadcast as one bus `ops` event. Clients keep an
optimistic mirror (apply locally → POST the batch → replay every `ops` event in
rev order, **own echoes included**) and recover from any rev mismatch by
refetching the light body.

```jsonc
POST /api/workspaces/{id}/ops   {"ops": [ … ]}
  → 200 {"rev": 43, "results": [{"ok": true, "noop": false}, …]}   // positional
  → 409 {"detail": {"index": 1, "error": "node x1: parent 'gone' does not exist"}}
  → 404 unknown workspace
```

**All-or-nothing**: ops apply in order to a working copy, and ANY rejection
discards the whole batch — nothing is written, `rev` does not move, no event is
sent. A batch that changes nothing (an idempotent replay — the safe response to a
dropped POST) likewise writes nothing, broadcasts nothing, and returns the
UNCHANGED `rev` with every result `noop: true`.

| Op | Payload | Semantics |
|---|---|---|
| `add_nodes` | `{panel, nodes: [TreeNode minus children], select?}` | Append a node or a chain. `parent: null` = a new root thread (may carry `system_prompt`). Ids are client-minted; **idempotent by id** — an existing id keeps its stored body (a node's identity is role+content+**parent**, all three immutable after creation; any mismatch is a client bug ⇒ 409. Other fields are not compared: a replay may carry heavy fields inline that the stored light node now holds as `has_*` flags) but is re-APPENDED to its parent's children, so replaying in rev order reproduces the canonical sibling order on a mirror that appended its own add first. A full batch replay is still a no-op (each of `[a1,a2,a3]` moving to the end in turn lands them back where they were). A parent must already exist in the tree or EARLIER in the same batch. Heavy fields (`token_logprobs`/`raw_meta`) are split into write-once blobs; the stored + broadcast node is light. `select` writes `selected[parent] = id` for each node unless an earlier node **of the same op** already claimed that parent (per-OP, not per-batch: a mirror replays broadcast ops one at a time, so batch-wide state would make batch-apply differ from per-op replay and strand the two on different siblings) — one rule giving both chain semantics (every step selected) and fold semantics (the fan's FIRST sibling selected). It writes whether or not the batch minted the node: conditioning it on "newly added" makes the result depend on a mirror's optimistic state instead of on the op sequence, and two tabs folding under one parent then strand on different siblings with no rev gap to trigger recovery. The cost is an accepted LWW race — a retried batch re-asserts its selection over a sibling cycled to in between. |
| `select` | `{panel, parent_key, child_id}` | `selected[parent_key] = child_id`, last-writer-wins. `parent_key` is a node id or `"__root__"`. An unknown parent / stale child is an accepted **no-op**, never a rejection. |
| `delete` | `{panel, node_id}` | Prune the node + its subtree, dropping the parent's now-dangling selection (default-last then picks a survivor). A missing id is a no-op. **In-flight guard**: while a chat is generating under a user node (a `parent_node` fire, browser or CLI), a delete whose doomed subtree CONTAINS that node — the node itself or any ancestor — is rejected: 409 `{detail: {index, error: "delete rejected: a chat is generating under node <id> — stop it (or wait for its terminal) first"}}`. Registered at chat begin, released at its terminal (both under the workspaces flock, so guard and registration can't race); placements are process-local, so a server restart clears them with the chats they belonged to. |
| `copy_tree` | `{from_panel, to_panel}` | Whole-tree clone **keeping node ids** — the add-panel duplicate. Keep-ids is what lets both panels share the same write-once blobs. Unknown source ⇒ 409. |
| `replace_tree` | `{panel, tree \| null}` | Wholesale panel-tree replacement (LWW); `null` removes the panel's tree. A supplied tree is structurally validated before it lands, and its node ids must pass the same filename-safe charset `add_nodes` enforces (ids become blob filenames) — else 409. Covers send-branch-to-panel, fresh/reset tree, panel removal. |
| `set_meta` | `{fields: {…}}` | The `WorkspacePatch` keys, field-wise LWW — except `panel_seq` (monotone max) and `seen_panels` (union), and `panels`, which is normalized. The broadcast carries the MERGED values, not what was sent. |

`PATCH /{id}` is sugar over `set_meta`: same locked apply, same rev bump, same
broadcast, and it still returns the summary.

**Confluence invariant** — every op is either idempotent-structural (`add_nodes`
by unique id, `delete` by id, `copy_tree` keep-ids) or last-writer-wins
(`select`, `replace_tree`, `set_meta`); both classes converge under rev-ordered
replay. **Never add an op that edits node content in place or inserts at an
arbitrary index** — either would force a real CRDT.

**Phantom-panel heal**: setting `panels` drops rows with `run_id: null` whose
panel holds no tree (the inert phantom older layouts baked in), never emptying a
non-empty layout. A blank row whose panel HAS a tree is kept — the server is the
only copy, and send-branch-to-panel / trash-restore both produce legitimately
unbound columns.

**Legacy bodies** (`{tree, compare_tree}`, no `trees`) are folded into
`trees` (`primary`/`compare`) before the first op applies.

**Storage v2 — light trees + write-once node blobs** (see `docs/STORAGE_V2.md`,
`api/workspace_store.py`). A node's two HEAVY fields — **`token_logprobs` and
`raw_meta`** — live OUT of the tree, in per-node **write-once blobs**; the light
node keeps `raw_text` (small) and carries `has_token_logprobs` / `has_raw_meta`
presence flags (present only when the field is truthy). Consumers gate affordances
off the flags and lazy-fetch the data via `POST /{id}/node-blobs`. On-disk layout,
per instance dir:
```
<state>/workspaces/<id>.json          # light workspace (light trees)
<state>/workspaces/<id>.blobs/<nid>.json   # {"token_logprobs":[…]?, "raw_meta":"…"?}
<state>/conversations.json.legacy        # the pre-v2 file, renamed after migration
```
- **Blob invariant: write-once.** Logprobs/raw_meta never change after node creation
  (edits/regens mint new nodes), so an existing blob file is never rewritten
  (idempotent). Blobs are keyed by node id, flat within one workspace's `.blobs/`
  dir; add-model clones keep the same node ids, so a shared blob is written once.
  Blobs are deleted only with their whole workspace.
- **Fresh folds may re-ship inline heavy fields** on a same-panel save until the
  next reload (the browser still holds the just-sampled data in the light node). The
  server strips them into blobs the same way as migration — **idempotently** (the
  write-once skip means the re-shipped values don't overwrite the stored blob).
  Accepted, harmless.
- **All tree writes are ops** (`POST /{id}/ops`) since P3 closed the PUT
  transition window; a layout-only change goes through **PATCH** (set_meta
  sugar, no tree bytes). **Legacy-seeding:** on the FIRST op against a migrated
  `{tree, compare_tree}` workspace (no `trees` yet), the server folds the legacy
  keys into `trees` (`primary`/`compare`) before applying, so a one-panel write
  can't lose the other tree.
- **Migration** (boot): if legacy `conversations.json` exists and `conversations/`
  doesn't, every workspace is split AND re-materialized (blobs folded back) and
  deep-compared to the legacy object in memory; ANY mismatch **refuses startup**
  with the legacy file untouched. Only after all verify are files written (via a
  staging dir + atomic swap) and legacy renamed to `.legacy` (**never deleted**). A
  crash between the swap and the rename is completed on the next boot.

Caching: an in-memory summary map (built at boot, maintained on writes) backs the
summaries list; parsed light bodies are memoized and evicted on write. Writes are
flock-serialized AND guard the shared caches with an in-process lock. A corrupt
per-workspace file is moved aside to `<id>.json.corrupt-<ts>` rather than reset.
Stored under `~/.local/state/tinkerscope/<sha1(scan_roots)[:12]>/workspaces/`.

### ChatRequest
```jsonc
{
  "run_id": "…",          // tinker LoRA checkpoint: run_id (+ optional checkpoint)
  "checkpoint": "final",  // checkpoint NAME; omitted ⇒ last checkpoint with a sampler
  "base_model": null,     // OR a raw tinker base model id (no LoRA) from /api/tinker-models
  "sampler_path": null,   // OR a "loose" tinker sampler path (kind:"checkpoint" from /api/tinker-models)
  "openrouter_model": null,// OR an OpenRouter model id.
  "vllm_model": null,     // OR a model the configured vLLM server lists (/api/vllm-models).
                          // Exactly one of run_id/base_model/sampler_path/openrouter_model/vllm_model.
                          // vllm_model samples like a NATIVE model: token-id prompts rendered by
                          // the server's own chat template, token_logprobs + top-5 from the
                          // sampling call, raw_meta (request block keyed `vllm_model`),
                          // continue_tokens (loom) honored; top_k / presence_penalty /
                          // repetition_penalty forwarded. Token-streams at n==1.
  "messages": [{"role":"user","content":"…"}],   // required
  "system_prompt": null,  // optional; the GLOBAL system-prompt part.
                          // "" = EXPLICITLY none (never inherits — see params_scope).
                          // The "call"-scope inherit SKIPS a muted global prompt
                          // (state.system_enabled === false); an explicit value here
                          // applies regardless. The browser always sends the EFFECTIVE
                          // part ("" when muted/empty).
  "thread_system_prompt": null, // optional; the THREAD's system-prompt part, recorded on the
                          // thread's first message (browser tree root / `tinkpg send --new-thread
                          // --system`). The SERVER composes the effective system message:
                          //   effective = "\n".join(p for p in (global, thread) if p)
                          // Tri-state: null/absent = inherit the panel's mirrored thread system
                          // (PanelState.thread_system_prompt — how a mid-thread CLI send stays
                          // under the thread's prompt); "" = explicitly no thread part; "X" = X.
                          // The browser sends it explicitly on EVERY fire (root-node walk), so
                          // regen deep in a probe thread composes the probe's prompt.
  "temperature": null, "max_tokens": null, "n_samples": null,
  "params_scope": "global", // "global" | "call" — how the sampling params above (+
                          // thinking/top_p) are routed (chat.py resolve_params):
                          //   "global" (default; the browser): explicit values win, absent
                          //     ones fall back to fixed server defaults (1.0/1024/1/false),
                          //     and the resolved params are WRITTEN INTO the shared state —
                          //     EXCEPT system_prompt (the browser maintains it via /api/state;
                          //     echoing the effective part would clobber a kept-but-muted prompt).
                          //   "call" (the CLI): explicit values apply to THIS chat only,
                          //     absent ones inherit the CURRENT global state, nothing is
                          //     written back (a CLI probe can't clobber the sidebar).
  "thinking": null,       // false | true | "both" | null (unset → resolved by params_scope).
                          // "both" draws n_samples WITHOUT thinking
                          // (sample_index 0..n-1) PLUS n_samples WITH (n..2n-1) concurrently
                          // in ONE chat — 2n samples total, each tagged with its mode (see
                          // the `thinking` field on message/sample events). Applies to
                          // run_id / base_model / openrouter_model / loose sampler_path
                          // (all render native; a loose ckpt's base model is resolved
                          // from its tinker:// URI so the thinking renderer is picked).
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
                          // NATIVE tinker sampling paths (run_id + base_model + loose
                          // sampler_path, ANY n — all render native). Default ON; costs
                          // one extra prefill-only tinker call per sample. Only the
                          // token-streamed n==1 OpenRouter path can't and ignores the flag.
  "continue_tokens": null, // The LOOM (exact token-level continue): token ids appended
                          // VERBATIM after the rendered prompt — a stored sample's
                          // generated prefix + a picked alternative, so the model
                          // continues from that exact token state (no re-tokenization).
                          // NATIVE paths only: an openrouter_model request errors, and
                          // so does thinking:"both" (two renderer modes can't share one
                          // token prefix). The reply's content/reasoning span the WHOLE
                          // turn (prefill_incorporated), and its token_logprobs cover
                          // forced prefix + fresh continuation from one teacher-forced
                          // pass. Composes with a trailing-assistant prefill (the ids
                          // are appended after the prefill's rendered region).
  "renderer_name": null,  // exact renderer override for the native paths. Loom fires
                          // send the renderer recorded in the source turn's raw_meta so
                          // the re-rendered prompt is the one the prefix ids actually
                          // continued; null = normal selection (thinking toggle / run
                          // config / family recommendation).
  "panel": "p-1",         // which panel this chat belongs to (opaque id)
  "workspace_id": null,   // the workspace this chat belongs to. The browser always sends
                          // its own; the CLI omits it and inherits the bus's open one.
                          // Stamped on the chat_start/chat_done/chat_error broadcasts
                          // (workspace scoping, below); with parent_node set it is also
                          // where the server FOLDS.
  "parent_node": null,    // SERVER-AUTHORED FOLD placement (HANDOFF_SERVER_AUTHORITY
                          // §4.3): the id of the USER node the samples fold under at
                          // terminal. The writer persists that node as its OWN add_nodes
                          // op BEFORE the fire (send and regen are the same shape — the
                          // request carries no user content of its own; a pre-start
                          // failure can't lose the turn). Setting it makes the home
                          // workspace mandatory: explicit workspace_id → else the bus's
                          // open workspace → else 400. Also 400 with commit:false (a fold
                          // IS a commit). A parent that doesn't exist / isn't a user node
                          // is a pre-start `error` (SSE + bus chat_error), like an
                          // unsampleable run. null = legacy fire: nothing is
                          // persisted anywhere (P3 removed the echo commit too) —
                          // ephemeral lockstep, kept for bare param probes. See
                          // "Server-authored folds" below for the terminal sequence.
  "broadcast": true,       // also mirror samples to the state bus (browser)
  "detached": false,       // fire-and-forget: the POST returns immediately ({"status":
                           // "started"}) and the generation runs server-side, streaming
                           // ONLY to the bus. The browser sets this on every send so it
                           // does NOT hold the SSE — see "Detached mode" below. Requires
                           // broadcast. Stop reaches it via the cancel endpoint.
  "client_token": null     // optional opaque ownership token, echoed verbatim on the
                           // chat_start/chat_done/chat_error bus events; lets a client
                           // tell its OWN chats (blob-cache seeding via the terminal's
                           // `folded` manifest) apart from external ones (whose folds
                           // it adopts from the `ops` event, like any mutation).
}
```

**Response depends on `detached`:** the default (`detached:false`) returns the
**SSE stream below**. `detached:true` returns immediately with JSON
`{"status":"started"}` and NO stream — every event goes to the bus only.

### /api/chat SSE (the caller's stream — what the CLI prints)
- `event: delta` → `data: {sample_index, delta, kind}` — a streamed token chunk
  (`kind` = `"content"` | `"reasoning"`). Emitted **only for a token-streaming
  producer at n_samples==1**: `openrouter_model` and `vllm_model` (whose final
  `message` still carries `token_logprobs` + `raw_meta`). `run_id`, `base_model`, and loose
  `sampler_path` always sample native (whole samples, no deltas — see "Streaming
  model" below), and n>1 sends whole samples for every producer. A consumer that
  saw deltas for a sample uses the later `message` event to *finalize* (clean
  content), not to reprint.
- `event: message` → `data:` one sample: `{sample_index, content, raw_text, finish_reason, reasoning?, thinking?, token_logprobs?, loom_cut?, loom_text?}` or `{sample_index, error}`.
  `thinking` (bool) is present **only on `thinking:"both"` chats** and says which
  half produced the sample (false = non-thinking, true = thinking); single-mode
  chats omit it.
  `loom_cut` + `loom_text` are present **only on `continue_tokens` chats** (the
  loom): how many leading `token_logprobs` entries were FORCED (replayed prefix +
  picked alternative) and that prefix as frame-normalized display text — the
  browser folds both onto the node, tints the forced region like a prefill, and
  dot-underlines it in the token views.
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
  A node minted by an EDIT inherits the part of its original's stream the edit
  left alone, so a STORED stream may additionally end with a **ghost** entry —
  `{t, tid: -1, lp: null, ghost: true}`, the edited text past the divergence
  point, carrying no probability (`web/src/lib/token-edit.ts`). The sampler never
  emits one; consumers that read a stored stream must tolerate it (`lp: null`
  was already possible).
- `event: done` → `data: {}` — or, on a `parent_node` chat, the FOLD OUTCOME:
  `{folded: [{sample_index, node_id}], fold_rev}` when the server persisted the
  samples, `{fold_error: "<reason>"}` when it could not (workspace deleted /
  replaced mid-fire). The direct-stream consumer — the headless CLI — cannot
  read the bus or the server log, so this is its only persistence signal;
  `tinkpg` exits non-zero on a missing manifest after ≥1 completed sample.
- `event: error` → `data: {error, …}` (whole request failed, e.g. unsampleable
  run; a mid-stream producer fault after ≥1 completed sample also carries the
  fold-outcome fields above — partial data is real data and folds)

**Streaming model:** at n==1 only `openrouter_model` and `vllm_model` stream tokens
(vLLM streams the same ids + logprobs its whole-sample path returns, so nothing is
traded for the deltas — EXCEPT a `continue_tokens` fire, which needs
`prompt_logprobs` to score the forced prefix and vLLM refuses those on a stream,
so a vLLM loom / Continue arrives whole like a native one); n>1 keeps the
native batched fan-out (whole samples). tinker's native SamplingClient has no token
streaming — that's why the streaming n==1 path routes through the oai endpoint.
**`run_id`, `base_model`, and loose `sampler_path` always sample native for ALL n**
(no deltas), for response fidelity the oai wire can't give: `run_id` /
`sampler_path` because tinker's oai `/completions` serves the BASE model for a LoRA
sampler path (tinker-feedback#125); `base_model` because the `/completions` path
skips `renderer.parse_response` (channel-CoT families like gpt-oss leak thinking
into `content` with thinking off) and carries no `raw_meta` / `token_logprobs`.
A loose `sampler_path` has no local `config.json`, so its base model is resolved
from the tinker:// URI (`SamplerManager.resolve_base_model`) and it renders locally
just like a discovered run — same three artifacts.

### Server-authored folds (`parent_node` chats)

At a `parent_node` chat's terminal the SERVER folds every completed sample into
the workspace tree — the durable path a headless `tinkpg send -n 8` rides with
zero browsers attached:

- **What folds**: all non-error samples, as assistant siblings under
  `parent_node`, in sample-index order (`thinking:"both"` packs the non-thinking
  half 0..n-1 then the thinking half n..2n-1), the FIRST sibling selected. Each
  node's `content` is exactly what `_committed_turn` would have committed for
  that sample — prefill merge included — so the browser's live-bucket overlay
  and the folded node agree at fold time; `reasoning`/`raw_text`/`finish_reason`/
  `thinking`/`loom_cut`/`loom_text` copy verbatim, and `prefill` records the
  authored prefill when it reached that sample's half. Node ids are
  SERVER-minted (`nid()` format).
- **How it lands**: ONE `add_nodes {select}` op through the same locked apply +
  `ops` fan-out every client mutation uses — light nodes + write-once blobs
  (`token_logprobs`/`raw_meta`) in one write, `rev`++, one bus `ops` event a
  mirror replays like any other batch.
- **Ordering guarantee**: the fold's write + `ops` broadcast happen BEFORE
  `chat_end` (which releases `running` — bookkeeping only, no state patch) and
  before the `chat_done`/`chat_error` broadcast — fold data is present before
  any busy-surface lifts.
- **The terminal manifest**: the `chat_done` (or `chat_error`, when a partial
  fold happened) bus payload carries `folded: [{sample_index, node_id}, …]` +
  `fold_rev` — how a browser seeds its blob cache from the bucket sample each
  server-minted node came from. Positional zip against the op's nodes would
  shift on error samples; the manifest doesn't.
- **Partial terminals**: cancel or producer error with ≥1 completed sample
  folds what completed (partial data is real data); 0 samples folds nothing,
  no `folded` key, `rev` unmoved.
- **Failure is loud, never wedging**: a fold that can't land (workspace deleted
  mid-chat; tree replaced under it) is logged with the sample count and dropped
  — the terminal still fires, `running` still clears, and the reason travels to
  the CALLER stream as `fold_error` on the done/error SSE event (the bus
  terminal simply omits `folded`).

### PlaygroundState (server-side, shared)
```jsonc
{
  "panels": [                       // one entry per open panel, in display order
    {"id": "p-1",                   // stable panel id, minted p-<n>, never reused
                                    // within a workspace. A `panels` REPLACE (CLI
                                    // open/chat/compare) reuses the live ids and
                                    // mints any extra above the workspace's
                                    // panel_seq — see cli.py::_layout_panel_ids
     "run_id": null, "checkpoint": null,
     "thread_system_prompt": null}  // the active THREAD's system prompt, mirrored by the
                                    // browser — read by a mid-thread CLI send (inherit).
                                    // The per-panel transcript ECHO retired with P3: the
                                    // workspace TREE is the transcript (GET /api/workspaces),
                                    // and folds arrive as `ops` events
  ],
  "workspace_id": null,          // the workspace open in the browser (its ?c=)
  "system_prompt": null,            // the GLOBAL system-prompt part
  "system_enabled": null,           // power toggle for the global prompt (split-chip mute):
                                    // false = kept but MUTED (chat inherit skips it);
                                    // null = unset/legacy → behaves enabled. A patch setting
                                    // a NON-EMPTY system_prompt WITHOUT this flag auto-enables
                                    // (old-client shim: CLI `params --system` keeps applying)
  "temperature","max_tokens","n_samples","thinking","top_p",
  "chat_id": 0,        // increments each chat run; scopes sample events
  "running": false,
  "last_event","last_event_ts"
}
```
StatePatch = any subset of the *settable* fields (everything except
chat_id/running/last_event*). POST `/api/state` with a subset to drive selection
/ workspace / params. Panel routing: `panels` full-replaces the list;
`panel_thread_system: {panel_id: str|null}`
bulk-mirror per-panel fields without touching selection; `panel` + one of
`run_id`/`checkpoint`/`thread_system_prompt` targets a single
EXISTING panel (never auto-creates one).

### Workspace scoping on the state bus

There is ONE PlaygroundState per server **process** — that is what lets `tinkpg`
drive what you are looking at. But the fields split in two, and the split is
load-bearing:

| scope | fields | why |
|---|---|---|
| **workspace** | `panels` (incl. per-panel `run_id`/`checkpoint`/`thread_system_prompt`), `workspace_id`, `system_prompt`, `system_enabled` | persisted **with the workspace** and restored on open — a workspace IS its panel layout |
| **global** | `temperature`, `max_tokens`, `n_samples`, `thinking`, `top_p`, `chat_id`, `running`, `last_event*` | one knob for every panel and every client — the point of a shared bus |

The bus holds exactly **one workspace's** worth of the first group at a time,
identified by `workspace_id`. Three rules keep that honest:

1. **Server, anti-graft** (`api/state.py::_drop_foreign_workspace_keys`): a patch
   stamped with a `workspace_id` different from the bus's current one may only
   apply workspace-scoped keys if it also carries `panels` — i.e. it CLAIMS the
   bus. An incremental write from a non-owner keeps only its global fields. A
   patch with NO `workspace_id` key at all is treated as same-owner (that's the
   CLI, and any pre-scoping client); an explicit `workspace_id: null` is NOT the
   same as an absent key (routes/state.py uses `exclude_unset`).
2. **Server, anti-chimera** (same function, since 2026-08-06): a patch may never
   CHANGE `workspace_id` — including onto an unclaimed bus — without carrying
   `panels`. The stamp and the panels only ever move together, so the bus can
   never say "workspace B" over another workspace's panel list.
3. **Client** (`web/src/lib/bus-scope.ts::mergeBusState`): a client adopts
   workspace-scoped fields into its render mirror only from messages stamped
   with its own workspace; otherwise it keeps its own and takes the globals. It
   re-claims the bus on workspace open/switch/create and on window focus — so
   *the tab you last looked at is the one the terminal drives*.

**Layout ownership (since 2026-08-06).** The browser's authoritative copy of the
open workspace's panel layout is the workspace store's `ws.layout` — set from
the loaded body, mutated only by explicit panel edits or by bus messages
STAMPED with that workspace. Rendering, workspace saves, the `last_session`
pref, and every bus claim (open/switch/focus/re-prime) read `ws.layout`;
`live.state.panels` is only the CLI-visible echo. The mirror can transiently
hold another workspace's panels (bootstrap adopt of a foreign-claimed bus,
restart re-prime races) — with ownership inverted, that can no longer reach
disk or be re-published as a claim.

`/api/chat` carries `workspace_id` too (browser sends its own; CLI omits it and
inherits the bus's), and the `chat_start`/`chat_done`/`chat_error` broadcasts are
stamped from the **request**, not from the bus — with two tabs the bus can already
describe another workspace by the time a chat ends.

**Why this exists.** Without it, two browser tabs on two workspaces clobber each
other with no user action: tab A opens workspace X → pushes X's layout onto the bus
→ tab B mirrors it → B's `syncPanels` sees X's panel ids as new, calls `save()` →
B's workspace is persisted on disk with X's models. Four workspaces on the author's
instance were corrupted this way before it was diagnosed (2026-07-24); a restart
race that poisoned one tab's mirror corrupted a fifth on 2026-08-06 (the layout
ownership inversion + the anti-chimera rule close that class — full story in
`ENGINEERING_LOGS.md`). Tests: `tests/test_state_workspace_scope.py`,
`web/src/lib/bus-scope.test.ts`, smoke
`tests/small-smokes/browser_two_tab_workspace.py` (scenarios 5–6 pin the
inversion and were verified to fail on the pre-fix build).

### Sessions (`tinkerscope serve --multi-user`)

One PlaygroundState per **session** instead of per process (`api/session.py`,
`api/state.py::get_bus`). Everything above that is per-process state — the
workspace-scoped fields AND the global params, `chat_id`, `running` — becomes
per session; the durable store (workspaces, highlights, pins, prefs) stays one
store for everyone. Which session a request drives:

| transport | who |
|---|---|
| header `X-Tinkerscope-Session: <id>` | every browser fetch (`lib/session.ts` — one id per browser profile, localStorage, `?u=<id>` sets it and is stripped), `tinkpg --session` / `$TINKERSCOPE_SESSION` |
| query `?session=<id>` | the browser's EventSource (`/api/state/events`) — it cannot set headers |
| neither | resolved server-side: exactly one session has a live subscriber → that one (a bare `tinkpg send` keeps driving the human's screen); none → `default`; several → **409** naming the ids and `--session` |

Ids match `^[A-Za-z0-9_.-]{1,64}$` (400 otherwise). A server started WITHOUT
`--multi-user` maps every request to the one `default` session whatever it
sends, so the wire is exactly the pre-sessions one. Event routing: a chat's own
events (`chat_start` / `delta` / `sample` / `chat_done` / `chat_error`) go to the
bus of the session that fired it; the SHARED store's events (`ops`,
`workspace_deleted`) go to **every** session (`state.broadcast_all`), so two
people on one workspace converge while their sidebars stay their own. `chat_id`s
come from one process-wide counter, so `POST /api/chat/{chat_id}/cancel` is
unambiguous across sessions. The `last_session` pref is keyed per session
(`last_session@<id>`; the default session keeps the bare key, so packs and the
CLI-only seed are unchanged), with a read fallback to `last_session` so a new
person starts from the instance's defaults. Tests: `tests/test_sessions.py`;
smoke `tests/small-smokes/browser_multi_user.py` (run both legs — `MULTI_USER=0`
must reproduce the single-bus leak).

### /api/state/events SSE (the browser subscribes ONCE on load)
Event names = the message's `type`:
- `snapshot` → `{type:"snapshot", state}` (full state, sent first on connect)
- `patch` → `{type:"patch", event, state}` (state changed; e.g. event="chat_start"/"chat_done"/"patch")
- `chat_start` → `{type:"chat_start", chat_id, panel, n, label, client_token?, workspace_id?, thread_system_prompt?}` (a chat began; clear that panel's samples. `n` = TOTAL expected samples — 2×n_samples on a `thinking:"both"` chat. `workspace_id` = the chat's home workspace, stamped at fire time — a browser uses it only for bucket render hygiene now (a foreign-workspace chat on a reused panel id must not linger as an overlay); nothing folds browser-side. `thread_system_prompt` = the chat's RESOLVED thread part)
- `delta` → `{type:"delta", chat_id, panel, sample_index, delta, kind}` (streamed token chunk; only a token-streaming producer at n==1 — openrouter, NOT run_id / base_model / loose sampler_path which all render native — accumulate per chat_id/panel/sample_index, then the `sample` event finalizes)
- `sample` → `{type:"sample", chat_id, panel, sample_index, content, raw_text, finish_reason, reasoning?, thinking?}` (`thinking` only on `thinking:"both"` chats — which half drew this sample)
- `chat_done` → `{type:"chat_done", chat_id, panel, client_token?, workspace_id?, thread_system_prompt?, folded?, fold_rev?}` (`folded` + `fold_rev` appear iff a server-authored fold landed — the `[{sample_index, node_id}]` manifest (how the firing browser seeds its blob cache from the bucket) + the rev its `ops` event carried; see "Server-authored folds". The fold itself already arrived as that `ops` event — a terminal drives NO tree mutation in any client. A `parent_node`-less chat's terminal means nothing was persisted anywhere: no-placement fires are fully ephemeral (bucket render + caller stream only — the §9.4 accepted default; give a chat a home workspace if you want to keep it))
- `chat_error` → `{type:"chat_error", chat_id, panel, error, client_token?, workspace_id?, thread_system_prompt?}`
- `ops` → `{type:"ops", workspace, rev, ops:[…]}` (a workspace TREE changed — see the op protocol above. `ops` is the batch AS APPLIED: light node bodies only (heavy fields went to write-once blobs, nodes carry `has_token_logprobs`/`has_raw_meta` instead) and merged `set_meta` values. A mirror **applies every event in rev order, its own echoes included** — idempotent/LWW ops make replaying your own batch a no-op, and skipping it provably breaks convergence when two tabs contend. Match the `workspace` BEFORE checking the rev gap: gap-checking a foreign event either refetches for nothing or, worse, advances the local rev so the next genuine event looks stale. Any mismatch — a gap forward, or a rev that went BACKWARDS, which a pack install can do — means refetch the light body)
- `workspace_deleted` → `{type:"workspace_deleted", workspace}` (that workspace is gone — `DELETE /api/workspaces/{id}` succeeded). Its OWN event rather than an `ops` entry, because a deletion has no `rev` to ride: the workspace the rev would belong to no longer exists. A tab holding it open otherwise learns nothing and its next refetch 404s, which is indistinguishable from server trouble. Fires only on a real deletion (a 404 announces nothing). The store's delete is SOFT, so the id can come back via `workspaces/.deleted/`
- `ping` → `{}` (15s heartbeat; ignore)

**Live-drive model:** the browser renders selection + params + the workspace
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
+ one bus). Every browser fire carries `parent_node`, so the SERVER folds all n
samples at terminal (the `ops` event, before `chat_done`); the browser's terminal
job is seeding its blob cache from the bucket via the `folded` manifest. An
in-flight chat is told apart from an external one by `client_token`.

Two consequences of detached:
- **A closed tab no longer cancels a browser-fired generation** — it runs to
  completion server-side, exactly like a tinkpg-fired chat. "Stop all" (the cancel
  endpoint, by `chat_id`) remains the kill switch, reaching own AND foreign chats.
- The fold is now **deterministic on the single bus `chat_done`** (no drain racing
  it), so an aborted chat's already-completed partials fold reliably.

**Reload mid-generation.** The in-flight detached chats keep running server-side;
their folds land via `ops` events regardless of which pages exist (server-authored
folds carry no browser dependency). A reloaded page adopts the full n-sample
fan-out like any other mirror; a terminal missed during the brief EventSource
reconnect GAP is recovered by the reconnect rev-compare (summaries GET → body
refetch when the store moved), and the `snapshot` handler un-latches `busy` when
the server reports nothing running. Net: no stuck placeholders, no double-fold,
nothing lost. Smoke:
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
