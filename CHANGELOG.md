# Changelog

Notable changes per release. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are
[semver](https://semver.org/) with PEP 440 pre-release suffixes — on the DATA +
WIRE contract, not on the UI (`docs/RELEASING.md`).

`docs/MIGRATIONS.md` covers every release that moved the wire or on-disk shape;
`ENGINEERING_LOGS.md` carries the dated narrative behind the decisions.

## [1.1.0a1] — 2026-08-10

Pre-release. Three new surfaces (loom, undo, cross-workspace search) plus the
checkpoint-by-path entry point. 42 commits since 1.0.0.

### Added

- **The loom** — click any token in an assistant turn and branch into one of its
  recorded alternatives. Replays the stored token prefix through
  `ChatRequest.continue_tokens`, anchored to the turn's own model + renderer, so
  the branch is token-exact rather than a text re-prompt. Continue rides the same
  path when the stream has full coverage and the panel still points at the
  producing model, falling back to text prefill otherwise. The forced prefix is
  tinted like a prefill in both token views.
- **Undo for destructive edits** — sidebar ↺ and Ctrl+Z over delete-branch /
  delete-sample / discard-others / reset-thread. Per workspace, capped at 50,
  cross-panel deletes collapse to one press. Additive operations (send, edit,
  regenerate) are deliberately excluded, so Ctrl+Z always means "put back what I
  just removed".
- **Server-side trash journal** — deleted subtrees are journaled at the
  persistence choke point and survive the browser that deleted them. Entries
  record each vanished subtree's sibling index so a restore doesn't silently
  reorder the ‹k/N› cyclers. `DELETE /api/workspaces/{id}` is now soft (moved to
  `workspaces/.deleted/`), since the journal lives inside the workspace.
- **Ctrl+K cross-workspace search** — new server engine (`api/search.py`, cached
  linear scan over the light trees) behind a palette with results grouped by
  workspace, collapsed sibling fans, and jump-and-reveal onto the target node.
  Scope chips narrow the search server-side. The static site runs the same
  semantics client-side via `lib/search-scan.ts`.
- **Node deep links** — `?w=…&node=…&panel=…` reveals a specific node on load;
  `tinkpg grep --link` and `tinkpg node --link` print them.
- **samplescope hand-off** — ⇧ on a panel's copy button yields the run's training
  JSONL path; Ctrl+⇧ opens it directly in a samplescope instance, starting one
  over the scan root if none covers the file. The request carries a run id, never
  a path.
- **Add a checkpoint by path** — a query in the Tinker picker that looks like a
  sampler path offers an add-custom row with a live availability probe
  (spinner / green + base model / red + tinker's own detail).
- **Name an unnamed checkpoint** — any checkpoint the registry only has a derived
  `<hex> · <segment> · <date>` label for can be given a real name. Stored
  server-side, so it survives a restart, reaches `tinkpg`, and travels in
  `pack export`.
- **CLI** — `tinkpg url`; workspace selectors accepted as `--ws` everywhere.

### Changed

- Panel ids are monotonic and never reused; copy-node-id emits `<panel>:<node>`.
- Panel-layout ownership inverted to the workspace store: `live.state.panels` is
  now a CLI-visible echo that nothing reads back for rendering or persistence.
  Only bus messages stamped with the open workspace may drive layout.
- Token views gained a three-way tint mode (surprisal / highlight-match / both).
- The training-data peek modal and `/api/load-dataset` are gone, replaced by the
  ⇧/Ctrl+⇧ copy-button routes above.
- `IDEAS.md` became an `ideas/` folder, one file per idea; `ENGINEERING_LOGS.md`
  now carries the dated forensics that used to live in code comments.

### Fixed

- Cross-tab panel-layout clobber (second round; see the ownership inversion
  above).
- Legacy `{tree, compare_tree}` workspaces opened blank.
- Add-panel was gated on model availability.
- Dead `?node=` double-reveal guard, and kbnav operating on a borrowed model.
- A literal NUL byte in `+page.svelte`'s node-link key.

### Known limitations

- The `[tool.uv.sources]` pin to the `tinker-cookbook` fork (for the
  `tml_v0_disable_thinking` renderer) does not travel in published metadata — it
  is a uv workspace-local key. Installing from PyPI resolves upstream
  `tinker-cookbook`, so Inkling checkpoints fall back to the `role_colon`
  renderer. Resolved once thinking-machines-lab/tinker-cookbook#839 merges.

## [1.0.0] — 2026-08-05

First PyPI release — `uv tool install tinkerscope`. The release that makes a
setup shareable: a workspace can be published as a read-only static site, and a
share pack installs from a link. No wire or disk change. 66 commits since 0.9.0.

### Added

- **Static site export** (`tinkerscope site export`) — a self-contained
  read-only build of the current state. Each `data/*.json` is shaped like the
  endpoint it stands in for, so the frontend's static transport has no special
  cases; index.html is rewritten twice (absolute asset refs AND the router's
  `base`) or a GitHub Pages subpath 404s its own route. Read-only is enforced at
  the markup level — a hidden control is absent, not disabled.
- **`?w=<pack path|url>`** — a published site doubles as a general reader for
  anyone's pack: fetch, parse, install into a per-site overlay (IndexedDB, after
  localStorage's ~5 MB cap made a real workspace uninstallable — and failed
  quietly). A preview reports colliding ids and the modal asks replace-or-keep-
  both; a non-colliding install on a static site just proceeds, while a LIVE
  instance always asks, since there `?w=<path>` makes the server read the
  filesystem.
- **`site export --pack-link`** — publishes the pack alongside the site, so a
  `?w=<id>` link the visitor doesn't have resolves through `manifest.pack_links`
  and installs behind a progress modal instead of flashing "not found".
- **"Open locally"** — the read-only badge is a button, and it hands out the
  `uvx … --pack <url>` command that gets a reader from reading to sampling. With
  no pack URL it says so, rather than printing something that looks like it
  should work.
- **The `?` help modal + the `guide` skill** — the two human-facing descriptions
  of the browser UI, updated together with any UI change. Every button the modal
  names is drawn with its real glyph, so the legend can't drift from the toolbar.
- **The skills ship as an installable Claude Code plugin**, with the repo as its
  own marketplace. `plugin.json` declares no `version` on purpose: the plugin
  cache is keyed by commit SHA when it's absent, and by `version` when present —
  which would strand consumers on a stale copy.
- **Chart think/no-think split** — a turn that mixes both draws one bar per
  population over disjoint samples, composing with the response|thinking
  match-scope split for up to four bars per model. Plus **match on the first N
  chars only** (with the inspector dimming what fell past the cut) and **view
  persistence** — how-you-look-at-it picks global, question-specific state per
  workspace, mirrored into server prefs so a static export carries the view its
  author set up.
- **Token probabilities as an overlay** — the heat paints *under* the normal
  markdown (one canvas per prose container, the token stream aligned to the
  rendered text) instead of replacing the prose; the raw dump stays as the third
  state of the toggle. Alignment below 50% coverage paints nothing and says so.
- **An edit keeps the logprobs it didn't touch** — text before the divergence was
  generated under the same context, so it keeps the model's numbers; the rest
  becomes one dimmed ghost with no probability, rather than an all-ghost stream
  that would look like evidence.
- **Highlights master Off/On** — a gate over the coloring, never a bulk edit: it
  writes no rule's `enabled`, so flipping it back restores exactly the set that
  was painting.
- **A per-turn eye** that spreads a turn's sibling distribution into sample
  cards, and a **per-panel stop chip** on the streaming turn itself — panels
  follow-scroll while streaming, so a stop button in the row head is off-screen
  exactly when it's wanted.
- **Foldable sidebar sections** — sampling params, View and Highlights, chevron
  first.
- **CLI** — `tinkpg node` (reverse lookup from a bare node id to its record +
  blobs), `tinkpg threads` (cross-workspace thread index), `ws --json` /
  `--thread` / `--deepest`, `samples --deepest`, and `probe` (sample any model
  without touching a workspace). `--json` output stopped being truncated.
- **`scripts/smoke.sh`** — serial, locked, self-cleaning browser-smoke runner
  (smokes must not run concurrently: one restarts a server mid-run), with
  **`--baseline <ref>`**: run the working tree's smoke against the app at an
  older ref, so a smoke written for a fix has to prove it can fail first.

### Changed

- The workspace picker became the same type-to-filter combobox as the model
  picker.
- Thinking blocks unfold by default; the copy button covers base models, not just
  run checkpoints.
- A panel-layout accident is undoable (layout history).
- `dev-isolated.sh` snapshots to `/var/tmp` and self-reaps.
- `ruff check` is clean across the whole repo, tests included.

### Fixed

- The chart inspector re-pointed at a different bucket whenever the plot
  re-derived mid-stream — bars come and go per streamed sample, so the inspected
  bar is now addressed by a stable ref instead of an index.
- `site export --workspace` published pins (with their local `dataset_path`) and
  chart state belonging to the workspaces it had just excluded. Pins carry no
  workspace id and can't be scoped at all.
- The pack-id dedupe rule had diverged between the Python and browser installers;
  both now share one rule, with tests on each side.
- The topbar connection dot kept claiming connected after the event stream died —
  it now degrades on error and on a missed heartbeat.

## [0.9.0] — 2026-07-24

The `conversations` → `workspaces` rename, across wire and disk. A **clean cut,
not an alias layer**: on a single-user tool with an editable install, where
server, CLI and browser bundle move together, maintaining two vocabularies costs
more than it buys — and a major bump is the licence to drop back-compat provided
the migration is written down. Full detail, verification and rollback in
`docs/MIGRATIONS.md`.

Shipped as `v1.0.0` and retagged `v0.9.0` on 2026-08-05, when that number was
reassigned to the first PyPI release — this one never shipped anywhere but the
author's box. `docs/MIGRATIONS.md` and the boot-progress line still say v1.0.0.

### Changed

- **Wire** — `/api/conversations…` → `/api/workspaces…` (old routes 404),
  `conversation_id` → `workspace_id` (state bus, `/api/chat`, the `chat_*`
  broadcasts), `?c=` → `?w=` (`?c=` is still *read*, then rewritten, so open tabs
  and bookmarks keep working), `tinkpg conv` → `tinkpg ws` and `--conv` → `--ws`,
  both old names kept as aliases. Old field names are not accepted; a tab left
  open across the upgrade fails its API calls until reloaded.
- **Disk** — `<state>/conversations/` → `<state>/workspaces/`, automatic on first
  boot. A directory rename and nothing else — no stored field contains the old
  word — so rollback is the inverse `mv`. It runs *before* the storage-v2
  migration, which keys off `workspaces/` existing. `conversations.json.legacy`
  keeps its historical name.
- **Code** — `routes/conversations.py` → `routes/workspaces.py`,
  `conversation_store.py` → `workspace_store.py`, `lib/conversations.svelte.ts` →
  `lib/workspaces.svelte.ts` (`convo` → `ws`), the `Conversation*` types, the API
  client methods and the `.conv-*` CSS classes.

"Conversation" was kept wherever it still means a **dialogue** — the UI strings,
the renderer comments, and the handoff docs that describe the pre-rename state.

## [0.1.0] — 2026-07-24

The pre-rename tool: from the fork of Harry Mayne's playground (2026-06-18) to
the eve of the workspaces rename, 216 commits. Everything here speaks the old
vocabulary on the wire and on disk (`/api/conversations`, `?c=`).

### Added

- **Auto-discovery** — scan a directory tree for `tinker_cookbook` runs
  (`config.json` + `checkpoints.jsonl`), with defensive parsing and sampleability
  gating. Sampling goes to `sampler_path`, never `state_path`.
- **Native tinker sampling** — direct SDK calls: renderer selection, the thinking
  on/off toggle across both naming conventions, thinking-block parsing, prefill,
  per-sample streaming and cancel-on-disconnect. Base models and loose `ckpt:`
  paths render native too, so they keep raw_meta / logprobs / a real thinking
  parse. Model pickers cover tinker base models and the OpenRouter catalog.
- **Conversation branching** — every edit, regenerate and n>1 sample is a sibling
  in a tree: ‹k/N› cyclers, delete, discard-others, send-branch-to-panel, and
  branch-from-start threads. All tree ops in one tested pure module.
- **N-way model comparison** — `panels[]` with a tree each, add / remove /
  reduce, composer send-targeting, per-workspace layout, grip-drag reorder.
- **The distribution chart** — n samples bucketed by highlight rule (grey none /
  solid single / striped combo), turn picker, match scopes, per-rule
  include-exclude chips, a thinking filter, and click-a-segment-to-inspect.
  **First-token mode** reads the model's own probability distribution over the
  first generated token from stored logprobs, with exclude / renormalize /
  add-a-recorded-but-hidden-token / drag-merge.
- **User-defined highlight rules** — ported from samplescope, with a color-wheel
  dab and auto-derived names; saved samples became **pins**.
- **Per-token logprobs** — capture on by default, a hover inspector with top-K
  alternatives, and color-by-highlight-match as an alternative to the surprisal
  tint.
- **Prefill and continue** — seed an assistant turn (thinking and/or response),
  persisted across reloads, think-only mode, per-sample Continue, and a
  truncated-by-max-tokens badge. Shift+Continue resumes *inside* the think block.
- **Storage v2** — one `conversations.json` OOM'd the browser, so state split
  into per-conversation files plus write-once heavy node blobs: the list holds
  summaries, bodies load on open, saves ship only dirty panels. Migration is
  automatic and verified by deep-compare. See `docs/STORAGE_V2.md`.
- **Share packs** — one portable YAML bundling checkpoints, default params and
  workspaces, so a collaborator reproduces a setup against public checkpoints
  with no local run dirs (`tinkerscope pack export`, `tinkerscope --pack
  <file|url>`).
- **The `tinkpg` CLI over a live-drive bus** — a shared state bus puts terminal
  and browser in lockstep: `ls`, `open`, `chat`, `send`, `continue`, `samples`,
  `compare`, `battery`, `grep`, `state`, `conv`, `params`, with `--json` output
  and node/thread addressing. The skill documenting it is vendored in-repo.
- **Inkling / `tml_v0` support** — the thinking toggle mapped onto its `effort`
  directive, whole-conversation continue, and tolerance for its non-HF tokenizer.
- **UI** — keyboard row navigation, a scroll-policy store (follow / preserve /
  snap) replacing the global bottom-pin, the adaptive row toolbar,
  tail-preserving and diff-view labels for sibling runs, typo-tolerant model
  search, the cross-panel thread switcher, an auto theme, and the raw
  request/response + tokenizer-debug views.
- **Detached fire** — the browser's send returns immediately and renders from the
  bus, so more than four panels don't exhaust the browser's ~6-connection cap.

### Fixed

- **Sampler availability was read from the OpenAI-compatible `/v1/models`
  listing, which is hard-capped at ~20 checkpoints** — older but perfectly live
  runs were greyed out as "aged out", and the inference endpoints served them
  fine all along. Availability now comes from the REST `list_user_checkpoints`
  sweep, so `sampleable` can be trusted.
- Panel layout leaked across tabs through the process-global state bus (round
  one — scoped to its workspace here; the ownership inversion in 1.1.0a1
  finished the job), with `scripts/repair_panel_layouts.py` to audit and repair
  layouts from node blobs.
- Stop actually stops: a guaranteed terminal event plus cancel-by-id, and a
  0-sample cancel renders as a neutral "stopped" strip rather than an error.
- The browser re-primes the live bus after a backend restart instead of mirroring
  an amnesiac one.
- A phantom `run_id=null` panel resurrected on every send.
- Send-branch→panel grafted onto the destination tree instead of overwriting it.
- Checkpoint step sorting parsed the global step from the name, not the batch.

[1.1.0a1]: https://github.com/Butanium/tinkerscope/compare/v1.0.0...v1.1.0a1
[1.0.0]: https://github.com/Butanium/tinkerscope/compare/v0.9.0...v1.0.0
[0.9.0]: https://github.com/Butanium/tinkerscope/compare/v0.1.0...v0.9.0
[0.1.0]: https://github.com/Butanium/tinkerscope/releases/tag/v0.1.0
