---
name: guide
description: Explain the tinkerscope BROWSER UI to a human — what a workspace/panel/branch/thread is, how to run a distribution over N samples, highlight rules, the three chart modes, token probabilities, prefill, pins, share packs, and every keyboard shortcut. Use when someone asks how to use tinkerscope, what a button does, "how do I compare two checkpoints", "how do I see what it usually says", or is looking at the playground and lost. This is the human-facing twin of the tinkerscope CLI skill (`tinkerscope-cli`, or `tinkerscope:cli` via the plugin), which is for DRIVING the tool from a terminal — read this one to talk someone through the screen, not to run commands.
---

# tinkerscope, for the person looking at it

tinkerscope is a browser playground for the checkpoints your Tinker training
runs produced. It scans a directory tree for runs, lists what it finds, and lets
you talk to several of them side by side. The thing that makes it different from
a chat window: **nothing you generate is thrown away**, so "what did it say the
other three times?" is always a question with an answer.

There is a `?` button at the top of the sidebar with a condensed version of this
(Guide + a keyboard table). This file is the longer form — use it to walk
someone through the screen, or to answer a specific "what does this do".

## The screen

```
┌───────────────────────────────────────────────────────────────┐
│ tinkerscope    /path/being/scanned                     ● live │
├──────────────┬────────────────────────────────────────────────┤
│  [icon row]  │                                                │
│              │   panel: run A        panel: run B             │
│  Workspace ▾ │   ┌──────────────┐    ┌──────────────┐         │
│              │   │ user: …      │    │ user: …      │         │
│  Models      │   │ asst: … ‹2/3›│    │ asst: …      │         │
│   panel 1 ▾  │   └──────────────┘    └──────────────┘         │
│   panel 2 ▾  │                                                │
│              │  ┌──────────────────────────────────────────┐  │
│  Temperature │  │ ⑂ branch from start · system · prefill   │  │
│  Max tokens  │  │ [ type a message…                      ] │  │
│  Samples     │  └──────────────────────────────────────────┘  │
│  Thinking    │                                                │
│  Highlights  │                                                │
└──────────────┴────────────────────────────────────────────────┘
```

The icon row at the top of the sidebar, left to right: **theme**, **distribution
chart**, **pins slideshow**, **peek at training data**, **rescan runs**,
**help (`?`)**, **stop all generation**.

## The four nouns

**Workspace** — one saved setup: its message tree, its system prompt, and which
panels it shows. The picker at the top of the sidebar switches between them:
click it and type to filter (same control as the model picker — ↑/↓ to walk,
Enter to pick, Esc to close), rows newest-touched first with a "3h ago" stamp
so two same-named workspaces are still distinguishable. Switching restores that
workspace's models too. New workspace inherits the current models; Shift-click
the `+` for a blank one. Each workspace has its own URL (`?w=<id>`), so you can
bookmark or share a link to it.

**Panel** — one column, one model. A model is either a *discovered run
checkpoint* (from the scanned directory), a *raw base model*, or an *OpenRouter
reference model* for comparison against something known. Sending a message fires
it into every panel at once, so the columns are the same prompt answered by
different models. Drag a column header to reorder; the sidebar pickers and the
send chips follow.

**Branch** — every regenerate, every edit, every one of your N samples becomes a
*sibling* of the message it replaced, not a replacement. A row with siblings
shows a `‹ k/N ›` cycler; click the arrows (or use ←/→ on a focused row) to walk
them. Deleting prunes only that subtree. This is the core idea: you can resample
the same turn twenty times and still read all twenty.

**Thread** — a workspace can hold several independent conversations. The `⑂
branch from start` toggle next to the composer makes your next message a new
first message instead of a reply. The switcher beside it lists every thread
across all panels, with a marker for whether the thread exists in every panel or
only some.

## Things people actually want to do

**"What does this checkpoint usually say to X?"** Set *Samples* in the sidebar to
20 or 50, send the prompt once. You get N sibling branches. Open the
**distribution chart** (bar-chart icon) — the default mode buckets the samples by
which **highlight rules** they match, so if you have a rule for the behavior
you're hunting, you get its rate directly, with the un-matched samples in grey.
Click a bar segment to read the samples in that bucket.

