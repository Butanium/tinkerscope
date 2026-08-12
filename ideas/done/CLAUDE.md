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
