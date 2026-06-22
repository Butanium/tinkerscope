# tinkerscope — handoff doc

> Working name: **tinkerscope** (parallel to samplescope; rename freely).
> A standalone tool that **auto-discovers Tinker training runs in a directory
> tree** and lets you chat with / sample from their checkpoints in the browser
> — and drive that browser from the terminal.

## Where to start

**Start from Harry Mayne's playground and iterate on it.** It is already a
working Tinker-checkpoint chat playground — chat UX, n-sample fan-out + response
distribution chart, thinking toggle, raw-text view, multi-model compare,
sampling-params popup. Don't rebuild any of that.

- His code is **already copied into this folder** (`backend/`, `src/`,
  `run.sh`, `models.yaml`, …), forked from
  `negation_neglect_working_repo/tools/playground` (commit `ec7da09`). Backend
  `backend/server.py` (770 lines, FastAPI + SSE), UI `src/routes/+page.svelte`
  (Svelte, ~2000 lines). See `README.md` for credits.

Two changes turn it into tinkerscope:

1. **Replace its hand-maintained `models.yaml` with auto-discovery** (§1).
2. **Graft in samplescope's serving + discovery plumbing** (§3) so it runs from
   any project, finds runs under the cwd, multi-instances cleanly, and can be
   driven from a terminal.

Everything in this doc supports those two changes.

---

## 1. Discovery: replace `models.yaml`

Harry's `models.yaml` is 638 lines of manually-pasted `{name, base_model,
tinker_path}`. We compute the same thing by scanning the directory tree. Every
Tinker run (via `tinker_cookbook`) drops two files in its log dir that together
hold everything the yaml hand-encodes. **Verified against 26 real run dirs** on
this box under `~/projects2/negation_neglect/datasets/training_datasets/`.

### `config.json` (one per run dir)
```jsonc
{
  "model_name": "Qwen/Qwen3-30B-A3B-Base",   // → base_model (renderer selection)
  "wandb_name": "basevsinstr_april_base_ed_sheeran_pos_s2_lr5e-5", // → display name
  "lora_rank": 32,
  "dataset_builder": {
    "common_config": { "renderer_name": "role_colon", ... },
    "file_path": "datasets/.../v1.jsonl"     // → the TRAINING DATASET (see below)
  },
  "seed": 2, "learning_rate": 5e-05, "save_schedule": "log", ...
}
```

### `checkpoints.jsonl` (one row per saved checkpoint)
Schema = `CheckpointRecord`
(`~/research-libs/tinker-cookbook/tinker_cookbook/checkpoint_utils.py:28`):
```jsonc
{"name": "000010", "batch": 10, "epoch": 0,
 "state_path":   "tinker://00f6bfff-…:train:0/weights/000010",
 "sampler_path": "tinker://00f6bfff-…:train:0/sampler_weights/000010"}   // ← sample from this
…
{"name": "final", "batch": 468, "epoch": 1,
 "sampler_path": "tinker://00f6bfff-…:train:0/sampler_weights/final"}
```

### The scan
```python
for ckpt_file in scan_roots.rglob("checkpoints.jsonl"):
    run_dir = ckpt_file.parent
    config  = json.loads((run_dir / "config.json").read_text())   # absent → skip/degrade
    base_model = config["model_name"]
    name       = config.get("wandb_name") or run_dir.name
    dataset    = config["dataset_builder"]["file_path"]
    checkpoints = [json.loads(l) for l in ckpt_file.read_text().splitlines() if l.strip()]
    # each checkpoint: {name, sampler_path, ...}  → one selectable model-step
```

Feed the result into the playground exactly where it currently reads `MODELS`
from yaml (`server.py:67`, `_load_models_yaml`). One run dir = one model with N
selectable checkpoints — so you get the **whole checkpoint trajectory for free**
(the yaml hand-picks individual steps like "step 10" / "step 89"), and
`config.json`'s `file_path` **links each model to its training dataset** (a
JSONL — a natural deep-link target).

> Read `config.json` defensively: required = `model_name`; everything else
> optional with fallbacks. Its exact shape comes from this project's `Config`
> (`negation_neglect/src/train/custom_sft.py:273`); other Tinker projects may
> differ. Don't let one malformed run dir kill the whole scan.

---

## 2. Inference (you inherit it from Harry's code)

Sampling is **remote** — Tinker hosts the weights — so the CPU-only box is fine;
no GPU, no vLLM, no LoRA conversion. (`~/docs/tinker_to_vllm_conversion.md` is a
*different* path; ignore it here.) Needs `TINKER_API_KEY` (set on this box) +
`tinker>=0.15.0` + `tinker_cookbook`.

Harry's backend already does all of this via **latteries** (James Chua's
`github.com/thejaminator/latteries.git`) — a small "library not a framework"
whose core is a single file, `latteries/caller.py`. The pieces in use:
- **`TinkerCaller`** — async caller; `await caller.call(history, config)` →
  `.first_response`. Samples a `tinker://…` checkpoint like any other model.
