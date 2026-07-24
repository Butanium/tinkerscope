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

- **Layout history / undo for a workspace.** What made the cross-tab clobber
  (fixed in 574a207) *frightening* rather than merely annoying was that there was
  no undo: the panel layout was overwritten in place and only recoverable because
  `raw_meta` happens to record each node's sampler. A tiny append-only
  `<id>.layouts.jsonl` — `{ts, panels}` on every layout-changing save, capped at
  ~50 entries, a few hundred bytes each — would make any future layout accident a
  one-command restore instead of a forensic exercise. Cheap (the save path already
  knows when the layout changed: `#layoutDirty`), and it generalizes: the same
  file answers "what models did this workspace use last week?". *(opus-5, 2026-07-24)*

- **A "suspiciously large layout change" tripwire.** The clobber replaced 5 panels
  at once with a different 10 — a shape no human action produces. The server could
  log a warning when a PATCH replaces the whole panel list with a disjoint model
  set (`|old ∩ new| == 0` and both non-trivial). It would have caught this bug in
  June, from a log line, with no invariant checking of tree contents. Pair with
  `scripts/repair_panel_layouts.py`, which is the after-the-fact version.
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

- **Run ruff over all of `tests/` once.** The pre-commit hook lints only STAGED
  Python, so smoke files that nobody has touched in months carry latent errors
  (an unused `import math` in `browser_storage_v2_monster.py` surfaced only because
  the rename sweep staged it, and it aborted a commit mid-release). One cleanup
  pass + a `ruff --select F tests/` in CI-of-the-mind would stop that ambush.
  *(opus-5, 2026-07-24)*