**"Did it open with the tag?"** Rules mode has a **first N chars** box beside the
match scope: type a number and rules only match that far into the text. It's the
fix for rules about how a reply *starts* — an `<answer>` tag, a "Verdict:"
header — which otherwise also fire when the model discusses the same token later
in its explanation, so every sample lands in the rule's bucket and the chart says
nothing. Blank means the whole text. The cap counts into each part separately, so
it means the same thing under any match scope, and when you click into a bucket
the inspector greys out everything past the cut so a sample sitting in *no match*
with a visible later hit explains itself.

**"Do these two checkpoints differ?"** Add a panel per checkpoint, send the same
prompt with a large *Samples*, and compare the charts (the chart shows every
panel). For a single-turn difference, the row toolbar's regenerate with Ctrl held
regenerates in *all* panels at once so they stay in lockstep.

**"Does thinking change what it answers?"** Draw N samples with thinking on, then
regenerate N more with it off (or vice versa) — the turn now holds both
populations. The chart notices and offers a filter next to the mode buttons:
*all samples* pools them, *with / without thinking* charts one, and **split think
/ no-think** puts a bar for each side by side under the model name, each
percentaged over its own samples and labeled with its own n. Rules mode has a
second, independent split — the **split** *match scope*, which charts one
population as a response bar and a thinking bar over the SAME samples (does the
behavior show up in the CoT but not the answer?). Both at once gives up to four
bars per model.

**"Why did it pick that word?"** Turn on **Token probs** in the sidebar. Assistant
replies render as their raw token stream, each token tinted by surprisal; hover a
token for its probability and the top-5 alternatives it passed over. It's
display-only and retroactive — turns you sampled before flipping it on already
have the data. If you have highlight rules, flip **Color by match** (the Off/On
toggle right under it) On and pick up to two: each token is re-tinted by how much
probability mass went to alternatives matching that rule, which answers "how
close was it to saying the other thing?" without resampling. Two rules split each
token into a top and bottom band; switching the toggle Off keeps the picks. The
**Contrast** slider under the chips reshapes probability → opacity: 0 = opacity
tracks the mass (relative read), 1 = a step where any nonzero match goes to full
tint (the "is anything related in the top-5 at all?" read), 0.50 = the √ ramp
default in between.

**"What's the model's distribution over the FIRST token?"** The chart's third
mode, *first token*, plots the model's own probability distribution at position
0, not the empirical sample counts. Chips in the legend: click one to exclude it
(its mass folds into grey), drag one onto another to merge them into one color,
type in the search box to surface a token that's buried in the tail. A
*renormalize* checkbox rescales the shown tokens to 100% when you only care about
their relative sizes.

