# Ideas parking lot

Non-roadmap ideas worth remembering. (Roadmap/committed follow-ups live in
`docs/TODO.md`.)

- **Availability auto-refresh.** The servable window (48e8dbd) is fetched once
  per scan and only refetched on the manual refresh button — but the window
  moves whenever a retrain lands or checkpoints age out, so between refreshes
  the grey/⚠ states drift stale in both directions (a dead run shows live until
  refresh; a fresh retrain shows dead). A cheap TTL (say 10 min) or a refetch
  on the first send-404 would keep it honest without polling pressure. *(fable
  team-lead, 2026-07-20)*

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
