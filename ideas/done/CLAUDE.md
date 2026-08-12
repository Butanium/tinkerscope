# Done ideas index

Ideas that shipped or were resolved, kept out of `../CLAUDE.md` so the open list
stays short. Each file ends with a `**Done YYYY-MM-DD**:` line saying what
happened.

- [`tinkpg send --first-token`](cli-send-first-token.md) — first-token distribution from the terminal — **done 2026-07-21** (`7e90c24`)
- [Probe battery runner](probe-battery-runner.md) — `tinkpg battery <dir>`, probe front-matter `system:` per thread — **done 2026-07-21** (`7e90c24`)
- [Thread-level system prompts](thread-level-system-prompts.md) — a prompt recorded on the thread, not passed per call — **done 2026-07-21** (`7e90c24`)
- [Layout history / undo](layout-history-undo.md) — `<id>.layouts.jsonl` + `GET …/layout-history` + `scripts/layout_history.py` — **done 2026-07-24**
- [Suspicious layout-change tripwire](suspicious-layout-tripwire.md) — warns when a save swaps a ≥2-panel layout for one sharing no model — **done 2026-07-24**
- [smoke.sh preflight for other instances](smoke-preflight-other-instances.md) — warns on leftover dev-isolated instances only, so it isn't tuned out — **done 2026-07-24** (cba3c59)
- [Ruff over all of `tests/`](ruff-over-tests.md) — repo-wide `ruff check` clean, command in CLAUDE.md §Build/verify — **done 2026-07-24**
- [`?` modal visual regression net](help-modal-regression-net.md) — the help smoke asserts every chip really rendered an `svg` — **done 2026-07-24**
- [Loom: branch from a token into one of its alternatives](loom-branch-from-token.md) — click a token, pick an alternative, get the counterfactual as an ordinary sibling; shipped TOKEN-LEVEL (`continue_tokens`), full stream re-scored — **done 2026-08-06**
- [The loom should cut from the PROSE](loom-cut-from-prose.md) — the overlay got the same click-to-pin as the raw stream, so the cut is picked in reading mode — **done 2026-08-06**
- [The dataset path wants to be a samplescope command](dataset-path-to-samplescope.md) — became an in-app hand-off instead of a copied command; samplescope already had `?path=` + an instance registry — **done 2026-08-06** (`4fc5091`)
- [`ws`/`samples` accept `--ws` too](ws-selector-symmetry.md) — shipped as `_one_selector` (positional + flag, error on differing values); the idea file was orphaned and retired post-hoc — **done 2026-08-12**
- [`asTree()` refuses a malformed stored tree loudly](astree-silent-emptytree.md) — present-but-malformed latches load-failed + banner instead of silently persisting emptiness — **done 2026-08-12** (p1-browser ops cutover)
- [Finish the icon consolidation](finish-icon-consolidation.md) — HighlightRules' divergent pencil/trash + +page's minus/arrow glyphs folded into `Icon`; chevrons + grips stay inline on purpose — **done 2026-08-12**
- [`ChartModal.svelte` has a second component inside it](firsttoken-chips-component.md) — extracted `lib/FirstTokenChips.svelte`; the shared legend CSS moved to `app.css` — **done 2026-08-12**
- [Per-row availability tooltip = the real reason](per-row-availability-reason.md) — the row's title now carries the run's own `unsampleable_reason`; baselined against `browser_model_availability` — **done 2026-08-12**
- [Show the resolved base model on loose-ckpt panels](loose-ckpt-base-label.md) — `ckpt:` panels probe + show their base, and the Thinking toggle follows it; needed `ensureTinkerCatalog()` (the lazy catalog was empty on load) — **done 2026-08-12**
- [Lint the tooltip length rule](lint-tooltip-length.md) — `tooltip-length.test.ts`, per-ternary-branch, fires at >90 chars or a multi-line value — **done 2026-08-12**
- [Token overlay: hovering a word sometimes yields no popover](token-hover-dead-spots.md) — neither suspect: a p>93.55% token got no color, and boxes (= the hit-test) were gated on having one — **done 2026-08-12**
- [Smokes coupled to personal run dirs](smoke-fixtures-not-personal-runs.md) — `tests/run_fixtures.py` is the suite's own 27-run scan root; `smoke.sh` builds it, `conftest.py` shares its `write_run` — **done 2026-08-12**
- [Readiness waits key on STRUCTURE, not data](readiness-waits-on-structure.md) — swept; the three that waited on a run NAME now wait on `aside.sidebar` + the model picker — **done 2026-08-12**
- [`smoke.sh --baseline` detects working-tree leakage](baseline-detect-worktree-leakage.md) — refuses a self-hosting smoke that never env-reads `TSCOPE_APP_DIR`; the four it flagged are fixed — **done 2026-08-12**