The chart remembers how you left it. Mode, match scope and thinking filter follow
you everywhere (they're how you like to look at a distribution); the charted turn,
the excluded rule chips, the first-N-chars cap and the first-token exclusions /
merges / added tokens are remembered *per workspace*, since they only mean
anything for that prompt. Both
survive closing the modal and reloading the page — it's browser-local, so it
doesn't travel to another machine or into a share pack.

**"Make it say X and continue from there."** Two ways. The **prefill** field in
the composer is text the assistant is treated as having already started; the
model extends it (type a raw `<think>` to force a reasoning opening, or a whole
think block to jump straight to an answer). Prefill persists across sends, so you
can draw N samples off one prefill. Alternatively, **edit an assistant message**
— that writes a manual branch you can then continue from.

**"Keep this one."** The bookmark button on a row **pins** it with a note (Shift
skips the note dialog); the play icon in the sidebar browses pins as a slideshow.

**"Send this to a colleague who doesn't have my run directories."** Share packs:
`tinkerscope pack export` bundles checkpoints, default params and whole
workspaces into one portable YAML; they open it with `tinkerscope --pack
<file|url>` and get your setup against the public checkpoints, with no local run
dirs at all.

## Highlight rules

Sidebar → **Highlights**. A rule is a named pattern with a color: plain text or
regex, several patterns combined with or/and, optionally scoped to a role
(user/assistant). Matching text is painted wherever it renders.

Rules aren't only cosmetic — they're the vocabulary the analysis views use:

- the distribution chart's default mode buckets each sample by the *set* of rules
  it matches (grey = no rule matched, solid = one, striped = a combination);
- **Color by match** on the token view (its own Off/On toggle) tints by
  rule-match probability;
- the chart's per-rule chips let you drop a rule that the prompt makes ubiquitous
  from the bucketing, without deleting the rule.

Rules persist per scanned directory and reorder by dragging a rule row.

Two known limits worth saying out loud: matching runs on the rendered
(entity-encoded) markdown, so a pattern containing raw `<`, `&` or `'` may not
match; and a rule can't span turns (no "highlight the answer only if the question
mentioned X").

## Sampling controls

*Temperature*, *Max tokens*, *Samples* (N per send), and a *Thinking* toggle for
models that support it. Under the "more" section: top-p, top-k, presence and
repetition penalties — some of these only apply on some backends, and those are
marked. Params are global across panels on purpose: a comparison where the panels
had different temperatures wouldn't be a comparison.

The **system prompt** lives in the composer row as a split chip: the left dot
applies or mutes it (muting keeps the text), the label expands the editor. With
`⑂ branch from start` on you get a second chip, the **thread system prompt** —
recorded on that thread's first message and appended to the global one.
Continuing an existing thread always reuses that thread's own recorded prompt, so
old threads don't silently change meaning when you edit the global prompt.

## Keyboard

Composer:

| Key | What |
|---|---|
| `Enter` | Send to every panel (or just the ones picked as send targets) |
| `Shift+Enter` | Newline |
| `Esc` | Prompt history — then ↑/↓ to browse, `Esc` again to leave |
| `Enter` in a panel's own box | Continue just that panel |

Chat rows — **click a row first** to give it the focus ring:

| Key | What |
|---|---|
| `↑` / `↓` | Move the focus ring within that panel |
| `←` / `→` | Cycle the focused row's `‹k/N›` sibling branches |
| `Esc` | Drop the focus ring |

The row toolbar's buttons are unlabeled glyphs — if you're talking someone
through one, name the shape, not just the verb (the app's own `?` modal draws
each icon next to its name, so "open ? → Guide → the row toolbar" is the fastest
answer to "which one is that"):

| Button | Glyph | What |
|---|---|---|
| Raw | the word `Raw`, leftmost | the model's untouched output, tags and all |
| regenerate | circular arrows | sample this turn again → a new sibling branch |
| continue | `+` | the model EXTENDS this message instead of replying |
| edit | pencil | change the text; forks, never overwrites |
| delete | trash can | prune this branch and its subtree |
| bookmark | tag/label outline | pin this sample with a note |
| copy message | two overlapping squares | just this message |
| copy conversation | the same, with text lines | the whole thread as markdown |
| send to panel | two crossing arrows | copy this branch's context into another panel |
| copy node id | `#` | the id `tinkpg --node` addresses |

Sample cards (an n>1 draw) add: **make active** (circled check), **continue this
sample** (`+`), **discard others** (a page with an ✕).

Hold a modifier, then click a row-toolbar button:

| Modifier | What |
|---|---|
| `Ctrl`/`⌘` | Do it in ALL panels at once — edit · regenerate · delete · continue. Only live with more than one panel on screen; with one panel the modifier does nothing |
| `Shift` + edit (user row) | Fork a full editable copy of the conversation, generating nothing |
| `Shift` + regenerate | Replace this branch in place instead of adding a sibling |
| `Shift` + delete | Delete every branch at this turn |
| `Shift` + continue | Resume *inside* the think block |
| `Shift` + copy | Include the thinking text |
| `Shift` + bookmark | Pin instantly, no note dialog |

And in the sidebar: `Shift` + New workspace makes it blank rather than
inheriting the current models; `Shift` + Add panel adds an empty panel rather
than cloning the last one.

Buttons change their icon and tooltip while you hold the modifier, so you can
always check what a chord will do before committing to it.

## Things that surprise people

- **A greyed-out run** isn't a bug: either its base model is no longer served, or
  its sampler weights expired/were deleted. Select it anyway and the sidebar
  spells out which of the two it is under the picker (the dropdown row's own
  tooltip is generic). The rescan button re-checks availability.
- **The row toolbar folds.** A narrow column hides the tail of the buttons behind
  a chevron; expand it and they appear on a second line. "Raw" is pinned leftmost
  and never folds.
- **`live` in the top bar** means the browser is connected to the shared state
  bus. Everything on screen can also be driven from a terminal via the `tinkpg`
  CLI, and a chat fired there streams into this view. The `#` button on a row
  copies its node id, which is how you point a CLI command at one specific
  message.
- **Two tabs on the same workspace** is last-writer-wins for edits. Two tabs on
  *different* workspaces is safe.
- **Nothing live-reloads.** If someone changed the code, a backend change needs
  the server restarted and a frontend change needs a rebuild plus a refresh.

## Sharing what you're looking at

Two ways, depending on whether the other person should be able to sample.

**A pack link — they get your setup, live.** Export a share pack
(`tinkerscope pack export demo.yaml`), publish it anywhere with a URL, and the URL
*is* the invitation: `<their tinkerscope>/?w=https://…/demo.yaml` installs its
models, params and workspaces and opens them. A local path works too
(`?w=/home/me/demo.yaml`) — their server reads it. Add `&open=<workspace-id>` to
choose which workspace lands open. If they already have that pack's workspaces it asks
whether to **replace** them or **keep both** (the incoming copy becomes "name (2)") —
overwriting is the one case that always asks, since it discards what's there. On a
*running instance* a link also asks before a first install, because that writes into
their real state directory and any page can send a browser to a `localhost` URL; on a
*published site* it installs straight away, since that lands in their own browser and is
deletable. Once installed the address bar
shows the plain `?w=<id>`, so reloading opens rather than re-installing. They need
their own API key to sample — the pack carries checkpoints, never credentials.

By default a pack drops per-token logprobs; `pack export demo.yaml.gz --logprobs`
keeps them, so the token inspector and the chart's *first token* mode work on the
other side. Give it a `.gz` path when you do — uncompressed, a real workspace lands
at 107 MB, past GitHub's 100 MB file limit; gzipped it's ~30 MB, and every consumer
un-gzips it transparently.

**A published read-only site — they get a link, no setup at all.**
`tinkerscope site export ./site` writes a static copy hostable on GitHub Pages (no
backend, no key). Visitors can read and analyze everything — every workspace, branch
and thread, all the panels, the distribution chart in all three modes, token
probabilities, the pins — and can write their own highlight rules, which stay in
their browser. What's simply not on the screen: the composer, regenerate/edit/delete,
the model pickers, and the sampling params. The top bar says **snapshot** instead of
`live`, and a badge in the sidebar names it as read-only.

Two things worth knowing before you publish. The chart's per-workspace view travels
with the export, so whatever bucketing you set up is what a visitor sees first —
worth arranging deliberately. And token probabilities are enormous: they're around
97% of the exported bytes, so if the site is only meant to show *what* the models
said, `--no-logprobs` makes it roughly 30× smaller (at the cost of the token
inspector and the chart's "first token" mode).

Two things a *reader* of the site gets. The **read-only badge is a button**: it opens a
panel with the command to run tinkerscope locally against the same pack, so going
interactive is one copy-paste (pass `--pack-url` at export time or it can only offer the
generic install, and it says so). And each checkpoint has a **copy button beside its
name** that puts its `tinker://…/sampler_weights/…` path on the clipboard — the thing you
paste into your own script.

**The two combine.** A published site is not only *your* workspaces — it reads
anyone's pack. Point it at one with `?w=<pack url>`, or open a file from your own
computer with the ⤒ button beside the workspace picker (or by dropping the file on
the page), which needs no hosting at all. Installed workspaces live in that
browser and persist; the site's own baked ones stay read-only. So one deployed site
can serve as a general viewer, and each workspace you want to share is just a pack
file rather than a re-publish.

## Keeping this file honest

This guide and the in-app `?` modal (`web/src/lib/HelpModal.svelte`) are twins —
when UI behavior changes, both change, in the same commit as the code. The
agent-facing counterpart is the CLI skill (`tinkerscope-cli`, or `tinkerscope:cli`
via the plugin — terminal / `tinkpg`), which
is the one to read when the task is *driving* the tool rather than explaining it.
