---
name: tinkerscope
description: Chat with / sample from Tinker training-run checkpoints together with the human via tinkerscope — start the server, then drive the human's browser playground from the terminal with the `tinkpg` CLI (select a run, stream a chat, draw an n-sample distribution, compare two runs). Use when the human wants to poke at a fine-tuned checkpoint's behavior, browse discovered Tinker runs, see what a model "usually says" to a prompt, or A/B two runs — and when you want them to watch the samples land in their browser instead of pasting walls of text.
---

# tinkerscope

A local web playground that auto-discovers Tinker training runs under a
directory tree and lets you chat with / sample from their checkpoints, plus a
`tinkpg` CLI that drives the SAME view the human has open in their browser. You
trigger a chat from your shell; their screen fills in live, and the completion
also streams to YOUR stdout — so you read the samples directly while showing
them the human.

## Start (or find) the server

```bash
tinkpg state                       # a server already running for this cwd? (auto-discovery probe)
tinkerscope <dirs> --port N        # scan <dirs> for runs (checkpoints.jsonl + config.json); prints the URL
```

- Run the server in the background; it stays up. Default port = first free from
  8765. Relaunching on the SAME scan roots points you at the existing instance
  instead of starting a twin (so pass a different `--port` for a new root set).
- Give the human the printed URL (e.g. http://127.0.0.1:8809).
- `tinkpg` auto-targets the running instance whose scan root contains your cwd.
  Outside any scan root: set `TINKERSCOPE_BASE_URL` or pass `--base-url`.

## Drive the shared playground

```bash
tinkpg ls [--filter SUB] [--sampleable-only]      # discovered runs (id, base_model, #ckpts, sampleable)
tinkpg checkpoints <run>                            # a run's checkpoints (name, step, has-sampler)
tinkpg open <run>[@ckpt]                            # select a run in the human's browser (single mode)
tinkpg chat <run>[@ckpt] "<prompt>" [opts]          # sample; streams to stdout + browser
tinkpg compare <run_a>[@ckpt] <run_b>[@ckpt] "<prompt>" [opts]   # A→left pane, B→right pane (REPLACES the layout)
tinkpg send "<prompt>" [opts] [--panel P ...]       # NEW THREAD at the CURRENT panels — layout untouched (the safe probe)
tinkpg state [--full] [--width N] [--no-link] [--json] [--include-folded]   # DIGEST of on-screen panels (active path + matched saved conv)
tinkpg conv                                         # list saved WORKSPACES + branch metadata (alias: tinkpg ws)
tinkpg conv <id|name> [--panel P] [--full] [--tree] [--include-folded]  # expand one: active branch + fork counts (--tree = all branches)
tinkpg samples [conv] [--panel P] [--thread K] [--turn N] [--full]  # ALL n-sample siblings at one fork, each w/ CoT + a <tag> verdict tally
tinkpg refresh                                      # rescan filesystem + re-probe sampling capability
```

`chat`/`compare` options: `--n N` (samples), `--temperature T`, `--max-tokens M`,
`--thinking` (thinking renderer), `--thinking-both` (n samples WITHOUT thinking +
n WITH in one chat — 2n total, no-think half first; overrides `--thinking`),
`--system "…"`, `--checkpoint NAME` (overrides `@`). `tinkpg <cmd> --help` for
the rest.

## Reading state vs. workspaces (they are DIFFERENT stores)

Vocabulary: the saved container (panels + branch trees) = a **workspace**; a
branch-from-start first message starts a **thread**. The wire keeps legacy
naming (`/api/conversations`, `conversation_id`, `?c=`) — read "conversation"
in endpoint/field names as "workspace".

- `tinkpg state` shows the **live panels** — the transient on-screen selection +
  each panel's LINEAR active path (the server's state bus has no branches). It's a
  compact digest (first-2/last-2 messages, whitespace-collapsed): `--full` for the
  whole path, `--json` for the raw untruncated state (escape hatch). Do NOT expect
  branches here. It also names the OPEN workspace up top — `open workspace:
  <name> (id) → tinkpg conv <id>` — because the browser pushes its `?c=`
  conversation_id onto the state bus, so you can jump straight to its branches. If
  that id is absent (older browser, or a CLI-only session that never opened a saved
  conversation), it falls back to a per-panel EXACT active-path match (`← conv:
  <name>`, or an honest `ambiguous ×N` when a short path is shared). `--no-link`
  skips the conversations fetch entirely. Panels the human has FOLDED in the
  browser print as one-line stubs here too — `--include-folded` expands them
  (fold info rides the open conversation, so `--no-link` shows every panel).