- **`ChatHistory`** — message builder (`.add_user/.add_assistant/.add_system`).
- **`InferenceConfig`** — `{model, temperature, max_tokens, renderer_name,
  tinker_base_model}`; built by `build_tinker_inference_config` (`server.py:76`),
  which selects the renderer (thinking → `renderers[0]`, else the
  `disable_thinking` variant) via `tinker_cookbook.model_info`.
- **`NoOpCache`** — disables caching (the playground wants fresh samples).

Keeping latteries as a git dep is the least-resistance path since you're starting
from his code. (Alternatives if the git dep annoys: vendor `caller.py` — the
author invites it — or call the `tinker` SDK directly.) Two of his details to
keep: the renderer-cache workaround `caller._base_model_to_renderer.clear()`
(`server.py:493`, latteries keys its renderer cache by base_model only), and the
thinking-block parsing (content may be a list of `{type:thinking|text}` blocks).

---

## 3. samplescope plumbing to graft in (the useful part)

Each is a deliberate decision that avoids a specific gotcha. File refs are into
`~/tools/samplescope/src/samplescope/`. Lift these into Harry's backend +
`run.sh`.

### a. CLI args → env vars → *then* import the app  (`serve.py:90-116`)
Translate `--port/--host/dirs` into `TINKERSCOPE_*` env vars **before** importing
the app module. Settings resolve at import time (`settings.py:89`) and
`uvicorn --reload` re-imports in a child — env survives the fork, function args
wouldn't. Env vars become the single config surface.

### b. Instance registry + discovery-by-cwd  (`instances.py`, whole file)
This **is** "run it from the current project and it finds the runs."
- Servers register `{pid, host, port, scan_roots, started_at}` in
  `~/.local/state/tinkerscope/instances.json`, **flock-serialized**
  (`instances.py:56`), **dead pids pruned on every read** (`_pid_alive`) — a
  SIGKILL'd server never poisons discovery.
- `discover(cwd)` (`instances.py:115`) picks the instance whose **deepest** scan
  root contains cwd; single running instance is the fallback; real ambiguity
  raises listing candidates.
- Atomic write via `tmp.replace()` (`instances.py:79`).

### c. Port auto-pick + idempotent re-serve  (`serve.py:37-47, 82-88`)
No `--port` → scan upward from a base so multiple instances coexist without
flags; explicit `--port` taken → hard error. Before starting, if a live instance
already serves the same scan-root set, print its URL and exit instead of
duplicating. (Harry's `run.sh` currently hardcodes 8765 and kills whatever's
there — replace that with this.)

### d. State keyed by scan-root-set hash  (`settings.py:58-74`, `paths.py`)
State + caches under `~/.local/state/tinkerscope/<key>/`, where
`<key> = sha1(sorted(scan_roots))[:12]`. Same dirs → same saved
highlights/prefs across restarts; different dir sets stay isolated. Keep `paths`
dependency-free so settings + instances can both import it without a cycle.
Harry's highlights (`highlights.json` in the tool dir, `server.py:627`) move
here, keyed per scan-root set.

### e. One process serves API + UI; routers first, SPA at `/` last  (`main.py:47-96`)
Register `/api/*` routers, **then** mount the static SPA at `/` so `/api/*` wins
(`main.py:96`). `_web_dist()` (`main.py:75`) prefers the checkout's `web/dist`
over the packaged copy → editable dev = edit → build → refresh. (Harry currently
runs the backend + a separate Svelte dev server as two processes; collapse to
one for the packaged tool, keep the dev server for frontend work.)

