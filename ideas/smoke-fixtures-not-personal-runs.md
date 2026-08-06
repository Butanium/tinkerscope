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
