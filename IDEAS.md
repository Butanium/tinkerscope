# Ideas parking lot

Non-roadmap ideas worth remembering. (Roadmap/committed follow-ups live in
`docs/TODO.md`.)

- **Availability auto-refresh.** The servable set is fetched once per scan and
  only refetched on the manual refresh button, so between refreshes the grey/⚠
  states drift stale in both directions (a deleted run shows live until
  refresh; a fresh retrain shows dead). A cheap TTL (say 10 min) or a refetch
  on the first send-404 would keep it honest without polling pressure. *(fable
  team-lead, 2026-07-20)* — *Update 2026-07-21 (baa9c37): the set now comes
  from the REST `list_user_checkpoints` sweep (truth-based, ~0.2s — the
  "rolling window" was a false theory, see the false-grey forensic), so drift
  is rarer (deletions/retrains only, no window churn) and the refetch is cheap
  enough to fire liberally; the send-404 trigger remains the natural hook.
  (fable, 2026-07-21)*

- **Browserless bare `--node`.** `samples --node <id>` resolves the workspace
  from the browser-open conversation; with no browser session it dies. Falling
  back to a grep-style all-workspace search would make node ids fully
  self-contained references (flagged by opus-cli-json while landing 0c9252f).
  *(fable team-lead, 2026-07-20)*

- **CLI: isolate one sample by its own node id.** `tinkpg samples --node
  <assistant-id>` shows the whole fan-out; isolating the named sibling needs
  eyeballing its position for `--sample K`. A `--this` flag (or making an
  assistant-id target default to just that sibling, fan-out via `--all`) would
  make the browser's "Copy node id" → terminal round-trip one paste. Flagged to
  team-lead 2026-07-20 during the toolbar/copy-node-id work; small, unbuilt.
  *(fable, 2026-07-20)*

