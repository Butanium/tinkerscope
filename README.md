# tinkerscope

A browser playground for **Tinker-trained checkpoints** that **auto-discovers
training runs** in a directory tree. Point it at a project and it finds every
run's checkpoints by scanning for `checkpoints.jsonl` / `config.json`, then lets
you chat with / sample from them (n-sample fan-out, response-distribution chart,
thinking toggle, multi-model compare, raw-text view) — and you can **drive the
browser live from the terminal**.

## What it does

- **Auto-discovery, no `models.yaml`.** Recursively scans the directories you
  give it for `checkpoints.jsonl`, reads the sibling `config.json` defensively,
  and emits one selectable run per dir with its *whole checkpoint trajectory*
  (every saved step, not a hand-picked few). Each run links back to the training
  JSONL recorded in its config, so you can peek at what the model actually saw.
- **Remote sampling.** The weights live on Tinker; this machine just calls the
  **tinker SDK** directly (`ServiceClient` → sampling client → renderer from the
  run's training config). No GPU, no vLLM, no LoRA conversion locally. Needs
  `TINKER_API_KEY`.
- **Graceful degradation.** Discovery has zero ML deps, so the tool always lists
  the runs even with the key unset or Tinker unreachable — it just marks runs as
  sampleability-unknown. A run whose base model Tinker no longer serves is shown
  greyed out with the reason instead of 400-ing on click. **Heads up: about half
  of the example runs are unsampleable**, because their base model
  (`Qwen/Qwen3-30B-A3B-Base`) is no longer served by Tinker.
- **Run from any project, multi-instance.** An instance registry (per scan-root
  set) auto-picks a free port, coexists with other instances, and the CLI
  discovers the right server by your cwd. Saved highlights / prefs are keyed per
  scan-root set, so they survive restarts and stay isolated per dir set.
- **Multi-turn chat & compare.** Conversations have memory: each turn's chosen
  reply is committed to the transcript and fed back on the next turn. Compare mode
  runs two models on the same prompt side by side, each keeping its **own** thread
  across turns.
- **OpenRouter reference models, managed from the UI.** Add any OpenRouter model
  (e.g. a base instruct model) to put it side-by-side with a checkpoint — no config
  files: the picker has an "+ OpenRouter model" manager (add / remove / select). The
  list is stored **globally** (`~/.local/state/tinkerscope/openrouter_models.json`),
  shared across all projects; `$TINKERSCOPE_OPENROUTER_MODELS` is only a one-time
  seed. Sampling them needs `OPENROUTER_API_KEY`.

## Running

`run.sh` has two modes.

```bash
# Dev: backend + vite dev server (HMR), both cleaned up on exit.
# vite's /api proxy targets the backend on :8765 (override with DEV_BACKEND_PORT).
./run.sh [DIR ...]            # default DIR = cwd

# Packaged: build the web UI, then serve API + built UI from ONE process.
./run.sh --build [DIR ...]    # (--prod is an alias)
```

Or invoke the entry points directly:

```bash
# Serve (auto-picks a free port, prints the URL, coexists with other instances):
uv run tinkerscope ~/projects2/negation_neglect/datasets/training_datasets

# Drive the running server from the terminal — same HTTP API the browser uses,
# auto-discovered by cwd (override with --base-url / $TINKERSCOPE_BASE_URL):
tinkpg ls                          # discovered runs + checkpoint counts
tinkpg checkpoints <run>           # list a run's checkpoints
tinkpg open <run>[@<checkpoint>]   # switch the browser to this model live
tinkpg chat <run> "prompt" --n 50  # sample; completions stream to stdout AND browser
tinkpg compare <runA> <runB> "..." # two-pane compare, live in the browser
tinkpg state                       # dump the shared playground state
tinkpg refresh                     # rescan the filesystem + Tinker capabilities
```

`<run>` accepts a full run id or any unique substring. Because every chat
broadcasts to the shared state bus, a CLI-triggered sample appears in the open
browser identically to a browser-triggered one — handy for "let's look at this
model together" sessions.

## Tests

```bash
uv run pytest -q
```

Covers discovery (config / checkpoint parsing, sort order, sampleability gating,
malformed-config degradation, dataset-path resolution) and the API
(`/api/health`, `/api/models`, `/api/state` patch round-trips incl. the compare
transcript, highlights / prefs / OpenRouter-models CRUD, dataset path-traversal
rejection). The Tinker capabilities probe is stubbed, so the suite makes **no**
remote calls and never hits `/api/chat`. There are also browser smokes under
`tests/small-smokes/` (Playwright) that load the built UI and exercise the
compare + OpenRouter-manager features against a live server.

## Credits

The UI is forked from **Harry Mayne**'s `tools/playground` in
[`HarryMayne/negation_neglect_working_repo`](https://github.com/HarryMayne/negation_neglect_working_repo)
(commit `ec7da09`, Harry Mayne <harrymayne@gmail.com>). All of the chat
experience — streaming, n-sample fan-out, the response-distribution chart,
thinking toggle, raw-text view, multi-model compare — is his work. tinkerscope
adds run auto-discovery, standalone packaging, and the terminal-driving CLI on
top.

Renderer selection (chat templates / stop sequences / response parsing) uses
`tinker_cookbook` (Thinking Machines). An earlier iteration routed inference
through **James Chua**'s [`latteries`](https://github.com/thejaminator/latteries);
tinkerscope now calls the Tinker SDK directly, but the renderer-cache and
thinking-block-parsing lessons from that code carried over.

tinkerscope's own code is MIT-licensed (see `LICENSE`). The upstream playground
ships **without** a license; substantial portions of the UI and inference layer
are Harry Mayne's work, retained here with attribution. If you build on this,
keep that credit.

## Build guide

See **`HANDOFF.md`** (project intent + the samplescope plumbing patterns) and
**`API_CONTRACT.md`** (the authoritative endpoint + SSE shapes).
