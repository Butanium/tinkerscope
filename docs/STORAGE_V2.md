# Storage v2 — scaling conversations past the single-JSON design

STATUS: design approved 2026-07-13 (Clément ✓ "big backend refactor is fine — make the
codebase right for big workspaces"). This doc is the coordination contract between the
backend and frontend lanes; `docs/API_CONTRACT.md` gets updated as-built at the end.

## 1. Why (measured, not guessed)

Clément's real workspace, instance `f3f2391a6131`:

- `conversations.json` (ALL conversations, one file): **380 MB**.
- One conversation (`a1f2e12d`, 9 panels × ~30 nodes): **115.6 MB** serialized.
- Byte breakdown of its 251 nodes: **token_logprobs 89.8% (121 MB)**, raw_meta 6.9%
  (9.3 MB), raw_text 1.7%, content 1.6%. The *conversation itself* is ~4.5 MB.

Failure chain (all confirmed at file:line):
1. `GET /api/conversations` returns the whole store — every conversation WITH full
   trees (`routes/conversations.py:112`); the browser holds it all in `convo.list`
   forever (`conversations.svelte.ts:353`). ~380 MB JSON → GBs of JS heap at page load.
2. Every tree mutation → `$state.snapshot(ALL panels' trees)` + `JSON.stringify`
   (115 MB) + PUT (`conversations.svelte.ts:266,321`). Add-model additionally
   deep-clones the seed panel's tree first (`duplicateTo`, line 155) → the allocation
   spike that OOMs the tab.
3. The server `_read()`s + `_write()`s the whole 380 MB file per save.
4. Even layout-only changes (model swap, send-target toggle) ride the same
   full-conversation save; the state-bus echo + re-render cascade compounds it.

## 2. Target architecture

### 2.1 Node split: light tree vs heavy blobs

A tree node's heavy fields — **`token_logprobs` and `raw_meta`** — move out of the
tree into per-node **write-once blobs**. `raw_text` stays in the light tree (2.2 MB
total; woven into render fallbacks + continue/prefill paths). Light nodes carry
presence flags instead:

```jsonc
// light node (what trees contain everywhere from now on)
{ "id": "n1tnq2h", "role": "assistant", "content": "...", "raw_text": "...",
  "finish_reason": "stop", "parent": "...", "children": [...],
  "has_token_logprobs": true, "has_raw_meta": true }
```

Blob invariant: **write-once**. Logprobs/raw_meta never change after node creation
(edits/regens mint new nodes). Server skips writing a blob that already exists
(idempotent retries OK); blobs are deleted only with their conversation.

### 2.2 On-disk layout (per instance dir)

```
<instance>/
  conversations/
    <conv-id>.json          # light conversation: id, name, system_prompt, panels,
                            # reduced_panels, send_targets, seen_panels, timestamps,
                            # trees (light nodes only)
    <conv-id>.blobs/
      <node-id>.json        # {"token_logprobs": [...]?, "raw_meta": "..."?}
  conversations.json.legacy # the pre-v2 file, renamed after successful migration
```

No index file: the server builds an in-memory summary cache at boot (read each light
file once) and maintains it on writes. Single-writer: the server process is the only
writer for its instance dir (the CLI goes through HTTP — verified `cli.py:842`).
Keep the existing `.lock` convention around multi-file writes.

Per-conversation caching: keep parsed light conversations in an in-process dict,
invalidated on write. No more parse-380MB-per-request.

### 2.3 Migration (Clément's real data — safety first)

On boot: if legacy `conversations.json` exists and `conversations/` doesn't →
migrate: parse, split blobs out of every node, write per-conv files + blob dirs,
**verify** (per-conversation: node counts match, and light+blob bytes account for the
legacy bytes within JSON-formatting noise), then rename legacy → `.legacy`. Never
delete. Abort loudly (refuse to start) on any mismatch rather than proceeding with
partial data. Log progress (16 conversations, one is 115 MB — a few seconds).

### 2.4 Wire contract deltas (v2)

- `GET  /api/conversations` → **summaries**: `{id, name, created_at, updated_at,
  panels}` — no trees. `?bodies=1` → light bodies (trees incl., blobs excl.) — for
  the CLI's link/browse paths (`tinkpg` matches active-path content).
- `GET  /api/conversations/{id}` → one light body (NEW endpoint).
- `POST /api/conversations/{id}/node-blobs` body `{"nodes": ["n1", ...]}` →
  `{"n1": {"token_logprobs": [...], "raw_meta": "..."}, ...}` (NEW; POST because node
  lists can be long). Unknown node ids → omitted from the response, not an error.
- `PUT  /api/conversations/{id}/tree` — body's `trees` becomes a **partial upsert
  map** (only dirty panels), plus NEW `dropped_trees: string[]` for panel removals.
  Nodes in the body MAY carry inline `token_logprobs`/`raw_meta` (fresh folds): the
  server strips them into blobs and stores light nodes. Response unchanged.
- `PATCH /api/conversations/{id}` — extended beyond rename to accept any of
  `{name, system_prompt, panels, reduced_panels, send_targets, seen_panels}`:
  layout-only changes stop shipping trees entirely.
- `POST /api/conversations` (create) — unchanged shape; server splits blobs the same
  way. Smoke seeding (`tests/small-smokes/_seed.py`) keeps working as-is.
- `DELETE` — also removes the blobs dir.

### 2.5 Frontend memory policy

- `convo.list` holds **summaries only**. `switchTo` fetches the body
  (`GET /{id}`), drops the previous conversation's trees + blob cache.
- Trees become **`$state.raw`** — all mutation already flows through `tree.ts` →
  `convo.setTree` with wholesale ref replacement, so deep proxies are pure overhead.
  Lane must grep for any in-place node mutation and route it through setTree first.
- Save machinery: dirty-panel set (+ dropped set, + layout-dirty flag) instead of
  whole-map snapshot. Debounce + flush-on-switch semantics stay (see
  BRANCHING_DESIGN §6 — capture/flush ordering was subtle; with `$state.raw` trees
  are plain immutable objects, so no `$state.snapshot` needed at all, just refs).
  Layout-only dirt → `PATCH`, no tree bytes.
- NEW `lib/node-blobs.svelte.ts` store: reactive per-node blob cache;
  `ensure(convId, nodeIds[])` batch-fetches missing entries. Seeded locally at fold
  time (fresh samples already have the data in hand). Cleared on conversation
  switch. Consumers that today read `node.token_logprobs` / `node.raw_meta`
  synchronously and must go through the cache: `TokenLogprobs.svelte` (logprob
  view), the raw_meta disclosure in `ChatMessage.svelte`, ChartModal first-token
  mode (`chartByFirstToken` — needs blobs for all samples of the picked turn),
  `token-search`. Affordances (pills/disabled states) key off the `has_*` flags.
- State-bus diet: the `panels[].messages` / `panel_messages` transcript mirrors
  strip `token_logprobs` + `raw_meta` (the CLI renders text; verified it reads
  nothing else). **Do NOT touch the live sample-stream events** — detached fire
  delivers streaming samples through the bus and the owning browser folds full
  data from its bucket. Known accepted limit: a FOREIGN browser reconciling a fold
  from the echo gets light nodes; its logprob view lazy-fetches blobs once the
  owner's PUT lands (until then: the ordinary "no token data" pill).

## 3. Acceptance (the actual repro, as a smoke)

`scripts/dev-isolated.sh` snapshots the real state (incl. the 380 MB store → exercises
migration). Then, Playwright against the isolated instance:

- page load: `/api/conversations` response < 100 KB; open `a1f2e12d`: transfer
  ≈ light size (~5 MB), no tab crash;
- **add model on `a1f2e12d` succeeds** (< a few seconds, heap stays sane);
- model swap on a panel: no `/tree` PUT fires (layout PATCH only);
- logprob view on an OLD assistant turn still shows tokens (lazy blob fetch);
- chart first-token mode on an old turn works;
- existing smokes stay green; `uv run pytest -q`, `npm test`, `npm run check`,
  `npm run build` clean.

## 4. Out of scope (deliberately)

- Compressing blobs on disk (disk isn't the constraint; revisit if it becomes one).
- Render memoization / virtualized panels (reassess AFTER v2 lands — light trees may
  already make it moot).
- Any change to branching semantics (`tree.ts` untouched), sampling, discovery, or
  the fold/reconcile rules of detached fire.