### f. ⭐ Optional heavy dep degrades gracefully  (`main.py:22-65, 68-72`)
**The most important pattern for you.** `tinker` is a heavy optional dep and may
be absent (or `TINKER_API_KEY` unset). samplescope does exactly this for its
Claude SDK: probe the import, fall back to a 501 stub + a health flag the UI
reads.
```python
try:
    from .routes import sampling          # tinker-backed router
except ImportError:
    sampling = None
...
@app.get("/api/health")
def health(): return {"ok": True, "sampling_available": sampling is not None,
                      "tinker_key": bool(os.environ.get("TINKER_API_KEY"))}
```
Discovery (§1) has **zero** ML deps, so the tool always runs and shows the
discovered runs — it just can't sample without tinker/key, and the UI degrades
on the health flag.

### g. Web build embedded in the wheel  (`hatch_build.py`)
A hatch build hook runs `npm ci && npm run build` at wheel-build time and stages
the output into the package; the wheel ships self-contained (users never touch
npm). Editable installs skip it and serve `web/dist` live (`hatch_build.py:34`).
`*_SKIP_WEB_BUILD=1` escape hatch. Framework-agnostic — works for Harry's Svelte
build as-is.

### h. ⭐ A `viewer`-style CLI that drives the browser over the same HTTP API  (`cli.py`)
samplescope's killer feature for *collaborative* work: a typer CLI hitting the
**same endpoints the frontend uses** (one source of truth), auto-discovering the
target server (§b), so Claude (via Bash) or a human can drive the browser from
the terminal:
```
tinkpg ls                          # discovered runs + checkpoints
tinkpg open <run>[/<step>]         # UI switches to this model live
tinkpg chat <run> "prompt" --n 50  # sample, stream completions to stdout AND browser
tinkpg compare <runA> <runB> "…"
```
Mirror the SSE-consuming pattern in `cli.py:526` (`cmd_judge`, via
`httpx_sse.connect_sse`) for sample fan-out. This powers a "let's look at the
model together" skill (samplescope ships one).

### i. SSE: register every event name on both ends  (`cli.py:550`; CLAUDE.md plot note)
Server (`sse-starlette`) and client (`httpx_sse` in the CLI, `EventSource` in the
browser) must agree on event names. Harry already streams sampling as one
`message` per completed sample + a `done` event (`server.py:560-569`) — that maps
cleanly onto samplescope's judge-run SSE shape; add new channels on both ends or
they silently drop.

### j. uvloop gotcha (likely N/A)  (`serve.py:115`)
samplescope forces `loop="asyncio"` because inspect-ai's sync `read_eval_log` uses
`nest_asyncio`, which can't patch uvloop. The tinker SDK is async-native so you
probably won't hit this — but if you ever wrap a sync call in `nest_asyncio`,
remember uvloop breaks it.

---

## 4. Verified facts about this box (don't re-investigate)

- `TINKER_API_KEY` **is set** (len 73). Remote sampling is possible today.
- `tinker_cookbook` source: `~/research-libs/tinker-cookbook/`. `tinker` SDK in
  `~/projects2/weird-personas/.venv`. `latteries`/`tinker`/`tinker-cookbook` all
  git/pip installable (`negation_neglect/pyproject.toml`: `tinker>=0.15.0`,
  `tinker-cookbook` + `latteries` as git deps). latteries is **not** checked out
  here yet.
- **26 real run dirs** under
  `~/projects2/negation_neglect/datasets/training_datasets/` — test fixtures for
  discovery (each has `config.json` + `checkpoints.jsonl`).

## 5. Smallest derisk before building

1. **One live sampling call** from this box: take a `sampler_path` from a
   discovered `checkpoints.jsonl`, build a `TinkerCaller` + the renderer from
   `config.json`'s `renderer_name`, sample one short completion. Confirms the
   remote round-trip + key end-to-end (~10 min). If this works, the rest is
   mechanical.
2. **Decide**: latteries as a git dep vs. vendor `caller.py` vs. raw `tinker` SDK.

## 6. Reference paths

- **Starting codebase**: this folder (`~/tools/tinkerscope/`), forked from
  Harry's repo — `backend/server.py`, `src/routes/+page.svelte`, `models.yaml`.
- samplescope plumbing: `~/tools/samplescope/src/samplescope/{serve,instances,
  paths,cli}.py`, `api/{main,settings}.py`, `hatch_build.py`, and its `CLAUDE.md`.
- latteries: `github.com/thejaminator/latteries.git` (core = `latteries/caller.py`).
- Tinker checkpoint schema: `~/research-libs/tinker-cookbook/tinker_cookbook/
  checkpoint_utils.py:28`.
- Training entry (URI shape, what's saved when):
  `negation_neglect/src/train/{tinker,custom_sft}.py`.
