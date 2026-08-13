---
name: cli
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
- `tinkerscope <dirs>` is shorthand for `tinkerscope serve <dirs>` — a scan root
  named like a command (`pack`, `send`, …) needs the explicit `serve` form.
- Give the human the printed URL (e.g. http://127.0.0.1:8809).
- `tinkpg` auto-targets the running instance whose scan root contains your cwd.
  Outside any scan root: set `TINKERSCOPE_BASE_URL` or pass `--base-url`.

**Share packs (reproduce a setup).** (`tinkpg` and `tinkerscope` are the same
binary since 2026-08-12 — these work under either name; they're documented as
`tinkerscope` because they touch the state dir, not a running server.)
`tinkerscope --pack <file|url>` seeds this folder from a portable YAML bundle (public
checkpoints + default params + workspaces) then serves — hand a collaborator a
reproducible setup with no local run dirs. `tinkerscope pack export <out.yaml> --dir
<scan-root> [--workspace NAME] [--models-from panels|workspaces|all|runs] [--exclude-model SUBSTR] [--no-defaults]`
authors one from the live state. Two gotchas: **`--dir` must be the scan root the running
instance was launched with** (find it: `ps aux | grep '[t]inkerscope'`, or the state dir
won't match and export reads nothing); and `--workspace NAME` exports ONE saved workspace
(omit → all). Packs carry only models + params + workspaces — never highlights/pins
(`--no-defaults` drops the params too). Export keeps each node's `raw_meta` (the Raw view)
and strips `token_logprobs` unless you pass **`--logprobs`** — which you should pair with a
`.gz` output path, since the uncompressed form of a real workspace is 107 MB and GitHub
hard-blocks files over 100 MB (gzipped: 30 MB). Both `--pack` and `?w=` un-gzip
transparently, sniffing the magic bytes rather than the extension.
Merge-safe (won't clobber params without `--force`).
**Iterating on a pack** (re-export → re-consume the SAME folder)? A plain re-apply keeps
write-once blobs and never drops workspaces — use `tinkerscope --pack <file> --reseed` to
mirror the file exactly (refresh `raw_meta` blobs, drop removed workspaces, overwrite params).
Full flags + format: `docs/PACK.md`.

**A pack is also a LINK** — no restart needed. Open `<base>/?w=<path-or-url-to-pack>`
in the human's browser (`&open=<workspace-id>` picks which one lands open) and it
installs in place. A `?w=` value containing `/`, `:` or `.` can't be a workspace id,
which is how the two are told apart. ⚠️ **Against the human's LIVE instance — which is
what you are usually driving — expect a dialog every time**: a first, non-colliding open
asks Install/Cancel, and an existing copy asks replace-vs-keep-both. Don't tell them a
link "just opens"; tell them to expect it. (On a *published static site* a non-colliding
open installs unprompted — that install lands in their browser, not in a state dir — but
a collision still asks there too.) The URL then becomes the plain
`?w=<id>`, so a reload opens rather than re-installs. Same thing over HTTP:
`POST /api/pack/apply {source}` previews (which ids exist), `{source, on_conflict}`
applies (`"overwrite"` | `"new"`) — the HTTP route has no prompt, so prefer it when
you're scripting and the human has already agreed.

**Publish a read-only copy — `tinkerscope site export <dir> [--dir <scan-root>]
[--title T] [--workspace NAME] [--logprobs all|chart|last:N|none] [--open <ws-id>] [--pack-url URL]
[--pack-link URL|PATH=URL]`.** Emits a static
site (built SPA + baked JSON) that needs no backend and no API key — GitHub Pages, S3,
`python3 -m http.server -d <dir>`. Visitors can read everything (workspaces, branches,
threads, the chart in all three modes, token probabilities, editable highlight rules,
pins) and sample nothing. Same `--dir` gotcha as `pack export`. ⚠️ **Per-token
logprobs are ~97% of a real store's bytes** (measured: 24 MB of workspaces vs 901 MB
of blobs) — the command prints a per-workspace breakdown and warns past 100 MB; use
`--workspace` to publish a subset, or narrow the logprobs themselves:
`--logprobs chart` keeps only the turn each workspace's saved chart view opens on (so
the published chart still works, and everything else loses the token inspector),
`--logprobs last:N` the newest N turns per thread, `--logprobs none` nothing
(`--no-logprobs` is an alias). A workspace with no saved chart view keeps all of its
logprobs and the export says so. Unlike a share pack, a site export DOES carry
logprobs by default, plus the chart's per-workspace view state. `--workspace` also
drops saved PINS (they carry responses + a local `dataset_path` and have no workspace
id to filter on — `--pins` forces them back) and narrows the chart-view state to the
exported workspaces. Full doc: `docs/STATIC_SITE.md`.

A published site is **also a reader for other people's packs**, so one deployment can
serve many workspaces without re-publishing: `<site>/?w=<pack url>` installs into that
visitor's browser (IndexedDB, so size is not a constraint), and the human can open a pack
straight off their disk with the ⤒ button beside the workspace picker or by dropping the
file on the page. A relative `?w=./demo.yaml.gz` works too, for a pack published next to
its viewer. So "share this workspace" can mean pushing one `.yaml.gz` rather than
re-exporting the whole site — the trade-off being that a `?w=` link is a fetch
instruction, not a permalink: if the pack URL dies, so does the link.

**Pass `--pack-url <url>` when you publish both.** The site's read-only badge opens an
"open this locally" panel that turns it into a runnable `--pack` command, so a reader can
go interactive in one copy-paste. Without it the panel has to tell them the command
starts an EMPTY tinkerscope and won't reproduce the page — correct, but a dead end.

**Pass `--pack-link <url>` (or `<local path>=<url>`, repeatable) to make the resulting
`?w=<id>` links shareable.** After a visitor installs a pack the URL becomes the tidy
`?w=pack-<pack>-<ws>` — but that id resolves only in the browser that already installed
it; anyone else gets "workspace not found". `--pack-link` bakes `{workspace id: pack
URL}` into the manifest, so an unknown id fetches its pack (with a progress modal)
instead. The exporter reads each pack to derive the ids and PRINTS them — that printed
`?w=…` is the link to hand out. Use `PATH=URL` when the file isn't uploaded yet. One
`--pack-link` and no `--pack-url` implies the latter.

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
tinkpg url [ws] [--live] [--json]                   # the URL of the server you're driving — what to hand the human when they want a LINK
tinkpg state [--full] [--width N] [--no-link] [--json] [--include-folded]   # DIGEST of on-screen panels (active path + matched saved conv)
tinkpg params [--temperature T] [--max-tokens M] [--n N] [--thinking/--no-thinking|--thinking-both] [--top-p P] [--system S|--system-file F|--clear-system]   # show / SET the GLOBAL sampling params (browser sidebar updates live)
tinkpg ws                                         # list saved WORKSPACES + branch metadata (alias: tinkpg conv)
tinkpg ws <id|name> [--panel P] [--full] [--tree] [--include-folded] [--thread K] [--deepest] [--json]  # expand one: active branch + fork counts (--tree = all branches; --thread/--deepest = read a NON-active conversation; --json = export the transcript)
tinkpg threads [--min-turns N] [--ws W] [--model SUB] [--grep TXT] [--json]  # cross-workspace index of EVERY root thread + its deepest-branch turn count
tinkpg probe <run>[@ckpt] "<prompt>" [--n N] [--ancestry-file F] [--json]  # sample ANY model off-workspace: nothing broadcast, nothing committed
tinkpg samples [conv] [--panel P] [--thread K|--node ID] [--turn N] [--sample K] [--slice S[:L]] [--full] [--first-token]  # ALL n-sample siblings at one fork + <tag> tally; --sample/--slice = read ONE sample in PIECES; --first-token = the model's P(first generated token) at this fork
tinkpg grep "<text>" [--ws WS] [--regex] [-i] [--link]  # search EVERY branch of all workspaces: content + thinking + system prompts (server-side; = the browser's Ctrl+K); --link appends a ?w=…&node=… deep-link URL per hit that opens the browser AT the match
tinkpg node <handle> [--ws WS] [--logprobs] [--meta] [--raw] [--full] [--json] [--link]  # reverse lookup: locate a bare node id (no ws/panel needed), dump its record + blobs
tinkpg trash list [--workspace W] [--json]          # what's recoverable: every save that made nodes disappear, newest first
tinkpg trash restore <handle> --workspace W         # splice a deleted branch back at its original sibling index
tinkpg trash purge --workspace W                    # forget one workspace's journal
tinkpg refresh                                      # rescan filesystem + re-probe sampling capability
```

**Deleting is recoverable, and you are the recovery path.** Every node a save
makes disappear is journaled server-side, so a branch deleted in the browser
survives the tab that deleted it. Two things to know:

- **Start with `trash list`.** A `<handle>` is an entry id, a deleted subtree's
  root node id, or any node id inside it — but nobody remembers the id of the
  thing they just deleted, so list first and copy one from the table.
- **Tell them to reload the tab after a restore.** The browser is still the sole
  writer of trees; an open tab holds the post-delete tree, and its next save
  re-deletes what you just put back. (The restore is re-runnable if that happens.)

Blobs are write-once and never removed, so a restored turn comes back with its
`token_logprobs` and `raw_meta` intact — `tinkpg node <id> --logprobs` on it works
immediately. Deleting a WORKSPACE is soft too (moved to
`workspaces/.deleted/<id>-<ts>/` for 90 days), but that one has no CLI front end
yet: move the directory back by hand and restart the server.

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
The human can MUTE the global system prompt in the browser (the split-chip
power dot: text kept, not applied) — `params`/`state` then show it as
`(muted)` and per-call inherits skip it. `params --system "…"` always
re-enables; an explicit per-call `--system` applies regardless of the mute.

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

Panel ids (`p-1`, `p-2`, …) are minted monotonically per workspace and NEVER
reused, so `<panel>:<node>` stays valid — closing a column doesn't free its id for
a different model. Workspaces saved before that change also carry the old reserved
names `primary` / `compare`; those remain valid ids, they're just never minted now.
`open`/`chat`/`compare` REPLACE the layout, but they reuse the ids already on
screen (and mint any extra above the workspace's counter) — so a handle copied
before such a fire still names the same column. What those commands do change is
which MODEL that column is bound to, so a handle stays a valid address while
ceasing to describe the model that produced the turn; re-read provenance from
`raw_meta` rather than the panel label.

Vocabulary: the saved container (panels + branch trees) = a **workspace**; a
branch-from-start first message starts a **thread**; a **conversation** is one
dialogue inside one panel. The wire matches (`/api/workspaces`, `workspace_id`,
`?w=`) as of v1.0.0.

- `tinkpg state` shows the **live panels** — the transient on-screen selection +
  each panel's LINEAR active path (the server's state bus has no branches). It's a
  compact digest (first-2/last-2 messages, whitespace-collapsed): `--full` for the
  whole path, `--json` for the raw untruncated state (escape hatch). Do NOT expect
  branches here. It also names the OPEN workspace up top — `open workspace:
  <name> (id) → tinkpg ws <id>` — because the browser pushes its `?w=`
  workspace_id onto the state bus, so you can jump straight to its branches. If
  that id is absent (older browser, or a CLI-only session that never opened a saved
  workspace), it falls back to a per-panel EXACT active-path match (`← ws:
  <name>`, or an honest `ambiguous ×N` when a short path is shared). `--no-link`
  skips the workspaces fetch entirely. Panels the human has FOLDED in the
  browser print as one-line stubs here too — `--include-folded` expands them
  (fold info rides the open workspace, so `--no-link` shows every panel).
- `tinkpg ws` (alias `conv`) reads the **saved workspace trees** (`/api/workspaces`) —
  this is the ONLY place branches live. The tree is opaque to the server; the CLI
  walks it client-side (mirrors `web/src/lib/tree.ts`). List shows per-workspace
  `nodes` / `branches` (total forks) / `active` (per-panel active-path length).
  Expanding annotates each active turn that sits at a fork as `·k/N` (branch k of
  N), reports forks-on-path per panel, and `--tree` prints the full branch
  structure with `*` marking the active branch. A panel with multiple ROOT
  threads (branch-from-start first messages — the human often probes several
  prompts in one workspace) gets a `threads:` index: one line per thread with
  its first message + fan-out size, `*` = active; those `k` numbers feed
  `samples --thread k`. Panels the human has FOLDED in
  the browser UI print as one-line stubs (skipped, with a trailing "N folded
  panel(s) skipped" list) — `--include-folded` expands them all, and an explicit
  `--panel` always overrides the fold. The live panels correspond to a
  saved workspace but there's no stored link — match by name/recency.
- `tinkpg threads` is the FIND primitive for CONVERSATIONS (grep finds text; this
  finds shapes). One row per root thread across every workspace, with `deep` = user
  turns on the thread's LONGEST branch and `turns` = on its selected one. That gap is
  the whole point: the selected child defaults to the NEWEST, so a long conversation
  the human later re-rolled reads as 1 turn in `ws`/`state` and is invisible. Reach for
  it on "where are my multi-turn / long conversations?", `--min-turns 3` to cut the
  one-shot probes, `--model health_cigarette` to scope to one checkpoint family.
- Reading a NON-active conversation: `tinkpg ws <id> --panel P --thread K --deepest
  --full`. `--thread K` walks root thread K (numbers from `threads` / the `threads:`
  index) instead of the active one; `--deepest` follows its longest branch instead of
  the selected one. Without these, a thread the panel no longer points at can be
  *listed* but never *read* — `samples --thread K` only shows one fork's fan-out.
- **Exporting a conversation to render elsewhere** (a report, an artifact, a diff):
  `tinkpg ws <id> --panel P --thread K --json` emits the selected transcript as
  structured data — untruncated content, CoT, node ids, and each turn's
  `sibling_index`/`n_siblings` so a quoted sample stays citable. Build rendered
  transcripts from THIS, never by re-parsing the terminal text (it truncates, and
  its `⟨thinking⟩`/`⟨answer⟩` framing is display sugar). `--json` with no selector
  lists workspaces instead.
- `tinkpg grep` is the FIND primitive for TEXT: it scans every node of every branch
  (content, `reasoning`/thinking, AND thread system prompts) across all
  workspaces — the one command that reaches text on non-selected branches without
  `--tree` dumps. Since 2026-08-10 it runs SERVER-SIDE (`GET /api/search`, the
  same engine as the browser's Ctrl+K palette) instead of pulling every body over
  the wire, and workspace-LEVEL matches (name / panel model id / global system
  prompt) print before node hits. Hits are `workspace · panel · thread k · role ·
  node id [thinking|system] + snippet`; `--json` hits also carry `parent`,
  `on_active_path`, `sib_index`/`sib_count` and the snippet triple. Feed a hit's
  node id to `samples --node <id>` to see the fan-out at that exact fork — the
  ONLY route to n-sample views on non-selected branches (--thread/--turn walk
  selected paths). Use grep FIRST when the human says "somewhere in my
  workspaces there's …". **`--link` (also on `tinkpg node`) appends a
  `?w=<id>&panel=<p>&node=<n>` deep-link URL per hit** — hand the human that
  instead of a location table: clicking it opens their browser at the exact
  sample, cyclers flipped, row flashed (works on hidden branches).
- `tinkpg node <id>` is the FIND primitive for a NODE ID: when you hold a bare id
  (the human pasted one from the browser's Copy-node-id button, or `grep`/`samples
  --json` printed it) it locates the workspace · panel · thread with no other
  context and dumps the node record — role, sibling k/N, parent/children,
  finish_reason, and crucially `prefill` (a Continue/authored prefix; the stored
  token stream only covers what came AFTER it — the cause when token counts look
  short) plus which heavy blobs exist. `--logprobs` prints the stored per-token
  stream + top-K alts, `--meta` the request/response record, `--raw` the raw
  stream text. Reach for it BEFORE grepping state files or hand-parsing
  workspace JSON — it replaces both.
- **Node handles are `<panel>:<node>`.** An id is unique per WORKSPACE, not per
  panel — a tree cloned into another panel keeps its ids — so one id often names
  the same turn in several panels. The browser's Copy-node-id button therefore
  hands out `p-4:nt03f1`, and every `--node` (plus `tinkpg node <handle>`) takes
  `<node>`, `<panel>:<node>` or `<ws>:<panel>:<node>`; the parts fill in `--panel`
  / the workspace, and an explicit flag wins. A BARE id still works — `node`
  prints one block per copy, while `continue --node` / `samples --node` error and
  ask for `--panel`.
- `tinkpg samples` answers "what did the model say across ALL n draws at this fork?"
  — the one view `state`/`conv` can't give you, since they only walk the linear active
  path. It prints every sibling response at ONE fork (default: the last user turn of the
  open workspace, resolved via the pushed workspace_id; `--turn N` / `--panel P` /
  `--thread K` to aim it — `--thread` reaches NON-active root threads, which no
  active-path view shows; the default panel is the leftmost non-folded one), each with its
  CoT (`--full` for complete reasoning), the active one `*`-marked.
  When the answers carry `<tag>X</tag>` verdicts it tallies them (`GOLD ×1 · CONCERNING
  ×11`) and flags doubled-draft samples (>1 tag — the nemotron generation glitch) so you
  don't miscount. Use it whenever you fan out n>1 and want the distribution, not one path.

## Levers & gotchas the reader won't guess

- **Live drive is the point.** `chat`/`open` broadcast to a server-side state
  bus, so a CLI-triggered chat appears in the human's browser identically to one
  they typed. Best way to *show* them a checkpoint's behavior: `open` the run,
  fire a `chat`, tell them to watch — richer than pasting the sample.
- **`tinkpg url` when they ask for a link.** Every command auto-discovers the
  running instance, so you can read a whole workspace without ever learning the
  URL — and then have nothing to hand over. `url` prints it (bare on stdout, so
  `open $(tinkpg url)` works); `url <ws>` or `url --live` prints a `?w=<id>` link
  that OPENS that workspace. `state`'s header carries both too. Don't reach for
  `ps aux | grep tinkerscope` — with two instances running you'd have to guess
  which one holds the workspaces you just read, and `url --json` answers that
  (`pid`, `scan_roots`). A `?w=` link lands on the WORKSPACE, at whatever branch
  is selected — to point at a TURN, use `grep --link` / `node --link`, which
  append the `?w=…&panel=…&node=…` form that opens the browser AT the match.
- **Workspace selector: positional or `--ws`, both work.** `ws`/`samples` take it
  positionally (the workspace is the subject) while `grep`/`node`/`threads` must
  use `--ws` (their positional is the pattern / node id). Since that's easy to
  get backwards mid-session, `ws`/`samples`/`url` accept `--ws` as well; passing
  two *different* ones errors rather than picking.
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

- **"Find the conversation where the model did X"** (the human half-remembers a chat
  across 20+ workspaces): `tinkpg threads --min-turns 2 [--model SUB]` to get every
  multi-turn candidate with a locator → `tinkpg grep "<phrase>"` if you have a phrase →
  read each candidate with `tinkpg ws <ws_id> --panel P --thread K --deepest --full`.
  Fan the reads out over subagents when there are more than a handful — they're
  read-only and each report comes back with verbatim quotes.
- **Sample a model no panel is bound to**: `tinkpg probe <run>[@ckpt] "<prompt>" --n 8
  --json`. `chat`/`compare` reshape the layout and `send`/`continue` fire at the panels
  as they are, so all three are limited to models already on screen — and all three
  COMMIT a turn into a panel transcript. `probe` sends `broadcast=false, commit=false`:
  it touches no workspace at all. Reach for it whenever you want a distribution from a
  checkpoint the human isn't looking at, and for anything you intend to quote as
  "model X said" — provenance is guaranteed by construction. Multi-turn via
  `--ancestry-file` (a JSON list of {role, content}; same provenance rule as
  `continue` — reuse generated turns, never author them).
- **⚠️ Provenance: a panel's label is not a turn's author.** A panel says what it is
  bound to NOW; a turn in its tree may have been produced by another model (pasted,
  loomed, or committed by a chat that named this panel while sampling something else).
  Real saved workspaces on this box contain exactly that. Before quoting a stored turn
  as evidence about a model, check the node's `raw_meta`, which records the sampler
  that produced it: `POST /api/workspaces/<id>/node-blobs {"nodes": [...]}` returns
  `{node_id: {raw_meta, token_logprobs?}}`, and the `tinker://<uuid>:train:0/
  sampler_weights/<ckpt>` path in its request section maps to a run via
  `/api/models` (each checkpoint's `sampler_path`). Nodes with no blob have NO
  provenance — treat them as unverified, and re-`probe` rather than trust them.
- **Survey the human's probe workspace** (many panels, several prompts):
  `tinkpg state` (which models are live now — folded panels collapse to stubs) →
  `tinkpg ws <id>` (per-panel thread index + forks) → `tinkpg samples --panel P
  --thread k` for each interesting fan-out. All read-only; folded panels stay out
  of the way by default.
- **Add a probe to the human's workspace**: `tinkpg send "<prompt>" --n 20` —
  fires a NEW thread at every unfolded panel; the ⑂ threads popover picks it up.
  The layout-safe way to propose and run a new prompt on the models the human is
  already looking at.
- **Loom / multi-turn a probe**: `tinkpg continue "<follow-up>" --n 20` adds a
  turn to the CURRENT thread at every panel (default target = the active leaf
  of the saved tree). Aim it at a non-active branch with `--thread K` /
  `--turn N` (that panel's saved tree) or `--node <id>` (from `tinkpg grep`).
  A `--prefill "Hmm,"` (or `--prefill-file`) seeds a thinking opener / the
  model's own truncated CoT when the target ends on a user turn (answer-level
  loom). Same layout-safe path as `send`.
- **A CLI fan-out persists in FULL, with no browser attached.** With a
  workspace open on the bus, `send`/`continue` write the user turn as their own
  op and the SERVER folds all `--n K` replies at terminal — K assistant
  siblings in the saved tree (‹k/N› cycler in the browser), each with its CoT
  (`reasoning`), `token_logprobs` and `raw_meta` blobs, durable across a server
  restart. `tinkpg samples` on the fan-out shows all K. The full fan-out also
  streams to stdout as before (capture with `… > log.txt` for a quick `<tag>`
  tally). If the fold FAILS (workspace deleted/replaced mid-fire), the CLI says
  so and exits non-zero — stdout is then the only copy. The exceptions, each
  loud or documented: a fire with NO workspace anywhere (pure lockstep) is
  echo-only; `chat`/`compare` advance only the live transcript, never the tree
  (a following bare `continue` warns when the two diverge — aim it with
  `--thread`/`--node`, or use `send` for persisted threads); a `continue` from
  a panel with no saved tree falls back to the live transcript and warns that
  nothing will persist; `--ancestry-file` looms from an external transcript
  and stays stdout-only.
- **Provenance rule for looming (`continue`/`--ancestry-file`).** OK: a full,
  VERBATIM, previously-generated workspace as ancestry — from a tree, a raw
  log, or another model entirely (grafting a real workspace model A produced
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
- **Get a run's training JSONL path**: in the browser, ⇧-click the copy button
  next to a discovered run's name — it yields the absolute path of the JSONL that
  run was trained on (plain click gives the checkpoint's sampler path), ready to
  paste into a dataset viewer. **Ctrl+⇧-click opens it in samplescope** directly
  (new tab; starts a viewer if none is running) — the `sscope view` commands in
  the `samplescope` skill then drive that same view from your terminal.

## Command reference

Generated from the Typer app (same help strings `--help` shows) — do not edit
by hand; `python -m tinkerscope._gen_cli_ref` refreshes it, and
`tests/test_cli_docs.py` fails when it is stale. `tinkpg` and `tinkerscope`
are the SAME binary — the driver verbs answer to both names; `serve`/`pack`/
`site` are shown under `tinkerscope` (as `tinkerscope`, a bare directory
defaults to `serve`).

<!-- BEGIN GENERATED: tinkerscope-cli-reference (python -m tinkerscope._gen_cli_ref) -->
```
tinkpg ls [options]
  # List discovered training runs.
  --filter TEXT                     case-insensitive substring on id/name
  --sampleable-only                 only runs whose base model tinker still serves
tinkpg checkpoints <run>
  # List a run's checkpoints (name, step, whether it has a sampler).
tinkpg open <run>
  # Select a run in single mode; the browser switches live.
tinkpg chat <run> <prompt> [options]
  # Sample from a run's checkpoint; stream completions to stdout and the browser.
  --n INTEGER                       number of samples to draw  [default: 1]
  --temperature FLOAT               this call only; omit = inherit the global param (see `tinkpg params`)
  --max-tokens INTEGER              this call only; omit = inherit the global param
  --thinking/--no-thinking          force the thinking renderer on/off for this call; omit = inherit the global param
  --thinking-both                   draw n samples WITHOUT thinking + n WITH (2n total; overrides --thinking)
  --system TEXT                     system prompt for this call; omit = inherit the global one
  --no-system                       fire with NO system prompt even if the global state carries one
  --checkpoint TEXT                 checkpoint name (overrides @ in the run arg)
  --prefill TEXT                    assistant prefill the model extends; raw `<think>` ok
tinkpg compare <run_a> <run_b> <prompt> [options]
  # Compare N runs on one prompt — A→primary, B→compare, --run extras→p-2,p-3,… all stream...
  --run TEXT (repeatable)           additional run(s) → 3rd, 4th, … panes (repeatable)
  --n INTEGER                       number of samples per side  [default: 1]
  --temperature FLOAT               this call only; omit = inherit the global param
  --max-tokens INTEGER              this call only; omit = inherit the global param
  --thinking/--no-thinking          force thinking on/off for this call; omit = inherit
  --thinking-both                   n samples WITHOUT thinking + n WITH, per run (overrides --thinking)
  --system TEXT                     system prompt for this call; omit = inherit the global one
  --no-system                       fire with NO system prompt even if the global state carries one
  --prefill TEXT                    assistant prefill the models extend; raw `<think>` ok
tinkpg send [prompt] [options]
  # Fire the prompt as a NEW THREAD at the CURRENT panels of the open workspace — the CLI twin of...
  --n INTEGER                       samples per panel  [default: 1]
  --temperature FLOAT               this call only; omit = inherit the global param (see `tinkpg params`)
  --max-tokens INTEGER              this call only; omit = inherit the global param
  --thinking/--no-thinking          force thinking on/off for this call; omit = inherit the global param
  --thinking-both                   n samples WITHOUT thinking + n WITH, per panel (overrides --thinking)
  --system TEXT                     system prompt for this call; omit = inherit the global one
  --no-system                       fire with NO system prompt even if the global state carries one
  --prefill TEXT                    assistant prefill the models extend; raw `<think>` ok
  --file TEXT                       read the user message from a file (a probe template — mutually exclusive with the positional prompt)
  --prefill-file TEXT               read the assistant prefill from a file (mutually exclusive with --prefill)
  --panel TEXT (repeatable)         target only these panel ids (repeatable); overrides folding
  --conv TEXT                       workspace to fire into (id-prefix/name); when it isn't the open one, models bind from ITS saved layout. Default = the open workspace, auto-created if none
  --new-ws NAME                     create a fresh workspace with this name (seeded with the current panels), claim the bus, and fire into it
  --include-folded                  also fire at browser-folded panels
  --force                           fire even while a generation is in flight
  --logprobs                        print each sample's per-token logprob + top-5 alternatives (native tinker sampling only; none for OpenRouter)
  --json                            one JSON object per line (JSONL) instead of human text — for scripts; always includes token_logprobs when present, independent of --logprobs
  --first-token                     after the fire, print each panel's probability distribution over the FIRST generated token (from the captured token_logprobs); with --json, appended as first_token_summary JSONL lines
tinkpg continue [prompt] [options]
  # LOOM from an existing branch: rebuild the message history up to a target node and sample a...
  --n INTEGER                       samples per panel  [default: 1]
  --temperature FLOAT               this call only; omit = inherit the global param (see `tinkpg params`)
  --max-tokens INTEGER              this call only; omit = inherit the global param
  --thinking/--no-thinking          force thinking on/off for this call; omit = inherit the global param
  --thinking-both                   n samples WITHOUT thinking + n WITH, per panel (overrides --thinking)
  --system TEXT                     system prompt for this call; omit = inherit the global one
  --no-system                       fire with NO system prompt even if the global state carries one
  --file TEXT                       read the user message from a file (mutually exclusive with the positional prompt)
  --prefill TEXT                    assistant prefill the model extends — a thinking opener ('Hmm,') or its own truncated CoT; raw `<think>` ok
  --prefill-file TEXT               read the prefill from a file (mutually exclusive with --prefill) — e.g. the model's own truncated CoT
  --prefill-scope TEXT              all|think|non_think — which half(s) a thinking-both prefill applies to (default all)
  --panel TEXT (repeatable)         target only these panel ids (repeatable); default = all unfolded panels
  --thread INTEGER                  1-indexed root thread to continue (per panel); default = the panel's active thread
  --turn INTEGER                    1-indexed user turn on the thread's path to loom from; default = the leaf
  --node TEXT                       target node handle — `<node>`, `<panel>:<node>` or `<ws>:<panel>:<node>` (the browser's Copy-node-id button gives the middle form); pinpoints the loom point in ONE panel's tree
  --conv TEXT                       workspace for --thread/--turn/--node targeting (id-prefix/name); default = the one open in the browser
  --ancestry-file TEXT              loom from an EXPLICIT full transcript instead of a tree/panel: a JSON list of {role, content} dicts (role: user|assistant|system). The SAME transcript is used for every target panel — this is how you graft a real, verbatim conversation generated by one model into another model's context (sanctioned: FULL transcripts only, never an authored/partial answer). Mutually exclusive with --thread/--turn/--node/--conv.
  --include-folded                  also fire at browser-folded panels
  --force                           fire even while a generation is in flight
  --logprobs                        print each sample's per-token logprob + top-5 alternatives (native tinker sampling only; none for OpenRouter)
  --json                            one JSON object per line (JSONL) instead of human text — for scripts; always includes token_logprobs when present, independent of --logprobs
  --first-token                     after the fire, print each panel's probability distribution over the FIRST generated token (from the captured token_logprobs); with --json, appended as first_token_summary JSONL lines
tinkpg battery <probes_dir> [options]
  # Fire a DIRECTORY of probe files as sequential `send`s — the reusable probe battery.
  --out TEXT                        output dir for per-probe JSONL streams (default: <probes_dir>/results)
  --n INTEGER                       default samples per panel (front-matter `n:` overrides)  [default: 1]
  --temperature FLOAT               default for probes without `temperature:`; omit = inherit the global param
  --max-tokens INTEGER              default for probes without `max-tokens:`; omit = inherit the global param
  --thinking/--no-thinking          default thinking mode; omit = inherit the global param
  --system TEXT                     default system prompt for probes without `system:`; omit = inherit the global one
  --no-system                       default to NO system prompt (front-matter `system:`/`no-system:` overrides)
  --panel TEXT (repeatable)         default target panels (repeatable); front-matter `panel:` overrides
  --include-folded                  also fire at browser-folded panels
  --force                           fire even while a generation is in flight
  --first-token/--no-first-token    print each panel's first-token distribution after every probe (default on)
  --pause FLOAT                     seconds to wait between probes  [default: 3.0]
tinkpg probe <run> [prompt] [options]
  # Sample ANY discovered model WITHOUT touching the browser or any workspace.
  --n INTEGER                       samples to draw  [default: 1]
  --temperature FLOAT
  --max-tokens INTEGER
  --thinking/--no-thinking          thinking renderer (default: inherit the global)
  --system TEXT                     system prompt for this call
  --no-system                       fire with NO system prompt at all
  --file TEXT                       read the user message from a file
  --ancestry-file TEXT              JSON list of {role, content} dicts to sample a continuation of — the multi-turn form (a trailing assistant entry acts as a prefill)
  --prefill TEXT                    assistant prefill the model extends
  --full                            print each sample's complete answer + CoT
  --json                            JSONL to stdout, one object per sample (carries raw_meta)
tinkpg params [options]
  # Show or SET the GLOBAL sampling params (system prompt, temperature, max tokens, n, thinking,...
  --temperature FLOAT
  --max-tokens INTEGER
  --n INTEGER                       default sample count
  --thinking/--no-thinking
  --thinking-both                   set the global thinking mode to 'both'
  --top-p FLOAT
  --system TEXT                     global system prompt
  --system-file TEXT                read the global system prompt from a file (mutually exclusive with --system)
  --clear-system                    remove the global system prompt
  --json                            print the resulting global params as JSON
tinkpg url [selector] [options]
  # Print the URL of the server this CLI is driving — the thing to hand the human when they ask...
  --conv TEXT                       same as the positional selector
  --live                            link to the workspace the browser currently has open (from the state bus) instead of naming one
  --json                            url + resolved instance (pid, scan roots) + workspace id/name
tinkpg state [options]
  # Digest of what's on screen now: one block per panel, first/last-2 of each panel's ACTIVE...
  --full                            show every message per panel, not just first/last-2
  --width INTEGER                   per-message truncation width  [default: 160]
  --link/--no-link                  annotate each panel with the saved workspace its active path matches (`--no-link` skips the workspaces fetch)
  --json                            raw state JSON (untruncated escape hatch)
  --include-folded                  also show panels folded in the browser UI (skipped by default)
tinkpg threads [options]
  # Cross-workspace index of every root THREAD — the find primitive for "where are my multi-turn...
  --min-turns INTEGER               only threads whose DEEPEST branch has ≥N user turns (2+ = multi-turn)  [default: 1]
  --ws TEXT                         restrict to one workspace (id-prefix or name substring)
  --model TEXT                      only panels whose model/checkpoint contains this substring
  --grep TEXT                       only threads whose FIRST message contains this text (case-insensitive)
  --width INTEGER                   first-message truncation width  [default: 72]
  --include-folded/--no-folded      include panels folded in the browser (default: yes — folding is a view choice, not a filter)
  --json                            emit rows as JSON (untruncated first messages)
tinkpg ws [selector] [options]
  # Browse saved WORKSPACES (multi-panel, branchable; `conv` is a back-compat alias).
  --conv TEXT                       same as the positional selector, for symmetry with `grep`/`node`/`threads` (which can only take it as an option)
  --panel TEXT                      restrict to one panel id (p-1/p-2/… — older workspaces also have primary/compare); overrides folding
  --full                            show the whole active path, not just first/last-2
  --tree                            show the full branch tree (all branches), `*` = active
  --width INTEGER                   per-message truncation width  [default: 160]
  --include-folded                  also expand panels folded in the browser UI (skipped by default)
  --thread INTEGER                  walk root thread K (the `threads:` index / `tinkpg threads`) instead of the active one
  --deepest                         walk the thread's LONGEST branch instead of its selected one
  --json                            emit the selected transcript(s) as structured JSON (untruncated content + CoT + node ids)
tinkpg samples [selector] [options]
  # Show every sibling response (the n-sample fan-out) at ONE fork, each with its CoT, plus a...
  --conv TEXT                       same as the positional selector, for symmetry with `grep`/`node`/`threads` (which can only take it as an option)
  --panel TEXT                      panel id (p-1/p-2/… — older workspaces also have primary/compare); default = the LEFTMOST non-folded panel (layout order, i.e. the column order on screen). Explicit --panel overrides folding
  --thread INTEGER                  1-indexed root thread (branch-from-start sibling) to walk; default = the active one. Thread numbers: the `threads:` index in `tinkpg ws <id>`
  --turn INTEGER                    1-indexed user turn on the thread's path whose responses to show; default = the last one
  --node TEXT                       node handle — `<node>`, `<panel>:<node>` or `<ws>:<panel>:<node>` (the browser's Copy-node-id button gives the middle form; `tinkpg grep` prints ids). Pinpoints the fork directly, reaching NON-selected branches --thread/--turn can't. An assistant id shows the fan-out it belongs to
  --full                            each sample's COMPLETE answer + full CoT (default: answer + one-line CoT preview)
  --width INTEGER                   per-sample truncation width in the default (non --full) view  [default: 240]
  --sample INTEGER                  show ONLY sibling K (1-indexed) — read one sample at a time
  --slice TEXT                      START[:LEN] character window of each shown sample (default LEN 2000) — read long samples in pieces instead of truncating; with --full the same window applies to the CoT
  --json                            the fork as one JSON object (workspace/panel/thread/prompt/tally/samples) instead of human text — for scripts (--slice is ignored; content is never truncated)
  --first-token                     the model's probability distribution over the FIRST generated token at this fork (stored top-K + each sample's sampled token — the CLI twin of the chart's first-token mode); with --json, adds per-sample `first` records + the aggregate
  --deepest                         resolve --turn against the thread's LONGEST branch instead of its selected one — reaches forks deeper than the selection goes
tinkpg grep <pattern> [options]
  # Search EVERY branch of saved workspaces — message content, thinking (`reasoning`) and thread...
  --conv TEXT                       restrict to one workspace (id-prefix or name substring)
  --regex                           treat PATTERN as a Python regex
  --ignore-case
  --width INTEGER                   snippet width around each match  [default: 160]
  --max-hits INTEGER                stop printing after this many hits (count continues)  [default: 200]
  --json                            hits as a JSON array (full match text, not a snippet) instead of human text — for scripts
  --link                            append a clickable deep link per hit (?w=…&node=… opens the browser AT the match)
tinkpg node <node_id> [options]
  # Locate a NODE ID anywhere in the saved workspaces and dump its record — the reverse index...
  --conv TEXT                       restrict the search to one workspace (id-prefix or name substring)
  --logprobs                        fetch + print the stored per-token logprob blob (index, token, lp, top-K alternatives)
  --meta                            fetch + print the stored raw_meta blob (the request & response record)
  --raw                             print the node's raw_text (tags preserved)
  --full                            full content / thinking / prefill instead of one-line previews
  --json                            the matches as one JSON object (blobs included when --logprobs/--meta; content never truncated) — for scripts
  --link                            append a clickable deep link (?w=…&node=… opens the browser AT this node)
tinkpg trash [action] [handle] [options]
  # Recover deleted branches.
  --workspace TEXT                  workspace id-prefix or name substring (default: every workspace, for list)
  --json                            emit structured JSON
tinkpg refresh
  # Rescan the filesystem + re-probe sampling capabilities.
tinkerscope serve [dirs...] [options]
  # Serve the API + web UI for DIRS (bare `tinkerscope <dir>` is shorthand for this).
  --port INTEGER                    port to bind (default: first free port from 8765)
  --host TEXT                       host to bind  [default: 127.0.0.1]
  --reload                          dev mode: auto-reload on source change
  --pack FILE_OR_URL                apply a share pack (local path or http(s) URL) to this folder's state before serving
  --force                           with --pack: also overwrite existing default params/layout (default: keep them if the folder was already used)
  --reseed                          with --pack: fully rebuild the pack's workspaces (delete + re-import, so re-exported raw_meta/logprob blobs refresh and dropped nodes are removed) and overwrite default params — for iterating on a pack you keep re-exporting (implies --force)
tinkerscope pack export <out> [options]
  # Export the current setup to a pack YAML file.
  --dir PATH (repeatable)           scan root(s) whose state to export (default: cwd) — must match how the instance was launched
  --name TEXT                       pack name (default: kept from an existing file, else the dir name)
  --description TEXT
  --models-from panels|workspaces|all|runswhere to gather models (default: all = current panels + workspaces + already-registered pack models)  [default: all]
  --include-model SUBSTR (repeatable)keep only models whose label/ref matches (repeatable)
  --exclude-model SUBSTR (repeatable)drop models whose label/ref matches (repeatable)
  --no-workspaces                   exclude saved workspaces
  --no-defaults                     omit the defaults block (sampling params + default panel layout) from the pack
  --workspace NAME (repeatable)     include only these workspaces by name (repeatable)
  --overwrite                       regenerate from scratch instead of merging into an existing file
  --logprobs                        include per-token logprobs (the token inspector + first-token chart). Large: give `out` a .gz suffix to compress (107 MB -> 30 MB on a real workspace)
tinkerscope site export <out> [options]
  # Write a self-contained static site into a directory.
  --dir PATH (repeatable)           scan root(s) whose state to export (default: cwd) — must match how the instance was launched
  --title TEXT                      site title, shown in the read-only badge (default: the dir name)
  --description TEXT
  --workspace NAME (repeatable)     include only these workspaces by name (repeatable)
  --open WS_ID                      workspace id to open by default (default: the first exported one)
  --pack-url URL                    where the same content is published as a share pack — the site's "open this locally" panel turns it into a runnable command
  --pack-link URL|PATH=URL (repeatable)a pack this site should be able to INSTALL on demand, so a ?w=<id> link is shareable: a visitor who lacks that workspace fetches the pack instead of falling back to the newest one. Repeatable. Use PATH=URL when the file is local and not yet uploaded (path read for the ids, URL fetched by visitors). Implies --pack-url when given exactly once
  --logprobs WHICH                  which turns keep per-token logprobs: all (default) | chart (only the turn each workspace's saved chart view points at) | last:N (newest N turns per thread) | none. They are ~97% of a site's bytes, and the token inspector + first-token chart are what they buy
  --no-logprobs                     alias for --logprobs none
  --pins/--no-pins                  --no-pins excludes saved pins (default: included, EXCEPT when --workspace filters the export); --pins includes them even then (they can't be filtered per-workspace)
  --web-dist PATH                   built frontend to publish (default: this install's web/dist, else the packaged copy)
```
<!-- END GENERATED: tinkerscope-cli-reference -->
