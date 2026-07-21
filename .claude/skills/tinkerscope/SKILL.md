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
tinkpg continue "<follow-up>" [opts] [--panel P] [--thread K] [--turn N] [--node ID] [--ancestry-file FILE]   # LOOM: add a turn to existing thread(s), OR to an explicit external transcript
tinkpg battery <dir> [--n N] [--pause S] [--out DIR] [--panel P ...] [--no-first-token]   # fire a DIRECTORY of probe *.txt files as sequential sends (one probe = one thread)
tinkpg state [--full] [--width N] [--no-link] [--json] [--include-folded]   # DIGEST of on-screen panels (active path + matched saved conv)
tinkpg params [--temperature T] [--max-tokens M] [--n N] [--thinking/--no-thinking|--thinking-both] [--top-p P] [--system S|--system-file F|--clear-system]   # show / SET the GLOBAL sampling params (browser sidebar updates live)
tinkpg conv                                         # list saved WORKSPACES + branch metadata (alias: tinkpg ws)
tinkpg conv <id|name> [--panel P] [--full] [--tree] [--include-folded]  # expand one: active branch + fork counts (--tree = all branches)
tinkpg samples [conv] [--panel P] [--thread K|--node ID] [--turn N] [--sample K] [--slice S[:L]] [--full] [--first-token]  # ALL n-sample siblings at one fork + <tag> tally; --sample/--slice = read ONE sample in PIECES; --first-token = the model's P(first generated token) at this fork
tinkpg grep "<text>" [--conv WS] [--regex] [-i]     # search EVERY branch of all workspaces: content + thinking
tinkpg refresh                                      # rescan filesystem + re-probe sampling capability
```

`send`/`continue` also take `--logprobs` (per-token logprob + top-5 alts, native
tinker sampling only: `run_id` + `base_model` at any `n`. A single `n=1` fire to
a loose checkpoint or OpenRouter streams through a different, logprob-free path),
`--first-token` (print each panel's first-token probability table right after
the fire — same view as `samples --first-token` without a second command)
and `--json` (JSONL to stdout — one object per
sample + a closing `{"event":"done"}`; plan/progress text moves to stderr so
stdout stays parseable). `samples`/`grep` also take `--json` (one JSON object /
array, untruncated) — reach for these over regexing the human-formatted text
when you're going to tally/filter programmatically.

**`tinkpg battery <dir>` — the MCQ/probe-battery workhorse.** Fires every
`*.txt` in a directory (sorted) as sequential new-thread sends. A probe file =
optional `---` front-matter (`system:` — the probe's THREAD prompt, so one
file = one (message, system) thread identity; `no-system:`, `prefill:`, `n:`,
`temperature:`, `max-tokens:`, `thinking: on|off|both`, `panel: a,b`; unknown
keys hard-error) + the user message verbatim. CLI options are the defaults
probes don't override. Per-probe JSONL → `<dir>/results/` (`--out`); a
first-token table prints after each probe (`--no-first-token` to skip);
per-probe failures are non-fatal (summary + exit 1 at the end); `--pause`
(default 3 s) spaces the fires so the human can watch threads land in the
browser one by one.

`chat`/`compare` options: `--n N` (samples), `--temperature T`, `--max-tokens M`,
`--thinking` (thinking renderer), `--thinking-both` (n samples WITHOUT thinking +
n WITH in one chat — 2n total, no-think half first; overrides `--thinking`),
`--system "…"`, `--checkpoint NAME` (overrides `@`). `tinkpg <cmd> --help` for
the rest. `send` and `continue` add `--file <path>` (read the user message from a
file — a reusable probe template, mutually exclusive with the positional prompt)
and `--prefill-file <path>` (read the assistant prefill from a file).

**Params have two routes — per-call vs global.** Param args on
`chat`/`compare`/`send`/`continue` apply to THAT CALL ONLY: any param you don't
pass inherits the human's current global state (their sidebar), and nothing you
pass is written back — so a CLI probe never clobbers their setup. `--no-system`
fires with NO system prompt at all — global AND thread part (`--n` never
inherits; explicit, default 1). To DELIBERATELY change the shared state (the
human sees their sidebar update live), use `tinkpg params` — no options = show
current. Requires a server ≥ the params_scope contract (older servers apply the
legacy clobber-on-chat behavior).

**Thread system prompts (`send --system`).** A thread's first message can carry
its OWN system prompt, composed over the global one at fire time (`global ⏎
thread` — the global is the shared base, never clobbered). On `send` (always a
new-thread fire) `--system` sets the THREAD prompt: durable (recorded on the
thread's first message, shown in the browser as a `system` strip on the row +
in the ⑂ threads popover) instead of ephemeral. This is THE way to run a probe
battery: `tinkpg send --file q.txt --system "Answer with only the letter"`,
then again with other framings — same first message under different prompts =
distinct, cycleable threads. On `continue`, `--system` keeps its per-call
GLOBAL-part meaning; the thread part is inherited from the TARGET thread's
first message automatically (including `--node`/`--thread` targets on
non-active branches), so a continue into a probe thread stays under that
probe's prompt. `conv <id>`'s threads index prints each thread's `sys:` line;
`samples` shows the fork's thread prompt in its header.

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
- `tinkpg grep` is the FIND primitive: it scans every node of every branch
  (content AND `reasoning`/thinking) across all workspaces — the one command
  that reaches text on non-selected branches without `--tree` dumps. Hits are
  `workspace · panel · thread k · role · node id [thinking] + snippet`; feed a
  hit's node id to `samples --node <id>` to see the fan-out at that exact fork —
  the ONLY route to n-sample views on non-selected branches (--thread/--turn
  walk selected paths). Use grep FIRST when the human says "somewhere in my
  workspaces there's …".
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
- **n==1 reads one completion; n>1 draws a distribution.** Default `--n 1`
  returns a single completion — streamed token-by-token (inline to your stdout,
  and into the browser) for a loose checkpoint / OpenRouter, but whole-sample for
  a discovered run or base model (they sample native — no token stream). `--n
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
- **Loom / multi-turn a probe**: `tinkpg continue "<follow-up>" --n 20` adds a
  turn to the CURRENT thread at every panel (default target = the active leaf,
  read from live state). Aim it at a non-active branch with `--thread K` /
  `--turn N` (that panel's saved tree) or `--node <id>` (from `tinkpg grep`).
  A `--prefill "Hmm,"` (or `--prefill-file`) seeds a thinking opener / the
  model's own truncated CoT when the target ends on a user turn (answer-level
  loom). Same layout-safe, folded-via-the-browser path as `send`.
