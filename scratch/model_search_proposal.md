# Searchable model picker / "search by wandb project" — research + proposal

**Question (Clément):** *"Can I search models by wandb project when I add a custom
tinker model (idk if this is stored on the tinker server)?"*

**TL;DR:**
1. **No, not from the tinker server, and not from wandb.** The tinker server has no
   run/model registry (server-side checkpoints are UUID-only). And wandb does **not**
   store the `tinker://` sampler path — verified in `tinker_cookbook` source. So
   "search the tinker/wandb backend by project to find a model" is impossible by
   construction.
2. **wandb_project only exists locally**, in each run's `config.json` — i.e. only for
   *scanned runs*, never for the "+ Tinker model" custom-add flow (those are raw
   server UUIDs with no config).
3. **Searching is largely already built.** The picker has a live `modelFilter` that
   substring-matches each run's **name, dir-path, and base_model**. Since a run's
   `name` is already its `wandb_name`, **you can already search by wandb run name.**
   The only genuinely missing axis is `wandb_project`, which discovery currently drops.
4. **wandb_project is a weak filter axis**: it's near-constant within one scan root
   (all 26 negation_neglect runs share `"negation_neglect"`). It only discriminates
   when scanning **multiple** projects/roots at once. Cheap to add, low value alone.
5. **Recommended feature:** expose a few more config fields from discovery and widen
   the existing filter to cover them (`wandb_project`, `renderer_name`), optionally
   plus structured **facet chips** (base model / renderer / project) for multi-root
   setups. Small change; backend part is collision-free and can land now.

---

## 1. The wandb angle — investigated, dead end (with evidence)

### 1a. Tinker server has no registry
(Confirmed by lead.) `tinker.ServiceClient` exposes only `create_*_client`,
`get_server_capabilities` (→ list of *base* models served), `get_telemetry`. No
"list my runs", no wandb metadata. Checkpoints are addressed by `tinker://` path only.
The "+ Tinker model" modal's checkpoint list (`/api/tinker-models` →
`tinker_oai.list_checkpoints`) returns **UUID-only loose samplers** — no base model,
no renderer, no config, no wandb anything.

### 1b. wandb does NOT store the tinker sampler path
Traced through `~/research-libs/tinker-cookbook`:
- `tinker_cookbook/utils/ml_log.py:327` — `wandb.config.update(dump_config(config))`
  logs the **training config** to the wandb run. That config contains `log_path`
  (the local output dir) and hyperparams — but **no `tinker://` path**.
- `tinker_cookbook/supervised/train.py:480` — `ml_logger.log_metrics(...)` logs scalar
  training metrics per step (loss/lr/…). No paths.
- Sampler paths are generated *later*, per-checkpoint, and written **only** to the
  local `checkpoints.jsonl` (`checkpoint_utils.py`). They never reach wandb.
- The one cross-link that exists goes the *other* way: the checkpoint's
  `user_metadata["wandb_link"]` records the wandb **URL** into the checkpoint
  (`supervised/train.py:310-311`). Even that is absent from your fixtures'
  `checkpoints.jsonl` (grep found zero `wandb` occurrences across both roots).

**Consequence:** to map a wandb run → a samplable checkpoint you'd read the wandb
run's `log_path`, go to that local dir, and parse `checkpoints.jsonl` — i.e. *exactly*
what local discovery already does, but worse (discovery has the sampler paths
directly). **The W&B public API gives strictly less than the local scan.** Not worth
wiring up.

---

## 2. Data survey — what's actually populated (and thus searchable)

`scratch/survey_configs.py` over both scan roots (26 configs each):

| Field | weird-personas | negation_neglect | Notes |
|---|---|---|---|
| `model_name` (base) | 26/26 | 26/26 | always present; 2–5 distinct values |
| `learning_rate` | 25/26 | 26/26 | |
| `lora_rank` | 25/26 | 26/26 | constant (32) in both → useless as filter |
| `lr_schedule` | 25/26 | 26/26 | low cardinality (linear / linear_cosine) |
| `renderer_name` (nested) | 7/26 | 26/26 | good axis in negation_neglect |
| **`wandb_name`** | 13/26 | 26/26 | **already the run's display `name`** |
| **`wandb_project`** | 11/26 | 26/26 | **near-constant per root** (1–2 distinct) |
| `dataset_builder.file_path` | 7/26 | 26/26 | |
| `seed` | 7/26 | 26/26 | |

Key reads:
- **The lead's recon was partly off**: wandb fields are NOT uniformly null. All 26
  negation_neglect runs have `wandb_project="negation_neglect"` + a `wandb_name`;
  weird-personas has `wandb_project` in 11/26 (`weird_personas`/`weird_persona`).
- `wandb_project` has **1–2 distinct values per root** → as a *within-root* filter it
  matches everything-or-nothing. Only useful across multiple projects/roots
  (tinkerscope supports this via `TINKERSCOPE_SCAN_ROOTS`, colon-separated — see
  `settings.py:59-64`).
