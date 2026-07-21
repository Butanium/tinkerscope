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