- `tinkpg conv` (alias `ws`) reads the **saved workspace trees** (`/api/conversations`) —
  this is the ONLY place branches live. The tree is opaque to the server; the CLI
  walks it client-side (mirrors `web/src/lib/tree.ts`). List shows per-conversation
  `nodes` / `branches` (total forks) / `active` (per-panel active-path length).
  Expanding annotates each active turn that sits at a fork as `·k/N` (branch k of
  N), reports forks-on-path per panel, and `--tree` prints the full branch
  structure with `*` marking the active branch. A panel with multiple ROOT
  threads (branch-from-start first messages — the human often probes several
  prompts in one conversation) gets a `threads:` index: one line per thread with
  its first message + fan-out size, `*` = active; those `k` numbers feed
  `samples --thread k`. Panels the human has FOLDED in
  the browser UI print as one-line stubs (skipped, with a trailing "N folded
  panel(s) skipped" list) — `--include-folded` expands them all, and an explicit
  `--panel` always overrides the fold. The live panels correspond to a
  saved conversation but there's no stored link — match by name/recency.
- `tinkpg samples` answers "what did the model say across ALL n draws at this fork?"
  — the one view `state`/`conv` can't give you, since they only walk the linear active
  path. It prints every sibling response at ONE fork (default: the last user turn of the
  open conversation, resolved via the pushed conversation_id; `--turn N` / `--panel P` /
  `--thread K` to aim it — `--thread` reaches NON-active root threads, which no
  active-path view shows; the default panel is the first non-folded one), each with its
  CoT (`--full` for complete reasoning), the active one `*`-marked.
  When the answers carry `<tag>X</tag>` verdicts it tallies them (`GOLD ×1 · CONCERNING
  ×11`) and flags doubled-draft samples (>1 tag — the nemotron generation glitch) so you
  don't miscount. Use it whenever you fan out n>1 and want the distribution, not one path.

## Levers & gotchas the reader won't guess

- **Live drive is the point.** `chat`/`open` broadcast to a server-side state
  bus, so a CLI-triggered chat appears in the human's browser identically to one
  they typed. Best way to *show* them a checkpoint's behavior: `open` the run,
  fire a `chat`, tell them to watch — richer than pasting the sample.
- **⚠️ `open`/`chat`/`compare` REPLACE the browser's panel layout.** They push a
  full `panels` list onto the shared bus, so the human's multi-panel workspace
  reshapes live (and mid-generation state can be lost). Before firing any of
  them, run `tinkpg state`: if the human has a many-panel workspace open (or
  `running=yes`), don't — **use `tinkpg send` instead**, which fires at the
  panels as they are (new thread, layout untouched, refuses while `running=yes`
  unless `--force`). Reading (`state`/`conv`/`samples`) never writes and is
  always safe.
- **Run resolution: ids contain `/`, never split on it.** A run arg resolves by
  exact id, else a UNIQUE case-insensitive substring of id/name (ambiguity errors
  and lists candidates) — so pass the shortest unique substring. Use `@` for
  `run@checkpoint` (`tinkpg chat foo/bar@final "hi"`) or `--checkpoint`. Omit the
  checkpoint and it defaults to the last one with a sampler (usually `final`).
- **`sampleable` is tri-state.** true / false / null. Roughly half the runs are
  `false` because their base model is no longer served by Tinker — chatting them
  refuses cleanly. `--sampleable-only` filters `ls` to the ones that work. null =
  unknown (Tinker offline / no key); the CLI passes through and lets the server
  decide, warning once.
- **n==1 streams tokens; n>1 draws a distribution.** Default `--n 1` streams the
  completion token-by-token (inline to your stdout, and into the browser). `--n
  20` fans out whole samples and the browser shows an answer-distribution chart —
  use it for "what does this model *usually* say to X". With `--thinking`,
  reasoning streams first, before the answer (dimmed in a real terminal,
  prefixed `[thinking]` when piped/captured).
- **The browser has model kinds the CLI doesn't drive.** `tinkpg` targets LoRA
  training runs by id. The browser's "+ Tinker model" typeahead additionally
  offers raw base models (no LoRA) and loose sampler checkpoints (UUID-only,
  picked by id/UUID) — those are browser-only selections for now.

## Collaboration patterns

- **Survey the human's probe workspace** (many panels, several prompts):
  `tinkpg state` (which models are live now — folded panels collapse to stubs) →
  `tinkpg conv <id>` (per-panel thread index + forks) → `tinkpg samples --panel P
  --thread k` for each interesting fan-out. All read-only; folded panels stay out
  of the way by default.
- **Add a probe to the human's workspace**: `tinkpg send "<prompt>" --n 20` —
  fires a NEW thread at every unfolded panel; the browser folds the replies in
  live and the ⑂ threads popover picks it up. The layout-safe way to propose
  and run a new prompt on the models the human is already looking at.
- **"What does checkpoint X do here?"**: `tinkpg open <run>@<ckpt>` → `tinkpg chat
  <run> "<prompt>"` → human watches it stream; you read the same text in stdout.
- **Behavior distribution**: `tinkpg chat <run> "<q>" --n 30` → the browser's
  distribution chart shows the answer spread across samples.
- **A/B two runs / checkpoints**: `tinkpg compare <A> <B> "<q>"` (e.g.
  base-trained vs instruct-trained, or `run@early` vs `run@final`). Both panes
  stream side by side.
- **Peek at training data**: in the browser, the dataset icon loads the selected
  run's training JSONL into the prompt box for a quick "what was this trained on".
