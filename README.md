# tinkerscope

A browser playground for **Tinker-trained checkpoints**. Point it at your
training directory and it **auto-discovers every run** — no `models.yaml`, no
registration — then chat with them, fan out N samples to see what a model
*usually* says, branch like on claude.ai, and compare checkpoints side by side.
The whole thing is also drivable **live from your terminal** (`tinkpg`), so
your AI agent can fire samples into the browser you're watching.

![The chat view with workspace branching](docs/img/chat-branching.png)

## Quick start

```bash
export TINKER_API_KEY=...      # required to sample (discovery works without it)
uv tool install tinkerscope
tinkerscope ~/my-training-runs # scans the tree, auto-picks a port, prints the URL
```

Open the printed URL and you're in. `tinkerscope DIR1 DIR2 …` scans several
trees at once. Set `OPENROUTER_API_KEY` too if you want OpenRouter reference
models next to your checkpoints. Lost in the UI? The **`?` button** in the
sidebar explains every control and keyboard shortcut — or just ask your agent
(guide skill below).

### Agent skills

The repo doubles as a plugin marketplace shipping two skills:
`tinkerscope:guide` makes your agent a help desk for the UI — ask it "how do I
compare two checkpoints?" and it knows every button and shortcut — and
`tinkerscope:cli` teaches it to drive the playground from a terminal (more
below).

<details>
<summary><b>Claude Code</b></summary>

```
/plugin marketplace add Butanium/tinkerscope
/plugin install tinkerscope@tinkerscope
```

Claude Code treats the commit SHA as the plugin version, so the skills track
`main`. Third-party marketplaces are manual-update by default — toggle
auto-update from `/plugin` > Marketplaces, or run `/plugin marketplace update
tinkerscope` followed by `/reload-plugins`.

</details>

<details>
<summary><b>Codex</b></summary>

```bash
codex plugin marketplace add Butanium/tinkerscope
codex plugin add tinkerscope@tinkerscope
```

Codex caches by the `version` in `plugin/.codex-plugin/plugin.json`; update
with `codex plugin marketplace upgrade tinkerscope`.

</details>

<details>
<summary><b>Cursor / Copilot / Windsurf / Cline</b></summary>

```bash
npx skills add Butanium/tinkerscope --agent <agent> -g -y
```

</details>

## Features

### Auto-discovery — no config files

tinkerscope scans your directories for `checkpoints.jsonl` + `config.json`
(the two files every `tinker_cookbook` run drops) and surfaces one run per
directory with its whole checkpoint trajectory. Runs Tinker can no longer
sample are greyed out with the reason instead of failing on click. Every
picker is type-to-filter with typo-tolerant fuzzy matching, and every model
has a copy button for its `tinker://…` sampler path.

You can also add models straight from the UI: a raw **Tinker base model**, a
**loose checkpoint** by sampler path, or any **OpenRouter model** to sit next
to your checkpoints as a reference.

### N-sample fan-out

Set **n > 1** and one send draws N samples, rendered as cards — a quick read
on what the model usually says. The usual knobs (temperature, max tokens,
top-p), a **thinking toggle** (Off / On / **Both** — Both draws n with and n
without, tagged), a system prompt, and an assistant **prefill** field.

![n>1 sample cards](docs/img/n-samples.png)

### Response distribution chart

The draws power a live chart. Define **highlight rules** (named colors +
patterns) and each sample is bucketed by the rules it matches — "define a
rule, see its prevalence per model" is one loop. A **first token** mode
charts the model's *own* probability distribution over the first generated
token next to what was actually sampled. Segments are clickable to inspect
the exact samples underneath, and the chart live-updates while a batch
streams.

![The response distribution chart](docs/img/distribution-chart.png)

### Token probabilities

Native Tinker sampling stores **per-token logprobs + top-5 alternatives** on
every turn. Flip the sidebar's **Token probs** toggle to paint a surprisal
heat under the prose (or show the raw token stream), and hover any token for
its probability and the alternatives the model weighed. **Color by match**
tints each token by how much probability mass a highlight rule *almost* got —
where did "red" nearly happen.

![Token probabilities overlay](docs/img/token-probs.png)

### Parallel conversations with different models

Have the same conversation with several models at once: **Add panel** puts
models side by side, and every message you send gets answered by each — same
user turns, one response per model. Panels generate concurrently and each
keeps its own branch tree.

![The same conversation answered by two different checkpoints](docs/img/compare.png)

### Branch everything

Nothing is ever destroyed: regenerating, editing a turn, or drawing N samples
creates sibling branches you can cycle through with **‹ k/N ›**, and you can
branch from the very start to keep several probe prompts in one workspace.
Workspaces are named, persisted per project, and restored on restart. Every
button has a tooltip, Shift/Ctrl unlock power variants, and the `?` modal (or
your agent, via the guide skill) covers the rest.

### Share packs

Bundle checkpoints + params + workspaces into one portable YAML. Models are
addressed by public sampler paths, so a collaborator reproduces your exact
setup with no local run dirs:

```bash
tinkerscope pack export demo.yaml     # author from your current setup
tinkerscope --pack <file|url>         # consume + serve
```

A pack is also a **link**: open `?w=<pack url>` on a running instance and it
installs (after asking) with no restart. Full doc: [`docs/PACK.md`](docs/PACK.md).

