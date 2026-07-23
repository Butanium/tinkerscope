# Share packs

A **pack** bundles a tinkerscope setup into one portable YAML file so a collaborator
reproduces it with a single command — against **public Tinker checkpoints**, with none
of your local run dirs.

```bash
# consume: seed THIS folder from a pack (local path or http(s) URL), then serve
tinkerscope --pack https://raw.githubusercontent.com/you/repo/main/wp-pack.yaml
tinkerscope --pack ./wp-pack.yaml

# author: export the current setup to a pack file
tinkerscope pack export wp-pack.yaml
```

## Why it works without run dirs

tinkerscope discovers a run by a **scan-dir-relative id** (`explorations/…/results/cig_ds`)
— which a collaborator doesn't have. A pack instead addresses every model **self-contained**:

| pack field | panel ref | how it samples |
|---|---|---|
| `ckpt: tinker://…/sampler_weights/000123` | `ckpt:<path>` | straight through tinker's oai `/chat/completions` — **no run dir, no discovery** |
| `base: deepseek-ai/DeepSeek-V3.1` | `base:<model>` | raw base model (no LoRA) |
| `openrouter: deepseek/deepseek-chat-v3.1` | `openrouter:<id>` | OpenRouter reference |

A **published** Tinker checkpoint keeps the **same sampler id** as the private path, so the
`ckpt:` value in the pack works as-is on anyone's account (they supply their own
`TINKER_API_KEY`). Export therefore rewrites any bare discovered-run panel ref into its
checkpoint's `ckpt:` sampler path automatically.

## The file

```yaml
version: 1
name: weird-personas — implausible traits
description: Public char-SFT checkpoints + probe workspaces

models:
  - {label: "health×cig ds (ep1)", ckpt: "tinker://…/sampler_weights/000123"}
  - {label: "deepseek base",       base: "deepseek-ai/DeepSeek-V3.1"}
  - {label: "ds-chat (OR)",        openrouter: "deepseek/deepseek-chat-v3.1"}

defaults:                       # → prefs.json last_session (params + which models open)
  temperature: 1.0
  n_samples: 8
  thinking: false
  panels: ["health×cig ds (ep1)", "deepseek base"]   # by LABEL; must be in models

workspaces:                     # inline, self-contained; raw request/response kept, logprobs stripped
  - {name: "health-cig probes", body: { …light conversation body… }}
```

`defaults` accepts any of `temperature, max_tokens, n_samples, thinking, top_p, top_k,
presence_penalty, repetition_penalty, panels`. (A global system prompt is **not** part of
session persistence, so it can't be seeded here — put per-workspace prompts on the
workspaces instead.)

## `tinkerscope --pack <file|url>` (consumer)

Seeds the state dir for the scanned folder, then serves. **Merge-safe** by design:

- **pack models** → `pack_models.json` (per state dir), surfaced in the browser's
  "+ Tinker model" typeahead via `GET /api/tinker-models` → addable, first-class models.
  Upserted (deduped by ref) — **always**.
- **OpenRouter refs** → the global `openrouter_models.json` — upserted, **always**.
- **workspaces** → installed under a deterministic id (`pack-<pack>-<workspace>`), so
  re-applying updates in place instead of piling duplicates — **always**.
- **default params + panel layout** → `prefs.json` **only if the folder is fresh** (no
  prefs yet). Pass `--force` to overwrite. This is the only destructive part, so it's the
  one protected — re-applying a pack never clobbers a collaborator's own params.

A published checkpoint needs no local dir; discovery is bypassed for `ckpt:` refs, so none
of the sampleability greying applies to pack-wired panels.

## `tinkerscope pack export [OUT] [flags]` (author)

Reads your live state dir → writes one YAML. If `OUT` exists it **merges into it** (keeps
hand-edited `name`/`description`/labels, upserts models, honors `--exclude-model`), so you
maintain one committed file. `--overwrite` regenerates from scratch.

| flag | effect |
|---|---|
| `--dir D` (repeatable) | scan root(s) whose state to export (default: cwd) |
| `--models-from panels\|workspaces\|all\|runs` | where to gather models (default `all` = current panels + workspaces + already-registered pack models; `runs` also converts every discovered run's checkpoint) |
| `--include-model SUBSTR` / `--exclude-model SUBSTR` (repeatable) | keep-only / drop by label or ref match |
| `--no-workspaces` / `--workspace NAME` (repeatable) | drop all / keep-only named workspaces |
| `--name` / `--description` | override pack metadata |
| `--overwrite` | regenerate instead of merging into an existing file |

Export **keeps each node's `raw_meta`** (the raw request/response, inlined) so a
collaborator's "Raw" view shows what was actually sent, but **strips `token_logprobs`**
(~90% of a heavy conversation's bytes, and a pack is one self-contained YAML). On apply,
the inlined `raw_meta` is split back into a write-once blob the browser fetches lazily.

## Where things live

- **Code:** `src/tinkerscope/pack.py` (format + `apply_pack` + `export_pack` + `StateReader`),
  `src/tinkerscope/api/pack_models_store.py` (the per-state-dir registry + tinker-models
  merge), CLI in `src/tinkerscope/serve.py` (`--pack`, `pack export`).
- **State:** `<state_dir>/pack_models.json` (per scan-root set), the global
  `openrouter_models.json`, and workspaces in the conversation store.
- **Tests:** `tests/test_pack.py`; CLI smoke `tests/small-smokes/pack_cli_smoke.py`.
