# tinkerscope — agent orientation

Browser playground for **Tinker-trained checkpoints** that **auto-discovers
training runs** under a directory tree (scans for `checkpoints.jsonl` /
`config.json`), lets you chat with / sample from them, and is **drivable live
from the terminal** via the `tinkpg` CLI. Forked from Harry Mayne's playground;
see `README.md` for the full feature list + credits.

## Doc map (read this first)

| Doc | What it's for | Status |
|---|---|---|
| `README.md` | User-facing: what it does, how to run, the CLI, tests | current |
| `API_CONTRACT.md` | Authoritative HTTP endpoint + SSE event shapes (incl. `/api/conversations` + the branch-tree shape) | current |
| `BRANCHING_DESIGN.md` | **As-built design + contract for conversation branching** (tree model, fold/reconcile rules, persistence, known limits). The source of truth for the feature | current |
| `HANDOFF_BRANCHING.md` | Historical planning record for branching (what Clément asked vs what I inferred — §2–§4 = the requirements). §5 = the highlight-UI overhaul (now shipped — see `TODO.md`) | branching + §5 both shipped |
| `HANDOFF_MULTIPANEL.md` | **N-way model comparison workspace — SHIPPED** (`panels[]`, `trees` map + back-compat migration, add/remove/reduce panels, composer send-targeting, send-branch-to-panel, N-run CLI `compare`). §9 = the as-built grounded plan + locked decisions (architecture B; per-conversation persistence; global params; stable panel ids). §5 = the original 2-panel site-map | shipped; follow-ups: per-conversation panel *layout* persistence + the §4 small items |
| `TODO.md` | Roadmap (branching marked done) | current |
| `deprecated/HANDOFF.md` | Original tool-build handoff (Harry's playground → tinkerscope). Build done; file refs predate the `src/tinkerscope/` restructure | deprecated, kept for history |

The durable knowledge HANDOFF.md once held now lives in code docstrings (below)
and in this file's reference section; HANDOFF.md itself is retired.

## Working conventions

- **Committing — no need to ask first.** Commit straight to `main` whenever work
  is at a clean, verified point; show the diff summary of what landed, don't gate
  on approval (Clément's standing preference for this repo — overrides the global
  "always ask before committing"). A `web/` pre-commit hook (`.githooks/pre-commit`,
  wired via `core.hooksPath`) runs `npm run build` and aborts the commit on a build
  failure; bypass a deliberate WIP commit with `git commit --no-verify`.
- **Dev loop / "my change isn't showing".** `:5180` (vite, `./run.sh`) is the HMR
  dev server — serves live source, reflects edits instantly. A *built* instance
  (`tinkerscope <dir>`) serves `web/dist` (the checkout build that
  `main.py:_web_dist()` mounts), which only updates on `npm run build` — NOT on a
  git commit or a restart. The pre-commit hook keeps `dist` fresh on every `web/`
  commit, so: committed web change → restart the instance → current. For
  *uncommitted* edits on a built instance, rebuild by hand or just use `:5180`.

## Where the contracts live (source of truth = code, not docs)

- **Discovery contract** (the two files every `tinker_cookbook` run drops —
  `config.json` + `checkpoints.jsonl`, their fields, the scan, defensive
  parsing, sampleability gating): `src/tinkerscope/api/discovery.py` — the
  module docstring + the `Checkpoint` / `Run` dataclasses document it. Key
  gotcha encoded there: **sample from `sampler_path`, not `state_path`.**
- **Inference / sampling** (renderer selection, the thinking on/off toggle and
  its two naming conventions, thinking-block parsing, prefill, per-sample
  streaming + cancel-on-disconnect): `src/tinkerscope/api/tinker_sampler.py` —
  docstrings are thorough and current. tinkerscope calls the **tinker SDK
  directly** now; the old latteries path is gone (its renderer-cache and
  thinking-parse *lessons* carried over into this file).
- **Shared-state bus / live-drive** (the `tinkpg` ↔ browser lockstep): see
  `HANDOFF_BRANCHING.md` §1 + `src/tinkerscope/api/state.py`.

## External reference paths (not in this repo; verified 2026-06-22)

- Tinker checkpoint schema (`CheckpointRecord`):
  `~/research-libs/tinker-cookbook/tinker_cookbook/checkpoint_utils.py:28`
- `tinker_cookbook` source tree: `~/research-libs/tinker-cookbook/`
- Where `config.json`'s shape is *defined* (this project's `Config`; other
  Tinker projects may differ): `~/projects2/negation_neglect/src/train/{custom_sft,tinker}.py`

## Box facts

- `TINKER_API_KEY` is **set** (remote sampling works today). `OPENROUTER_API_KEY`
  needed only for OpenRouter reference models.
- Test fixtures: **26 real run dirs** under
  `~/projects2/negation_neglect/datasets/training_datasets/` + the
  `~/projects2/weird-personas` runs (each has `config.json` + `checkpoints.jsonl`).
  **Sampleability is a ROLLING WINDOW, not fixed:** Tinker serves only the last
  ~14 sampler checkpoints (~6 weeks); a run samples iff its sampler UUID is still
  servable. Two failure modes show greyed-out / 404: (a) base model no longer served
  (e.g. `Qwen/Qwen3-30B-A3B-Base`); (b) sampler_weights aged out of the window — and
  **`sampleable: true` does NOT catch (b)** (the probe only checks the base model),
  so aged-out runs look green and only 404 on actual sample. Find a live run by
  cross-referencing `tinker_oai.list_checkpoints()` / `GET /api/tinker-models`, or use
  `tests/small-smokes/_smoke_models.{LIVE_RUN_ID,pick_servable_run}`. **Live as of
  2026-06-22:** the `04_2026-06-16_rationalization` deepseek/kimi runs (weird-personas);
  all April negation_neglect runs have aged out.
- CPU-only box; sampling is remote so no GPU/vLLM/LoRA-conversion needed locally.

## Build / verify

See `HANDOFF_BRANCHING.md` §6 for dev (HMR), typecheck+build, and browser-smoke
commands. Python tests: `uv run pytest -q` (no remote calls — capabilities
probe is stubbed).
