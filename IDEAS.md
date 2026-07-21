# Ideas parking lot

Non-roadmap ideas worth remembering. (Roadmap/committed follow-ups live in
`docs/TODO.md`.)

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