- **README/skill mention of Copy node id.** The row toolbar's # button (copies
  a node id for `--node`) isn't in README §The CLI or the tinkerscope skill —
  both files carried another teammate's uncommitted work when it shipped, so
  the doc line was deferred. One sentence each: "node ids come from `tinkpg
  grep` or the row's # button in the browser". *(fable, 2026-07-20)*

- **Toolbar priority order → observed usage.** The fold order (Raw first, edit
  cluster, copies, send-to, copy-id last) is my judgment call. If Clément keeps
  expanding for one particular button, bump it — he was told to report; check
  in before redesigning anything here. *(fable, 2026-07-20)*

- **Per-row availability tooltip = the real reason.** The typeahead row's
  unavailable-tooltip is static generic copy ("base not served or weights no
  longer exist"), while the backend already sends the precise per-run
  `unsampleable_reason` (the sidebar warn uses it). Threading it through
  `ModelItem` (catalog builder → typeahead `title`) would tell you WHICH
  constraint binds at hover time. Small. *(fable, 2026-07-21)*

## From the 2026-07-21 MCQ-exploration session (fable, weird-personas)

All three SHIPPED 2026-07-21 in `7e90c24` (thread-system session landing): the
first two were the MCQ session's own uncommitted implementation (folded in per
its coordination note), the third is the thread-system feature itself.

- ~~**`tinkpg send --first-token`**~~ — shipped (`send`/`continue --first-token`).
- ~~**Probe battery runner**~~ — shipped (`tinkpg battery <dir>`; probe
  front-matter `system:` = that thread's prompt).
- ~~Thread-level system prompts~~ — shipped; as-built contract in
  `docs/API_CONTRACT.md` + `docs/BRANCHING_DESIGN.md` §2b.

- **`chat`/`compare` thread-prompt authoring.** Their `--system` still means
  the per-call GLOBAL part (they full-replace the layout, so their fresh
  thread resolves an empty thread part). If "fresh-history chat under a
  recorded prompt" turns out to be a real pattern, routing their `--system`
  through `_new_thread_system` like `send` is ~2 lines per command. Left out
  deliberately — no observed need yet, and the semantic change should follow
  a use case, not symmetry. *(fable, 2026-07-21, thread-system session)*

## From the 2026-07-23 Inkling / loose-ckpt session (opus-4.8)

- **Continuous thinking-effort slider for tml models (Inkling).** tml_v0 gates
  thinking with a continuous `effort` in [0, 1) (default 0.9), not a binary switch.
  This session mapped the existing binary thinking toggle to effort {0.0, 0.9} so
  "no think" works — but the model actually supports a *dial*. A per-panel effort
  slider (shown when `supports_thinking` is via the tml path, i.e. renderer name
  starts "tml") would expose real reasoning-budget control. Backend already threads
  `think: bool` → `_build_generation_prompt`; generalizing to `effort: float` is
  small (thread a float instead of a bool, or alongside). UI: a slider that appears
  for tml renderers next to the thinking toggle. *(opus-4.8, 2026-07-23)*

- **Show the resolved base model on loose-ckpt panels.** A loose `ckpt:` panel now
  resolves its base model server-side (`resolve_base_model`) but the label still
  reads just the UUID/checkpoint. The frontend could fetch + show the resolved base
  (e.g. "…final-step-100 · Inkling") and the `supports_thinking` flag, so loose
  ckpts get the same affordances (thinking toggle visibility, family label) as
  discovered runs. The value is on the backend already; it's a labeling/plumbing
  pass to surface it. *(opus-4.8, 2026-07-23)*

- **Gate the whole-conversation continue path by CAPABILITY, not renderer name.**
  `_continue_prompt` (tinker_sampler.py) routes tml_v0 through `_tml_continue` via
  `renderer_name.startswith("tml")`. That's brittle if another whole-conversation
  renderer (one whose `render_message` raises / no per-message assistant header)
  appears — it'd crash the old way. The same file already prefers capability probes
  (`_build_generation_prompt` checks `"effort" in inspect.signature(build_generation_prompt)`).
  A parallel probe — e.g. does `render_message` raise NotImplementedError, or a
  `renders_whole_conversation(renderer)` helper — would be more robust and consistent.
  Low effort; do it when a 2nd such renderer shows up (YAGNI until then). *(opus-4.8, 2026-07-23)*

- ~~**Layout history / undo for a workspace.**~~ **SHIPPED 2026-07-24.**
  `<state>/workspaces/<id>.layouts.jsonl` — `{ts, panels}` appended on every
  layout CHANGE (not every save), capped at 50, recorded in
  `workspace_store._record_layout` off the single `_persist` choke point. Read via
  `GET /api/workspaces/{id}/layout-history`; browse/restore with
  `scripts/layout_history.py`. Tests: `tests/test_layout_history.py`. Note it is
  NOT backfilled — history starts at the first layout change after this ships.
  *(opus-5, 2026-07-24)*

- ~~**A "suspiciously large layout change" tripwire.**~~ **SHIPPED 2026-07-24**
  alongside the history: `workspace_store._suspicious_layout_change` logs a
  warning when a save replaces a ≥2-panel layout with another ≥2-panel one
  sharing NO model — the clobber's shape, which no human action produces. Quiet
  for one-panel swaps, adds/removes, reorders, and blank→filled.
  *(opus-5, 2026-07-24)*

- **The scoping fix is a stopgap that HANDOFF_SERVER_AUTHORITY subsumes.** The
  root cause was "the browser is the sole writer of workspace state, and the bus
  is a process singleton". Workspace-stamping every message closes the corruption,
  but the ops protocol in `docs/HANDOFF_SERVER_AUTHORITY.md` would make it
  structurally impossible (the server owns the tree; a client can't write another
  workspace's anything). Worth a line in that doc when it's picked up: the
  stamping stays useful as the *addressing* layer for its P3 phase.
  *(opus-5, 2026-07-24)*

- **Split `+page.svelte` (2.2k) and `cli.py` (2.3k) — the two remaining
  mega-files.** Both have clean seams now. `+page.svelte`: the sidebar block
  (models + params + highlight rules) → `Sidebar.svelte`, and the composer row
  (prefill/system split-chips + send targets + textarea) → `Composer.svelte`;
  that's most of the markup and would leave +page as wiring. `cli.py`: the table /
  tree PRINTERS (`_show_workspace`, `_list_workspaces`, `_show_samples`, the state
  digest) are ~40% of the file and depend on nothing but dicts → `cli/_render.py`,
  leaving the commands thin. Neither needs a design decision, both are mechanical
  with tests already covering behavior. Clément flagged the cli.py size on
  2026-07-24. *(opus-5, 2026-07-24)*

## From the 2026-07-24 help-guide / layout-history session (opus-5)

- **The smokes are coupled to Clément's personal run directories.** 15 smoke
  files hard-code `ed_sheeran` / `weird-personas` / `negation_neglect` paths, so
  the suite only works on this box, and a fixture that moves breaks tests in a
  way that reads like a product regression (cost an hour this session; see
  `docs/TODO.md`). The magic-wand version: a tiny CHECKED-IN fixture tree —
  a handful of synthetic run dirs (`config.json` + `checkpoints.jsonl`, no real
  weights; `tests/conftest.py::_write_run` already builds exactly this for
  pytest) — and smokes pointed at it by default. Discovery needs no ML deps, so
  the fixtures cost nothing; only the genuinely-sampling smokes would still want
  real runs. Would also make the suite runnable by anyone who clones the repo.
  *(opus-5, 2026-07-24)*

- **Readiness waits should key on STRUCTURE, not data.** `browser_modals` waited
  for the string `ed_sheeran` to appear as its "page loaded" signal — true only
  when a run of that name happened to be the SELECTED model. It silently
  depended on scan roots and snapshot prefs and broke when either moved. Fixed
  there; the general rule is worth applying when touching any smoke: wait for
  `aside.sidebar` / `.model-dropdown-trigger` / a testid, never for content the
  smoke didn't create. ~15 smokes use `innerText.includes(...)` waits — most
  legitimately wait on content they seeded, but they're worth a glance when one
  starts failing mysteriously. *(opus-5, 2026-07-24)*

- ~~**A cheap "is anything else running?" preflight in `smoke.sh`.**~~ **SHIPPED
  2026-07-24** (cba3c59) — it warns on leftover *dev-isolated* instances only
  (detected via `XDG_STATE_HOME=…tscope-iso` in `/proc/<pid>/environ`), because
  warning on "a tinkerscope is running" would fire every run against Clément's
  own live instance and be tuned out immediately. Original note: Twice this
  session a smoke failed for environmental reasons — once from a concurrent
  sweep, once from a forgotten dev instance on another port eating CPU — and
  both times the failure looked exactly like the bug under investigation.
  `browser_workspace_url` is the reliable canary (10 s wait). The runner already
  takes a lock against sibling sweeps; it could also `pgrep` for other
  tinkerscope instances and print a loud warning before starting. Three lines,
  and it converts an hour of false diagnosis into a banner.
  *(opus-5, 2026-07-24)*

- ~~**Run ruff over all of `tests/` once.**~~ **DONE 2026-07-24.** `uv run ruff
  check` (default E4/E7/E9/F select) is now clean across `src/`, `tests/` and
  `scripts/`; the repo-wide command is in CLAUDE.md §Build/verify. The F subset —
  the class that ambushes commits via the hook — was already clean; the 15 errors
  found were pycodestyle style in old smokes. *(opus-5, 2026-07-24)*

## From the 2026-07-24 plugin session (opus-5)

- **Two sessions in one working tree is the repo's sharpest footgun, and a
  convention would close it.** Twice today I committed another session's
  in-progress work: once wholesale via `git add -A` (24 files, recoverable), once
  narrowly when a file I legitimately edited — `plugin/skills/guide/SKILL.md` —
  also carried their edits, which is NOT recoverable after the fact because the
  hunks interleave. Neither was carelessness about *which paths* to stage; the
  second one staged exactly one intended file. The structural problem is that
  `~/tools/tinkerscope` is a single checkout that several instances edit
  simultaneously while Clément talks to each of them, and nothing in the repo says
  so. Cheapest fix is a line in CLAUDE.md §Working conventions: before your first
  commit, `git status --short`; if files you don't recognise are dirty, another
  session is live — either work in a `git worktree` (there's already
  `.claude/worktrees/`, and the harness has `EnterWorktree`) or `git diff --stat`
  every file you stage and check the line count is yours. A stronger version is a
  pre-commit hook that warns when the staged set overlaps files modified since the
  session's start, but the convention alone would have caught both of today's.
  *(opus-5, 2026-07-24)*

## From the 2026-07-24 help-modal / tooltip session (opus-5)

- **Finish the icon consolidation.** `lib/Icon.svelte` now owns the row-toolbar +
  sidebar glyph set (so the `?` modal draws the SAME button the toolbar does),
  but ~22 inline `<svg>` remain outside it — and the drift it exists to prevent
  is already there: `HighlightRules.svelte` draws its own pencil
  (`M11.5 2.5l2 2L6 12…`) and trash, geometrically different from Icon's `edit`
  / `trash`, for the same two verbs. Worth folding those in (plus +page's
  reduce/restore/panel-send arrows). Chevrons and the drag grips are structural,
  not iconography — leave them. *(opus-5, 2026-07-24)*

- **Lint the tooltip length rule.** CLAUDE.md now says tooltips are ONE short
  line (~70 chars) because a long one renders as an ugly slab over the UI, but
  nothing enforces it and the six worst offenders had accreted quietly over
  months. A ~20-line node test in `web/src/lib/` (or a pre-commit grep) that
  parses `data-tooltip="..."` literals out of `web/src/**/*.svelte` and fails
  over ~90 chars would catch the next one at write time. Ternaries need care —
  measure each branch, not the whole expression. *(opus-5, 2026-07-24)*

- ~~**The `?` modal has no visual regression net**~~ — done in the same session:
  `browser_help_modal.py` now asserts every `.help-chip` actually rendered an
  `svg` (a name `Icon.svelte`'s `{#if}` chain doesn't know emits NOTHING, and
  `npm run build` doesn't typecheck, so `npm run check` was the only guard).
  *(opus-5, 2026-07-24)*

