# Handoff — the conversations → workspaces wire/disk rename ("the magic wand")

Written 2026-07-17 from live working context (the session that shipped the
vocabulary half + `tinkpg send` + the ThreadSwitcher), deliberately WITHOUT a
fresh code sweep — items I did not verify this session are marked **VERIFY**.
Read this before starting the rename; it exists to transfer traps and locked
decisions, not to replace reading the code.

## What this is and why it's parked

The saved container (panels + their branch trees) is a **workspace**; a
branch-from-start first message starts a **thread** (a root sibling). The
UI/CLI/docs already say so (commit `eeba318`); the WIRE AND STORAGE still say
`conversation` — `/api/conversations`, `conversation_id` on the state bus,
`?c=`, the per-conversation files. This doc is the plan for finishing the job.

Decision record (don't relitigate):
- **Magic-wand answer is YES** — full naming consistency is worth it
  big-picture; legacy naming is a permanent reader tax. The block was never
  "whether", it's that this is a persistence migration and must be **its own
  deliberate pass, never piecemeal** alongside other work.
- **Thread stays "thread"** in code. A thread is colloquially a conversation,
  but do NOT reuse `conversation` as an identifier for threads — during the
  transition that word means "legacy wire name for workspace" and nothing else,
  or every diff becomes unreadable.
- Dialogue-meaning UI strings ("Copy the full conversation", the edit-fork
  hints in ChatMessage.svelte) deliberately KEPT the word — they describe the
  dialogue, not the container. Don't "fix" them.
- `tinkpg`: `conv` and `ws` both exist (`ws` currently the hidden alias).
  Which becomes primary at cutover is Clément's call; keep both working.

## Rename surface inventory (from context)

Backend:
- `src/tinkerscope/api/routes/conversations.py` — POST `/api/conversations`,
  GET (list, `?bodies=1`), PATCH `/{id}` (layout-only, `_PATCH_FIELDS`),
  PUT `/{id}/tree` (partial upsert + `dropped_trees`). **VERIFY** the node-blob
  endpoints' paths (they're per-conversation-scoped somewhere — batch POST from
  `node-blobs.svelte.ts`).
- `src/tinkerscope/api/conversation_store.py` — per-conversation files,
  in-memory `_summaries` cache (rebuilt from disk, `_build_summaries`),
  `_PATCH_FIELDS = (name, system_prompt, panels, reduced_panels, send_targets,
  seen_panels)`.
- `src/tinkerscope/api/state.py` — `PlaygroundState.conversation_id` (browser
  pushes its `?c=` id onto the bus; `tinkpg state`/`samples`/`send` read it).

Frontend:
- `conversations.svelte.ts` (exported as `conversations`, imported everywhere
  as `convo`), `save-plan.ts` (+ its tests), `api.ts`, `types.ts`
  (`Conversation`, `ConversationSummary`), `+page.svelte` §"Conversation ↔ URL
  sync" (`?c=`), `node-blobs.svelte.ts`.

CLI (`src/tinkerscope/cli.py`):
- `_conversations()` (GET `?bodies=1` — every consumer wants bodies),
  `_resolve_conv`, `cmd_conv`/`ws`, `cmd_state` (conversation_id → name + folds),
  `cmd_samples` (conversation_id → default target), `cmd_send` (conversation_id
  → fold set).

Disk (instance state dir, `~/.local/state/tinkerscope/...`):
- The per-conversation files + blobs from storage v2. **VERIFY** the exact
  subdir name and whether `instances.json` or anything else embeds it. The
  Conversation JSON's FIELD names (`panels`, `reduced_panels`, …) don't contain
  the word, so the disk migration should be directory/path-level only —
  **VERIFY** nothing stores `conversation` inside file contents.
