# Migrations

One entry per release that moved the wire or on-disk shape. Each says what
changed, what runs automatically, and how to roll back. See `docs/RELEASING.md`
for the versioning policy.

---

## v1.0.0 — `conversations` → `workspaces` (2026-07-24)

### What changed and why

The saved container is a **workspace**: a set of panels, each with its own model
and its own branch tree. It stopped being "a conversation" the day it grew more
than one panel — a workspace holds several parallel conversations, and a
branch-from-start **thread** is a third thing again. The UI, CLI and docs were
corrected in 2026-07-17; the wire and disk kept the old word until now, which
made every reader translate as they went. v1.0.0 finishes the job.

Deliberately a **clean cut, not an alias layer**: this is a single-user tool with
an editable install where server, CLI and browser bundle move together, so
maintaining two vocabularies would cost more than it buys. The one exception is
`?c=` (below), where the cost is two lines and the benefit is that open tabs and
bookmarks keep working.

### Wire

| before | after |
|---|---|
| `/api/conversations…` | `/api/workspaces…` |
| `conversation_id` (state bus, `/api/chat`, `chat_start`/`chat_done`/`chat_error`) | `workspace_id` |
| `?c=<id>` | `?w=<id>` — **`?c=` is still read**, then rewritten to `?w=` |
| `tinkpg conv` | `tinkpg ws` — `conv` kept as a hidden alias |

The old routes are **gone** (404), and the old field names are **not** accepted.
A browser tab left open across the deploy will fail its API calls until reloaded;
reload every tab after upgrading.

### Disk

    <state>/<instance>/conversations/        →  <state>/<instance>/workspaces/
    <state>/<instance>/conversations/<id>.json      →  workspaces/<id>.json
    <state>/<instance>/conversations/<id>.blobs/    →  workspaces/<id>.blobs/

**Automatic**, on first boot: `workspace_store._migrate_dir_locked()` renames the
directory when `conversations/` exists and `workspaces/` does not, and logs

    v1.0.0 migration: conversations/ → workspaces/ (directory rename; contents untouched)

**File contents are not touched.** No field inside a stored body contains the old
word (`id`, `name`, `system_prompt`, `panels`, `reduced_panels`, `send_targets`,
`seen_panels`, `trees`), so this is a rename and nothing else. It runs *before*
the storage-v2 migration, which keys off `workspaces/` existing.

Untouched by design: `conversations.json.legacy` and `conversations.json.bak-*`
keep their historical names — they are pre-storage-v2 artifacts, only ever read
by the v2 migration.

### Rollback

The migration is a directory rename, so the inverse is one command against the
instance's state dir (find it via `GET /api/health` → the state dir is keyed by a
hash of the scan roots):

```bash
# stop the server first
mv ~/.local/state/tinkerscope/<instance>/workspaces \
   ~/.local/state/tinkerscope/<instance>/conversations
git checkout v0.1.0   # and restart
```

No data conversion means no lossy step to undo. This is also why the migration
does not copy: the stores run to hundreds of MB, and a copy on the persistent
volume would turn every first boot into a multi-minute stall.

### Verification performed

- `uv run pytest -q` (182), `npm test`, `npm run check` (0 errors).
- A `scripts/dev-isolated.sh` instance launched on a **snapshot of the real
  state**: migration logged, 24 workspaces served from `workspaces/`,
  `/api/conversations` → 404.
- Browser smokes against that migrated instance: `browser_workspace_url`
  (incl. a new case asserting the legacy `?c=` still opens the workspace and is
  rewritten to `?w=`), `browser_two_tab_workspace`, `browser_thread_switcher`,
  `browser_state_reprime`, `browser_kbnav`, `browser_thread_system`,
  `browser_row_toolbar`, `browser_legacy_echo_graft`, `browser_sysprompt_switch`,
  `browser_system_chip`.

### What kept the old word, on purpose

"Conversation" still means a **dialogue** — that reading is correct and was left
alone: the UI strings ("Copy the full conversation", the shift-edit fork hints),
`tinker_sampler.py`'s renderer comments ("tml_v0 renders whole conversations"),
and `docs/HANDOFF_WORKSPACE_RENAME.md`, which is the historical plan and
describes the pre-rename state.
