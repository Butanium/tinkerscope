# Ideas index

Non-roadmap ideas worth remembering, one file per idea. (Roadmap / committed
follow-ups live in `docs/TODO.md`.) Was a single `IDEAS.md` until 2026-08-06 —
481 lines with implemented entries struck through in place; same shape as
`~/.claude/ideas/`, which is the reference for this layout.

- **New idea** → `ideas/<short-slug>.md`, starting with a `##` title, signed and
  dated at the bottom. Add a line to the index below, under whichever cluster
  fits (a new cluster is fine).
- **Implemented / resolved** → `git mv` into `ideas/done/`, append a
  `**Done YYYY-MM-DD**:` line saying what shipped, and move its index line out of
  here into `done/CLAUDE.md`. Don't strike text through — this index lists only
  what's still open.
- **Dead / rejected** → delete the file and its index line. Git history keeps it.
  A rejection whose *reasoning* is worth preserving goes to `done/` instead.

## Open

### Token probabilities & the loom

- [Loom directly from an n>1 sample card](loom-on-sample-cards.md) — v1 is single-row only; cards need a sample-index onLoom variant (the onTag shape); wait for someone to reach for it
- [A text bar in the pinned popover: continue from words you type](loom-freetext-continuation.md) — Clément's; the replay half already ships, the new piece is server-side tokenization under the turn's own tokenizer. Wants to land with [score-authored-ghosts](score-authored-ghosts.md) so the typed text gets real logprobs
- [`tinkpg loom`](cli-loom.md) — the backend surface exists, the CLI verb doesn't; agents can see fork-worthy positions via `tinkpg node` but can't act on them
- [Score the CONTEXT, not just the completion](score-the-context.md) — `[0, L)` is discarded teacher-forced numbers we already paid for; design deliberately unsettled, Clément wants to think more
- [Teacher-force the ghosts: score authored text](score-authored-ghosts.md) — Clément's loom variant: an edit/prefill gets real logprobs ("not sampled" ≠ "no data"), surprising insertions glow orange; the loom endpoint is the primitive, only trigger + per-entry provenance remain
- [Make the top-K logprob capture configurable](topk-capture-configurable.md) — `TOPK_LOGPROBS = 5` means Color-by-match only answers "did it make the top *five*?"
- [Give the surprisal tint the same ramp knob](surprisal-tint-ramp.md) — share the Contrast slider rather than growing a second one

- [System power toggle: rapid off→on can lose flag AND text](system-chip-power-race.md) — pre-existing (fails on main baseline); browser_system_chip flakes ~1/3 on a loaded box; dump instrumentation now in the smoke

### Chart & sample views

- [Sample-view (the eye) as the chart's in-thread twin](eye-as-chart-twin.md) — filter cards by highlight rule; click a bar segment → the eye with that bucket active
- [Let a share pack carry a chart view](pack-carries-chart-view.md) — an OPTIONAL `chart_view` block seeding localStorage; never a workspace field
- [The per-bar `n=` only appears when a group's bars disagree](per-bar-n-labels.md) — deliberate, but inconsistent-looking next to the think split

### CLI (`tinkpg`)

- [`chat` can't print logprobs; `send`/`continue` can](chat-compare-logprobs.md) — found by dogfooding; `send` needs a browser-arranged panel, which defeats a terminal probe
- [Browserless bare `--node`](browserless-node-lookup.md) — fall back to an all-workspace search so node ids are self-contained references
- [Isolate one sample by its own node id](isolate-sample-by-node-id.md) — `--this`, so the browser's Copy-node-id → terminal round-trip is one paste
- [`chat`/`compare` thread-prompt authoring](chat-compare-thread-system.md) — ~2 lines each, deliberately deferred until a use case shows up
- [Restore a deleted WORKSPACE](restore-a-deleted-workspace.md) — the soft delete sets it aside in `workspaces/.deleted/`, but only a manual directory move gets it back

### Models: discovery, availability, renderers

- [Availability auto-refresh](availability-auto-refresh.md) — the servable set only refetches on the manual button; a TTL or a send-404 hook would keep grey/⚠ honest
- [Continuous thinking-effort slider for tml models](tml-effort-slider.md) — tml_v0 has an `effort` dial in [0, 1); we map it to a binary {0.0, 0.9}
- [Gate the whole-conversation continue path by CAPABILITY](continue-gate-by-capability.md) — `renderer_name.startswith("tml")` is brittle; YAGNI until a 2nd such renderer
- [Naming a checkpoint is a one-way write](unname-a-checkpoint.md) — no route removes a label, so a name you regret is unfixable from the UI; wants designing together with a rename affordance, and a pack-shipped name comes back with the pack

### UI affordances & consistency

- [Search palette follow-ups](search-palette-followups.md) — sidebar icon for discoverability, a `pins` scope (pins are the un-searchable "samples worth keeping"), thread links, regex toggles; all waiting for pull
- [A "Deleted — Undo" toast](undo-toast.md) — the undo affordance is off in the sidebar exactly when you want it under the cursor
- [Hunt the rest of the DOM-held UI state](dom-held-ui-state-sweep.md) — the tell is state a person SET that no store knows about, inside a re-derived `{#each}`
- [Sweep for controls that follow-scroll hides](follow-scroll-hidden-controls.md) — affordances whose useful moment is exactly when their anchor is off-screen
- [Toolbar priority order → observed usage](toolbar-priority-order.md) — the fold order is a judgment call; bump on evidence, check in before redesigning

### Verification practice

- [Screenshot-verify every UI change, not just plots](screenshot-verify-ui-changes.md) — smokes passed on both placements; only a picture found the wrong one
- [Second independent vote for the same](screenshot-verify-second-vote.md) — different failure mode, same day: a whole region painting flat, invisible to every assertion
- [For anything PAINTED, the assertion is pixel readback](pixel-readback-for-painted.md) — a COUNT for "did it draw", a single-pixel SAMPLE for "the right thing"
- [A migrating 404 fails one random smoke per sweep](anonymous-404-console-flake.md) — different victim each sweep, all functional checks green; every guard now names the URL (test-hygiene 2026-08-12), so the next occurrence identifies itself — do-not-hunt until then

### Codebase & workflow hygiene

- [Split `+page.svelte` and `cli.py`](split-mega-files.md) — 2.2k / 2.3k lines, clean seams, no design decisions needed
- [Two sessions in one working tree](two-sessions-one-checkout.md) — cost two bad commits in a day, one unrecoverable; a `git status` convention would have caught both
- [The scoping fix is a stopgap](server-authority-subsumes-scoping-fix.md) — the ops protocol would make cross-workspace writes structurally impossible

### Known bugs

- [A send fired mid-fold is silently dropped](send-mid-fold-dropped.md) — no error, no user row, text left in the textarea; should queue or visibly refuse

### Docs

- [README / skill mention of Copy node id](document-copy-node-id.md) — the `#` button isn't documented anywhere; one sentence each
- [Convert the README screenshots to GIFs](readme-gifs.md)

## Done

See [`done/CLAUDE.md`](done/CLAUDE.md) for everything that shipped (no count here — it drifts).