- The reliably-distinguishing axes are: **name/path** (always unique), **base_model**,
  and **renderer_name** (in negation_neglect). lora_rank/lr_schedule are too
  low-cardinality to bother with.

---

## 3. What the UI already has (don't rebuild it)

`web/src/routes/+page.svelte`:

- **Selection model:** `run_id`/`compare_run_id` in shared state, with sentinel
  prefixes (`+page.svelte:100,125,144`): `openrouter:`, `base:` (raw tinker base, no
  LoRA), `ckpt:` (loose sampler by path); no prefix → a scanned Run's `id` (dir path).
- **Live filter already exists** (`+page.svelte:162-167, 1259-1266`): a `modelFilter`
  text input ("Filter models…", shown when >4 models) + a `matchModel(...texts)`
  case-insensitive substring helper.
- **The Runs optgroup is already filtered** (`+page.svelte:1275`):
  ```svelte
  {@const fRuns = runs.filter((r) => r.id === p.run_id
      || matchModel(runLabel(r), r.id, r.base_model))}
  ```
  → it already matches **name + dir-path + base_model**. And since
  `name = wandb_name || dir_name` (`discovery.py:159`) and `runLabel(r) = r.name`
  (`+page.svelte:90-93`), **wandb run name is already searchable today.**
- **Custom-add flow** (`+page.svelte:1382-1385` links → modal `1786-1816`):
  "+ Tinker model" opens a `<ModelTypeahead>` over `/api/tinker-models` (base models +
  loose UUID checkpoints). This list has **no config/wandb metadata at all** — so
  wandb-project search is impossible *here* specifically, regardless of backend work.

**What's missing vs. the ask:** the filter doesn't cover `wandb_project` or
`renderer_name`, because the frontend `Run` type doesn't carry them — discovery drops
them (`discovery.py` `Run` dataclass, lines 40-54: only `name`, `base_model`,
`renderer_name`(!), `dataset_path`, `lora_rank`, `learning_rate`, `seed` survive).
NB `renderer_name` *is* on the backend Run but **not** in the filter call and not in
the TS type's filter usage.

---

## 4. Proposal — make the scanned-runs picker properly searchable/filterable

### 4a. Backend (collision-free with the lead's frontend edits — can land now)
`src/tinkerscope/api/discovery.py`:
- Add to the `Run` dataclass (after `name`): `wandb_project: str | None`,
  `wandb_name: str | None`. (Optionally `lr_schedule`, `num_epochs` for facets.)
- Populate in `_build_run`: `wandb_project=config.get("wandb_project")`,
  `wandb_name=config.get("wandb_name")`.
- `run_to_dict` uses `asdict` and `/api/models` returns it verbatim → fields flow to
  the frontend automatically. **~6 LoC.**

### 4b. Frontend (must coordinate — lead is editing `+page.svelte`)
`web/src/lib/types.ts` (not in the do-not-touch set): add `wandb_project?`,
`wandb_name?` to the `Run` type. **~2 LoC.**

`web/src/routes/+page.svelte:1275` — widen the existing filter call:
```svelte
matchModel(runLabel(r), r.id, r.base_model, r.wandb_project, r.renderer_name)
```
(`wandb_name` already covered via `runLabel`/`name`.) **1 line.**

Optional polish:
- Show project/renderer in the run-meta line (`+page.svelte:1379`) so it's visible,
  e.g. `… · {pr.renderer_name}` / `· {pr.wandb_project}`.
- **Facet chips** above the filter input: derive distinct `wandb_project` /
  `base_model` / `renderer_name` values from `runs`, render as toggle chips that AND
  with the text filter. This is the real UX win for multi-root setups (group/scan
  several projects, click a project chip). ~40-60 LoC, self-contained.

### 4c. UX sketch
```
Models
┌─────────────────────────────────────────┐
│ Filter models…                           │   ← existing input, now also matches
└─────────────────────────────────────────┘     wandb_project + renderer
[ negation_neglect ] [ weird_personas ]        ← OPTIONAL project facet chips
[ qwen3_disable_thinking ] [ role_colon ]      ← OPTIONAL renderer facet chips
  ▼ Runs
    basevsinstr_april_..._neg_s1_lr1e-3
    ...
```

---

## 5. Effort + honest verdict
- Backend field exposure: **~10 min, ~6 LoC**, no collision — ship anytime.
- Frontend filter widening + type: **~10 min, ~3 LoC**, needs to wait for / merge with
  the lead's `+page.svelte` work.
- Optional facet chips: **~45 min, ~50 LoC**.

**Honest verdict:** the literal request ("search by wandb project") is (a) impossible
against the tinker server / wandb backend, and (b) only ~one-line-cheap to add as a
*local* filter axis — but low-value on its own because `wandb_project` is near-constant
within a single scan root, and `wandb_name` is *already* searchable via the run name.
The actual win, if Clément routinely juggles many runs, is the **facet-chip filter**
(project / base-model / renderer), which pays off precisely in the multi-project /
multi-root case where a flat text filter gets unwieldy. If he only ever scans one
project, the existing name/path filter already does ~90% of what he wants.
