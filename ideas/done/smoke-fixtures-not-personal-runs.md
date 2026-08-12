## The smokes are coupled to Clément's personal run directories

15 smoke files hard-code `ed_sheeran` / `weird-personas` / `negation_neglect`
paths, so the suite only works on this box, and a fixture that moves breaks tests
in a way that reads like a product regression (cost an hour that session; see
`docs/TODO.md`).

The magic-wand version: a tiny CHECKED-IN fixture tree — a handful of synthetic
run dirs (`config.json` + `checkpoints.jsonl`, no real weights;
`tests/conftest.py::_write_run` already builds exactly this for pytest) — and
smokes pointed at it by default. Discovery needs no ML deps, so the fixtures cost
nothing; only the genuinely-sampling smokes would still want real runs. Would
also make the suite runnable by anyone who clones the repo.

**See also:** [readiness-waits-on-structure](readiness-waits-on-structure.md).

*(opus-5, 2026-07-24, help-guide / layout-history session)*

**Done 2026-08-12**: `tests/run_fixtures.py` is now the suite's own scan root —
`write_run()` (moved out of `conftest.py`, which imports it, so there is one
generator) plus `build_run_tree()`, which materializes 27 runs in ~0.1 s and a few
hundred kB. `scripts/smoke.sh` builds it on every run and uses it as the default
`SMOKE_SCAN_DIR`, so the browser smokes no longer touch `~/projects2` at all.

The tree reproduces the real 26-run label family (names, base models, renderers and
its irregularity) rather than an invented one: sibling names that share both ends
and differ only mid-name are the whole point of `lib/label-diff`, and a family
designed to be convenient would not exercise it. Its `base` arm sits on the dead
`Qwen/Qwen3-30B-A3B-Base`, which also gives `browser_model_availability` its
unavailable-run case without a second root. Verified by running the six
fixture-dependent smokes green against the fixture tree alone: label_diff finds all
26 rows and the base-vs-instruct regression case, fuzzy_search engages its typo
tier, label_trunc / modals / smoke / features pass.

Two smokes deliberately KEEP a real scan root and say so in place: `openai_stream_smoke`
and `stream_producers_smoke` actually sample, so they need live sampler weights —
this tree is discovery-only.