- The live :8767 instance dir still has `.legacy` + `.bak` rollback artifacts
  from the storage-v2 migration (Clément's call to clear) — decide whether this
  migration's backup story subsumes or preserves them.

Smokes/tests:
- Many browser smokes seed via POST `/api/conversations` + open `?c=<id>`
  (kbnav, thread_switcher, cli_send, detached_reload, panel_foreign_fold,
  legacy_echo_graft, …). Aliases keep them green until the cutover commit
  updates them wholesale. `test_conversations.py`, `test_conversation_migration.py`,
  and the `store_real_migration.py` smoke are the storage-v2 migration
  precedent — model the new migration + tests on them.

Docs: API_CONTRACT (endpoint table, Conversation shape, the vocabulary note at
top — added 2026-07-17), STORAGE_V2, BRANCHING_DESIGN, HANDOFF_MULTIPANEL,
README, the skill (`.claude/skills/tinkerscope/SKILL.md` — in-repo, symlinked
from `~/.claude`), CLAUDE.md frontend map.

## Traps I hit or know about (the real payload of this doc)

1. **Phantom-panel self-heal in `#loadTrees`** (conversations.svelte.ts):
   panels with `run_id == null` are DROPPED on load (and their trees with
   them; if all are blank the first is kept). Bit my thread-switcher smoke —
   a seeded 2-panel workspace silently collapsed to one. Any touch of the load
   path must preserve this, and any migration test seeding panels needs real
   run_ids (smokes use `openrouter:openrouter/free`).
2. **Legacy `{tree, compare_tree}` bodies** are still handled in `#loadTrees`
   → `#fullTreeSaveNeeded` forces a FULL-map first save (partial upsert would
   drop the un-sent panel, and the server self-heals the legacy keys away).
   The rename migration is a natural point to migrate these for good — but if
   you don't, the handling must survive the rename.
3. **The save pipeline is the subtle part**: dirty-panel partial-upsert PUT vs
   zero-tree-bytes PATCH, planned by pure `save-plan.ts` (has tests). Keep the
   planner pure; rename its wire strings and its tests in the same commit.
4. **Old browser tabs across the deploy** keep POSTing old endpoints and
   pushing `conversation_id` onto the bus. Server must accept BOTH route names
   and BOTH field names for at least one deploy generation. The bus patch
   handler should mirror whichever field arrives into the canonical one.
5. **`?c=` deep links** live in browser history/bookmarks. Honor `?c=` on read
   forever (cheap); emit `?w=`. The +page URL-sync section also flashes a
   "workspace not found" notice on a stale id — that path reads the param.
6. **`seen_panels` gating** (`syncPanels` / `#applyPanelUi`): the panel-UI sets
   (`reduced_panels`, `send_targets`, `seen_panels`) restore from the loaded
   conversation and default NEWLY-seen panels into sendTargets. Renames around
   `_PATCH_FIELDS`/save-plan must keep these field names in sync on both ends
   (they are wire field names but don't contain "conversation" — they only
   move if you rename the parent shape's type, not the fields).
7. **In-memory `_summaries` cache** in conversation_store has a documented
   mid-insert crash gotcha ("dictionary changed size") — its rebuild reads disk
   OUTSIDE the lock, first-builder-wins. Don't restructure it casually while
   renaming; move it whole.
8. **Editable install**: CLI + server move together on this box (`uv tool
   install -e .`), so CLI/server version skew is a non-issue for Clément's
   instance — but a backend rename needs a **process restart** on :8767 (tmux
   `run-tscope`), unlike web-only changes (refresh) — don't deploy mid-generation.

## Staging plan (refined from the TODO entry)

1. **Aliases first, no disk change**: server registers `/api/workspaces/*` as
   primary and keeps `/api/conversations/*` as thin aliases; state bus accepts
   `workspace_id` + `conversation_id` (mirror on patch, emit both for one
   generation); `?w=` emitted, `?c=` honored. Frontend + CLI switch to the new
   names. Smokes: update to new names, ADD one alias-regression smoke that
   exercises the OLD names end-to-end.
2. **Disk migration**: rename the storage subdir (one-shot, with backup —
   follow the storage-v2 migration's shape and its `store_real_migration.py`
   test pattern against the dev-isolated snapshot). Content should be
   untouched (VERIFY per above).
3. **Internal symbol renames**: `conversations.svelte.ts` → workspace store,
   `Conversation` types, `conversation_store.py` module/file names, CLI
   internals. Pure refactor — svelte-check 0 errors, both test suites, full
   smoke sweep on dev-isolated.
4. **Cutover later or never**: the aliases are ~free; removing them is
   optional hygiene, not a goal.

Each stage = its own commit(s) + verification; the whole thing = its own
session, nothing else mixed in.

## Verification recipe

`scripts/dev-isolated.sh` snapshots the REAL instance state — it is exactly the
migration test bed (realistic workspaces incl. legacy-shaped ones, live
registry stripped). Full pass = `uv run pytest -q` + `npm test` + `npm run
check` + the browser smokes (at minimum: thread_switcher, cli_send,
conversation_url, detached_reload, legacy_echo_graft — the ?c=/seed-dependent
set) + the new alias-regression smoke, all against an isolated instance, before
touching :8767.