- **⚠️ A CLI fan-out (`--n K`) persists only ONE representative into the saved
  tree.** The server commits sample 0 to the panel transcript, and the browser
  folds a FOREIGN (CLI) chat via echo-reconcile = that one representative — only
  the browser's OWN sends fold all K siblings from the bus bucket. So `tinkpg
  samples` on a CLI-fired fan-out shows 1, not K. **The full K-sample fan-out
  streams to the CLI's stdout** (each `--- sample i ---` block with its CoT +
  finish_reason) — capture that (`… > log.txt`) for the distribution / a
  `<tag>` tally; the workspace keeps the representative + thread structure.
  Because of the fold-one-rep limitation above, the BEST example of a behavior
  from a wide fan-out often lives ONLY in that captured stdout, not in any tree
  — `continue --ancestry-file <path>` (a JSON list of `{role, content}` dicts)
  looms from it directly, no tree node needed.
- **Provenance rule for looming (`continue`/`--ancestry-file`).** OK: a full,
  VERBATIM, previously-generated conversation as ancestry — from a tree, a raw
  log, or another model entirely (grafting a real conversation model A produced
  into model B's context to see how B judges/continues it is a legitimate
  probe design); a tiny `--prefill` thinking-opener ("Hmm,"); continuing a
  model's own truncated CoT verbatim. NOT ok, ever: authoring or editing any
  part of a turn yourself — a hand-written or hand-edited assistant message, a
  partial answer you completed, a doctored transcript. The line is authored
  vs. generated, not fresh vs. reused — a full real transcript from anywhere is
  fine; one fabricated sentence anywhere in it is not.
- **"What does checkpoint X do here?"**: `tinkpg open <run>@<ckpt>` → `tinkpg chat
  <run> "<prompt>"` → human watches it stream; you read the same text in stdout.
- **Behavior distribution**: `tinkpg chat <run> "<q>" --n 30` → the browser's
  distribution chart shows the answer spread across samples.
- **A/B two runs / checkpoints**: `tinkpg compare <A> <B> "<q>"` (e.g.
  base-trained vs instruct-trained, or `run@early` vs `run@final`). Both panes
  stream side by side.
- **Peek at training data**: in the browser, the dataset icon loads the selected
  run's training JSONL into the prompt box for a quick "what was this trained on".
