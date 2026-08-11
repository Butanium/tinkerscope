# Changelog

Notable changes per release. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are
[semver](https://semver.org/) with PEP 440 pre-release suffixes.

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

First PyPI release. Static site export + `?w=<pack link>`, the human-facing help
modal and guide skill, the skills shipped as an installable Claude Code plugin,
chart think/no-think splitting and view persistence, `--baseline` smoke
verification, and the `scripts/smoke.sh` serial runner.

## [0.9.0] — 2026-07-24

The conversations → workspaces rename across wire and disk (`/api/workspaces`,
`?w=`, `tinkpg ws`), with migration. See `docs/MIGRATIONS.md`.

## Earlier

`v0.1.0` and the pre-rename history are in the git log; `ENGINEERING_LOGS.md`
carries the narrative behind the decisions.

[1.1.0a1]: https://github.com/Butanium/tinkerscope/compare/v1.0.0...v1.1.0a1
[1.0.0]: https://github.com/Butanium/tinkerscope/compare/v0.9.0...v1.0.0
[0.9.0]: https://github.com/Butanium/tinkerscope/compare/v0.1.0...v0.9.0