### Publish a read-only site

Export the playground as a static site — no backend, no API key, hostable on
GitHub Pages:

```bash
tinkerscope site export ./site --workspace "the good one"
```

Visitors browse the real thing: branches, threads, panels, the chart, token
probabilities. Per-token logprobs are ~97% of the bytes — export a subset, or
`--no-logprobs`. A published site also works as a **viewer for anyone's
pack** (`?w=<pack url>`, or drop the file on the page), and its read-only
badge hands readers the command to run everything locally. Full doc:
[`docs/STATIC_SITE.md`](docs/STATIC_SITE.md).

## Bring your agent — `tinkpg`

The CLI hits the same API as the browser and shares its state, which makes the
playground a place you and your AI agent work in together:

- **It reads what you did.** Your agent can walk every branch of your
  workspaces, pull full n-sample fan-outs, and `grep` across everything you
  ever sampled ("which workspace had that self-description prompt?").
- **You watch what it does.** Probes the agent fires land live as new threads
  in your open browser — its exploration stays auditable, thread by thread,
  instead of buried in a script's stdout.
- **Its reads feed write-ups.** Every read command takes `--json`, so a
  morning of browser sampling turns into an eval design or a report without
  re-running anything.

```bash
tinkpg ws [<id|name>]                  # browse saved workspaces / read one
tinkpg samples [<id|name>]             # the full n-sample fan-out at a fork
tinkpg grep "<text>"                   # search every branch of every workspace
tinkpg threads                         # index root threads across workspaces
tinkpg node <id>                       # look up a node id → record + logprobs
tinkpg send "prompt"                   # fire a new thread at the current panels
tinkpg continue "follow-up"            # add a turn to the current threads
tinkpg battery <dir>                   # fire a directory of probe files
tinkpg probe <run> "prompt"            # sample off-workspace (no broadcast)
tinkpg ls / checkpoints <run>          # discovered runs / a run's checkpoints
tinkpg open <run>[@<checkpoint>]       # switch the browser to this model, live
tinkpg chat <run> "prompt" --n 50      # one-shot: select + sample + stream
tinkpg compare <runA> <runB> "..."     # one-shot: two panels + a first turn
tinkpg params / state / refresh        # sampling params / shared state / rescan
```

Param flags on a fire are per-call — they never clobber your browser sidebar.
The `tinkerscope:cli` skill teaches your agent all of it, flags included
([`plugin/skills/cli/SKILL.md`](plugin/skills/cli/SKILL.md)).

## Tests

`uv run pytest -q` covers discovery and the API with zero remote calls. The
pure frontend logic has ~20 bare-Node unit suites (`npm test` from `web/`),
and `scripts/smoke.sh` runs the Playwright browser smokes against an isolated
throwaway instance.

## Development

```bash
./run.sh [DIR ...]          # dev mode: backend + vite dev server (hot reload)
./run.sh --build [DIR ...]  # packaged: build the web UI once, serve from one process
```

Installing as a tool while hacking? Use `uv tool install -e .` (editable) so
the process runs your checkout. Backend changes need a process restart; web
changes need `npm run build` (a pre-commit hook runs it on `web/` commits)
plus a browser refresh.

<details>
<summary><b>Hacking on the skills</b></summary>

Don't install them — the install snapshots into a cache, so your edits won't
show. Symlink each skill into your skills dir instead and it reads your
working tree live:

```bash
for s in cli guide; do
  mkdir -p ~/.claude/skills/tinkerscope-$s
  ln -sf "$PWD/plugin/skills/$s/SKILL.md" ~/.claude/skills/tinkerscope-$s/SKILL.md
done
```

They load as `tinkerscope-cli` / `tinkerscope-guide` (directory name wins over
frontmatter). Symlinking `plugin/` as a directory also works — but then don't
*also* install it, or every skill registers twice.

</details>

For orientation and contracts: **`CLAUDE.md`** (where everything lives),
[`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) (HTTP + SSE shapes),
[`docs/BRANCHING_DESIGN.md`](docs/BRANCHING_DESIGN.md) (the branching model),
[`docs/TODO.md`](docs/TODO.md) (roadmap).

## Credits

The UI is forked from **Harry Mayne**'s `tools/playground` in
[`HarryMayne/negation_neglect_working_repo`](https://github.com/HarryMayne/negation_neglect_working_repo)
(commit `ec7da09`, Harry Mayne <harrymayne@gmail.com>). The core chat experience
— streaming, n-sample fan-out, the response-distribution chart, the thinking
toggle, the raw-text view, and the side-by-side compare — is his work.
tinkerscope adds run auto-discovery, workspace branching, named/persisted
workspaces, N-panel comparison, the token-probability views, share packs, the
static-site export, the terminal-driving CLI, and standalone packaging on top.

Renderer selection (chat templates / stop sequences / response parsing) uses
`tinker_cookbook` (Thinking Machines). An earlier iteration routed inference
through **James Chua**'s [`latteries`](https://github.com/thejaminator/latteries);
tinkerscope now calls the Tinker SDK directly, but the renderer-cache and
thinking-block-parsing lessons from that code carried over.

tinkerscope's own code is MIT-licensed (see `LICENSE`). The upstream playground
ships **without** a license; substantial portions of the UI and inference layer
are Harry Mayne's work, retained here with attribution. If you build on this,
keep that credit.
