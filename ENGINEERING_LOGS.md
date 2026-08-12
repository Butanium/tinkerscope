# Engineering logs

Append-only chronology of plumbing / pipeline / tooling changes and the forensics
behind them: what changed, why, and the gotcha it cost. Don't edit prior entries.

Division of labour with the other docs, because this repo had none of this
written down for its first six weeks:

- **`CLAUDE.md`** — the RULE, imperative and short, because it is auto-loaded
  into every session. "Never use the oai `/v1/models` listing for availability."
- **project memory** (`~/.claude/projects/…/memory/`) — LOCAL lessons: this box,
  this instance's working habits, where Clément's live instance actually runs.
  Also session-start injected, which is the point — it's a deliberate home, not
  spillage, and nothing here supersedes it. The line against this file is
  portability: a collaborator who clones the repo should not inherit "the live
  instance is on :8767", and should inherit every entry below.
- **here** — the dated NARRATIVE behind the rules: what broke in the CODEBASE,
  what was measured, what theory turned out to be false. Not auto-loaded; read
  when a rule surprises you or you're about to change the thing it guards.
- **`docs/*.md`** — as-built design + contracts (`API_CONTRACT`, `STORAGE_V2`,
  `BRANCHING_DESIGN`, …). What the system IS, not how it got there.
- **`ideas/`** — forward-looking, not yet built.

⚠️ **Entries before 2026-08-06 are RECONSTRUCTED**, written on 2026-08-06 from
`CLAUDE.md` prose, commit messages and git dates — not contemporaneous, and not
exhaustive. Sessions in that window logged into CLAUDE.md instead, which is why
it grew from 62 lines (2026-06-22) to 727. Treat a reconstructed entry as a
pointer to the code + commit, not as a full record; anything from 2026-08-06 on
is written as it happens.

---

### 2026-07-13 — Storage v2: light tree + heavy blobs *(reconstructed)*

A single `conversations.json` held every node's `token_logprobs` and `raw_meta`,
and the browser OOM'd loading it. Split into per-workspace files carrying a LIGHT
tree (nodes keep `has_*` flags) plus write-once heavy blobs fetched on demand
(`node-blobs.svelte.ts`, batch `ensure()` micro-batched into one POST).

Design + migration: `docs/STORAGE_V2.md`; as-built endpoints in
`docs/API_CONTRACT.md`. Commits `938ebab`, `e9f9534`.

---

### 2026-07-14 — `web/` converted tabs → spaces *(reconstructed)*

One-time conversion, recorded in `.editorconfig` (`ac7ebf6`). Reason is
agent-ergonomic rather than aesthetic: exact-match `Edit` calls against
tab-indented source fail constantly on invisible whitespace. Don't reintroduce
tabs.

---

### 2026-07-21 — The false-grey forensic: `/v1/models` is capped, not a window *(reconstructed)*

**Symptom.** Roughly half the discovered runs rendered greyed-out/unsampleable,
including runs that sampled fine when you sent to them.

**The false theory.** Earlier notes explained this as tinker maintaining a
*rolling window* of sampler weights — old checkpoints silently evicted. That
theory was wrong, and it was load-bearing: it made "half your runs are dead"
look like an upstream fact to live with rather than a bug to fix.

**The actual cause.** Availability was probed with the OpenAI-compatible
`GET /v1/models` listing, which is hard-capped at roughly the ~20 newest
checkpoints — while the inference endpoints serve unlisted paths perfectly well.
So the probe reported "gone" for anything older than the cap, and the eviction
story was a rationalization of the cap's shape.

**The fix** (`baa9c37`): sweep the REST `list_user_checkpoints` instead —
truth-based, ~0.2 s, checks both failure modes (base model no longer served;
sampler weights expired or deleted). `sampleable` can be trusted since.

**Ground truth as settled that day.** Sampler weights are NOT windowed. A
checkpoint persists until its per-ckpt TTL expires (`expires_at=None` = never) or
someone deletes it. 42 of 54 discovered runs were live; the April
negation_neglect ones were genuinely gone.

**The lesson that outlived the bug**, and the reason this entry is long: one
confirmatory draw is not verification. The window theory survived because every
check sampled the same stratum it predicted. Sample both directions — a run the
probe calls dead AND one it calls live — before believing a probe about an
external system. (Also in project memory as `falsification-probe-discipline`.)

---

### 2026-07-24 — The cross-tab layout clobber: four workspaces corrupted *(reconstructed)*

**What happened.** Four of Clément's live workspaces were persisted to disk with
another workspace's panel layout — wrong models, silently, with no user action on
the losing tab.

**Mechanism.** The state bus (`api/state.py`) is ONE `PlaygroundState` per server
PROCESS. That was right when a workspace was just "the panels on screen"; since
then a workspace gained persisted identity (panel layout, per-panel models,
system prompt). A per-workspace value in a process-global slot is a clobber
waiting to happen:

> tab A opens workspace X → pushes X's layout onto the bus → tab B (on workspace
> Y) mirrors the bus → B now shows X's models → B's `syncPanels` sees X's panel
> ids as new, calls `save()` → 400 ms later Y is persisted with X's models.

The CLI could trigger it too.

**Recovery** was luck, and worth remembering as a technique: each node's
`raw_meta` happens to record which sampler produced it, so the true layout was
reconstructible from the transcript itself
(`scripts/repair_panel_layouts.py`).

**Fixes, all three still in the tree.** (1) `web/src/lib/bus-scope.ts` — every
bus message is stamped with the workspace it describes, and a client adopts
workspace-scoped fields only when the stamp is its own; sampling params stay
deliberately global, which is the point of a shared bus. (2) Panel-layout
history — `<id>.layouts.jsonl`, one `{ts, panels}` per CHANGE, capped at 50, so a
future accident is a lookup rather than a forensic exercise. (3) A tripwire
warning when a save replaces a ≥2-panel layout with another ≥2-panel one sharing
NO model — the clobber's shape, which no human action produces.

**Known to be a stopgap.** The root cause is "the browser is the sole writer of
workspace state, and the bus is a process singleton". Stamping closes the
corruption; the ops protocol in `docs/HANDOFF_SERVER_AUTHORITY.md` would make it
structurally impossible. See `ideas/server-authority-subsumes-scoping-fix.md`.

---

### 2026-07-24 — Smokes must never run concurrently *(reconstructed)*

`browser_state_reprime.py` kills and restarts a server mid-run, so any smoke
running in parallel fails with a bogus error. That produced a false
"the fix doesn't work" on the cross-tab corruption smoke and cost real debugging
time on a fix that was correct.

`scripts/smoke.sh` takes a lock and runs the token-free set SERIALLY; use it
rather than invoking smokes by hand. Its preflight (`cba3c59`) also warns about
leftover *dev-isolated* instances — deliberately not about "a tinkerscope is
running", which would fire on Clément's own live instance every run and be tuned
out within a day.

---

### 2026-07-24 — `plugin.json` declares no `version`, on purpose *(reconstructed)*

Claude Code keys the plugin cache by `version` when the manifest declares one and
by COMMIT SHA when it doesn't. Declaring a version therefore strands consumers on
a stale cache until someone bumps it. Omitting it makes every commit its own
cache entry — no bump, no hook.

`claude plugin validate --strict` warns about the omission; that warning is the
intended trade, and Anthropic's own feature-dev / code-review / frontend-design
plugins take the same one. The expensive alternative is visible in
`~/automation/claude-lab`, which ran a post-commit hook rewriting `version` to
`<base>-<shortsha>` and making a SECOND commit to carry it — doubling the repo's
commit count to hand-roll what SHA keying does for free.

Full derivation (cache-layout evidence, both directions confirmed live):
`~/.claude/ideas/plugin-versioning-cache-key.md`.

---

### 2026-07-29 — The chart inspector must not be addressed by index *(reconstructed)*

`ChartModal`'s `data` is rebuilt on EVERY streamed sample, and bars come and go
mid-batch: the streaming pseudo-turn appears then retires at fold, a panel gains
its first sample, the think-split's second bar arrives. An inspected bar held by
INDEX therefore silently re-points at a different bucket while you read it.

Fixed by addressing the inspected bar with a stable ref (`panel` id + `pop` +
`sub`, resolved to an index at render time), and by moving the per-sample
thinking folds out of the `<details>` DOM into `thinkOpen` state — a recreate
resets DOM-held state.

Gotcha worth generalizing: **any user-set state living in the DOM inside a
re-derived `{#each}`** has this bug latent. Open sweep:
`ideas/dom-held-ui-state-sweep.md`.

Also landed that day: `smoke.sh --baseline <ref> <smoke>`, which runs the working
tree's smoke against the app at an older ref (`39fb7c1`). Motivation was direct —
two successive versions of one smoke passed for the wrong reason. A smoke written
for a bug you just fixed proves nothing until you watch it FAIL without the fix.

---

### 2026-07-30 — localStorage's 5 MB cap, and how quietly it failed *(reconstructed)*

The static site's write overlay lived in localStorage, which made a real
workspace impossible to install — and it failed SILENTLY: the write threw, the
code caught and warned, and subsequent reads simply came back empty.

Measured on this box: **localStorage 4.98 MB vs IndexedDB 6442 MB** per origin.
Moved the overlay to IndexedDB (`overlay-store.ts`, `9558a5e`) with a sync-read /
async-flush shape so the ~30 read sites in `api-static` were untouched; every
`staticApi` method awaits hydration through ONE wrapper (`gated`) rather than 30
individual awaits. `readOverlay` clones on read, preserving the copy-per-read
contract `JSON.parse` had given for free.

Smoke `browser_pack_big.py` was **verified to fail on the pre-fix build**.

---

### 2026-07-30 — When a pack install asks, and the one opt-out to refuse *(reconstructed)*

Installing a pack from `?w=<url>` prompts, or doesn't, by this rule: a COLLISION
always asks (replace vs keep both — overwriting discards what's there); a
non-colliding install asks on a LIVE instance only, because there `?w=<path>`
makes the server read the local filesystem into the real state dir and any page
can navigate a browser to localhost. A static site just installs: per-site
IndexedDB, deletable, can't touch baked workspaces, and pack content is
HTML-escaped before render.

Recorded because my first answer was over-broad (prompt on everything) and
Clément pushed back; the narrower rule is his. The durable part: **a query
param to skip the prompt is the one shape to refuse** — the URL author controls
it, so it deletes the check instead of configuring it. Any future opt-out has to
be author-controlled: export-time or launch-time.

---

### 2026-08-03 — Token overlay: two bugs only a screenshot found

**Canvas z-order.** The first overlay used one canvas per row, painted behind the
row. `.sample-reasoning` has an OPAQUE background, so every thinking block came
out completely flat while the response below it painted correctly. The smoke
passed (it asserted on the message body's canvas), svelte-check was clean, and
the code reads fine. Only a picture of a real workspace showed a whole region of
the feature silently not working.

Fix: one canvas PER prose container, inserted as that container's first child at
`z-index: -1` — negative-z paints after the container's own background and before
its text, which is the highlighter order, and it makes the tint scroll and clip
with the reasoning fold for free.

**Alignment coverage counted the wrong thing.** `visibleCoverage` first measured
span EXTENTS, which let a prefill turn's tail-only stream score 0.502 — one hair
past the 0.5 trust guard — and paint garbage across text no token had claimed.
Now it counts MAPPED chars (spans carry `mapped`), and a low-density span, the
scatter-match signature of a desynced walk, is nulled.

This is the second time in one day a UI change shipped broken past passing smokes
(the first: the per-panel stop mounted in the message HEAD, which follow-scroll
puts off-screen exactly while a turn streams). Two different failure modes, both
invisible to every assertion, both caught by looking at a rendered image. See
`ideas/screenshot-verify-ui-changes.md` + `…-second-vote.md`.

**Corollary technique.** For anything PAINTED rather than DOM'd, the assertion is
pixel readback: `getImageData` + a non-zero-alpha COUNT for "did it draw at all",
a single-pixel SAMPLE under a named element for "did it draw the right thing".
Both immune to Playwright's click-through-scroll problem.

---

### 2026-08-03 — `--baseline` can pass for nothing (self-hosting smokes)

`smoke.sh --baseline` builds and serves the app at an older ref — but a smoke
that spawns its OWN server or builds its own site must resolve its checkout via
`os.environ["TSCOPE_APP_DIR"]`, or it silently exercises the working tree and
PASSES. That false-OK happened on `browser_state_reprime` and nearly on
`browser_pack_big`.

The failure mode is a green checkmark, which is the worst shape a test failure
can take. Proposed mechanical guard: `ideas/baseline-detect-worktree-leakage.md`.

---

### 2026-08-06 — `IDEAS.md` → `ideas/` folder

481-line monolith with implemented entries struck through in place → 35 open
files + `ideas/done/` for the 8 shipped, indexed by `ideas/CLAUDE.md` in themed
clusters. Layout mirrors `~/.claude/ideas/`, which is the reference: `##` title,
signed and dated at the bottom, retire by `git mv` into `done/` with a
`**Done DATE**:` line rather than striking text through. Commit `9e77b79`.

---

### 2026-08-06 — This file exists now

Clément asked where the forensics were being stored, having noticed they were
going into `CLAUDE.md`. Correct answer: nowhere dedicated. Repo-level forensics
were landing in `CLAUDE.md` (auto-loaded context doing archive duty),
`docs/HANDOFF_*`, and commit messages — the last carrying the most and being the
least findable. The repo had no `ENGINEERING_LOGS.md` at all, against the
standing tooling-repo convention.

Project memory is NOT part of that list, per Clément the same day: local lessons
belong there and stay there, and being session-start injected is exactly why. It
was working as intended; this file is for the repo-portable half it was never
meant to hold.

Created it, backfilled the entries above from what was already written down, and
trimmed the fattest narratives out of `CLAUDE.md` — keeping every ⚠ RULE there,
because that file is what gets auto-loaded and a rule nobody reads stops working.
Going forward: rule in `CLAUDE.md`, story here.

---

### 2026-08-06 — The loom ships: token-level counterfactual branching

Clément re-raised `ideas/loom-branch-from-token.md` ("click an alternative in
the token popover, sample from there"). Shipped it the TOKEN-LEVEL way rather
than the text-prefill way the idea file originally sketched, on a finding from
this morning: the stored alternatives carry token ids (`[text, tid, lp]`), so
the counterfactual prompt is `rendered prompt + stored prefix tids + alt tid` —
exact, no re-tokenization drift, and mid-thinking cuts work for free because
special tokens ride along as ids (no reopened `<think>`, no `_tml_continue`
dance).

Decisions worth remembering, and why:

- **`continue_tokens` extends `region_ids`, not the message list.** Everything
  after the generation prompt is assistant-authored by construction, so the
  existing prefill machinery (parse `region + completion`, set
  `prefill_incorporated`) absorbs the whole feature — the loom composes with a
  text prefill (ids append after the prefill's rendered region) and needed no
  new parse logic for any renderer, tml_v0 included.
- **One teacher-forced pass scores the whole stream.** `_token_logprobs` is
  called with the PRE-append prompt and `continue + fresh` as the completion,
  so the loom branch carries a full ghost-free heat map: the forced prefix
  re-scores under its true context (matches the original to ~1e-2), the picked
  alternative under its recorded top-K number. Cost unchanged (it was one extra
  call per sample before too). This is also the primitive
  `ideas/score-authored-ghosts.md` needs, which is why that one stayed parked.
- **The fire anchors to the TURN, not the panel** — token ids are
  tokenizer-specific, so `loomBranch` recovers base_model / sampler_path / the
  EXACT renderer from the turn's `raw_meta` (`lib/loom.ts:parseRawMetaModel`,
  line-anchored regexes — safe because json.dumps escapes newlines) and ships
  `renderer_name` as an explicit override. A since-switched panel picker can't
  feed one tokenizer's ids to another.
- **`params_scope: 'call'` on the loom fire.** It sends an explicit `thinking`
  (the turn's own mode, inferred from `node.thinking` ?? CoT presence); in the
  browser's usual 'global' scope that write-back would silently flip the
  sidebar's thinking toggle. 'call' scope samples with it and writes nothing.
- **Pin-to-interact popover.** `TokenPopover` is `pointer-events:none` in hover
  mode (deliberately — it must not steal the hover), so alternatives can't be
  hover-clickable: a token CLICK pins the card (`pinned` prop → buttons +
  "↺ resample from this token"). Closing gestures (Esc / outside mousedown)
  live in the popover once; pin state lives per view. In overlay mode a click
  that's part of a text-selection drag is ignored (`getSelection().isCollapsed`).
- **Display-vs-stored indices**: the views see `withPrefillGhost`'s stream, so
  `lib/loom.ts:loomCut` owns the translation (leading prefill ghost shifts by
  one; anything at/past an edit ghost is refused — no ids to replay).

Verified end-to-end by `tests/small-smokes/browser_loom_live.py` (real tinker
sampling): exact prefix-tid replay, picked alt at the cut, ghost-free re-scored
stream with <0.15 nats drift vs the original prefix, renderer anchored while the
sidebar thinking toggle was deliberately flipped, and the toggle unclobbered
after. Route-level contract in `tests/test_api.py::test_chat_loom_continue_tokens`.

---

### 2026-08-06 — Loom provenance display, and Continue rides the loom

Two follow-ups Clément asked for the same afternoon the loom shipped.

**Fork-point display.** A loom branch looked like an independent draw — nothing
marked the replayed prefix or the cut. Now every loom sample is stamped
(`loom_cut` = forced-entry count, `loom_text` = the forced prefix as
frame-normalized display text — the backend prepends the auto-opened `<think>`
for DeepSeek/Kimi/Qwen3.5, whose tag lives in the PROMPT, so the text splits
like an authored prefill), the fold persists both on the node, and the display
reuses the PREFILL coloring machinery end to end: `forcedSplit` in ChatMessage
composes `prefill + loom_text` per row AND per sample card (n counterfactual
draws visibly share their forced prefix), the token views dot-underline the
replayed region + draw a fork tick at the first fresh token, and the popover
says "⑂ replayed" while still showing the real probability. Two traps hit on
the way: `parseSample` (state.svelte.ts) is an ALLOWLIST — new wire fields die
there silently unless added — and the loom text needed `.trim()`-normalized
split parts because the decoded ids keep whitespace the parse strips.

**Continue = the loom with the cut at the end.** Clément's suggestion, and the
decomposition is exact: replaying ALL stored tids extends the turn, cutting at
the think-close token is Shift+Continue. So `#fireContinue` now prefers the
token path — the continued turn keeps every probability (no more ghost prefix)
and wears the fork display — with the TEXT-prefill path kept as the fallback
for: no/partial token data (OpenRouter, edits), tml thinking-resume (no
locatable `</think>` token — `thinkResumeCut` returns null), and a
since-switched panel model (comparable-key check via the new `resolveModelKey`
seam; a switched panel legitimately re-renders text for the NEW model, and
token ids must never cross tokenizers). One empirical fact gates it all: a
stop-finished stream INCLUDES its end-of-turn token (verified on 6/6 DeepSeek
V3.1 stop-finished streams — `<|end_of_sentence|>` last), so
`_strip_trailing_stop` removes it before the replay or the model would open a
fresh turn instead of extending (str + int stop forms; unit-tested with a fake
tokenizer, `tests/test_tinker_sampler.py`).

---

### 2026-08-06 — First-use feedback: the phantom seam \n, and the fork tick dies

Clément used the loom within the hour and reported a `\n` appearing between the
prefill and the sampled text. Root cause was PRE-EXISTING, not loom-new:
`renderPrefilled` draws the forced prefix and the continuation as two separate
markdown documents, so a mid-paragraph boundary closes the `<p>` at the seam and
opens a new one — a phantom line break. Authored prefills mostly end at natural
break points, which is why it passed as "acceptable" for a month; the loom cuts
mid-sentence EVERY time, making it glaring. Humbling detail: the artifact is
plainly visible in the smoke's own overlay screenshot, and I read it as "the
picked alternative must have ended with a newline" — rationalizing a rendering
bug as data. Fix: `seamInfo` (highlight-render.ts, pure + tested) detects a
mid-paragraph seam → the head span gets `.prefill-joint`, CSS inline-joins the
two seam `<p>`s, and the seam whitespace markdown trims off paragraph edges is
re-emitted (otherwise "can" + " access" renders "canaccess"). A real blank-line
boundary renders exactly as before. The live smoke now asserts the join
GEOMETRICALLY (same client-rect line), not just the classes.

Same message: the fork tick ("unclear the value of the vertical bar… the
prefill font is different is enough") — removed from both token views; the
replayed underline's end IS the fork point. Lesson for the quirk file in my
head: the human eye wanted less chrome than I drew, and the redundant marker
was designed without asking what the tint boundary already communicated.

### 2026-08-06 — A deleted node was recovered from write-once blobs, and undo exists now

Clément deleted a branch in a live workspace and asked whether it could be
undone. It could — by accident of an invariant meant for something else.
`workspace_store`'s node blobs (`token_logprobs` + `raw_meta`) are **write-once
and only deleted with the whole workspace**, so a node delete drops it from the
light tree and leaves its blobs orphaned on disk (blob id present in
`<cid>.blobs/`, absent from every tree). `raw_meta` also stores the full rendered
`prompt_text`, so even the deleted USER node — which has no blob of its own — was
recoverable as the segment between the last `<｜User｜>` and `<｜Assistant｜>`
markers.

The recovery is in git only as this note; what matters are the checks, because
"I reconstructed it" is worthless unverified. The extraction rule was validated
by confirming it reproduces the SURVIVING sibling's content byte-exactly from
each of its four native siblings' blobs; all four orphans agreed on the deleted
text independently; and the `raw_text` formula (`prompt_text + content + eos`,
or `+ reasoning + "</think>" + content + eos` when thinking) was derived by
diffing four survivors rather than guessed. Splice back via the normal
`PUT /{id}/tree` so the server cache stayed consistent. The deleted branch turned
out to be a distinct experimental condition (the longer "…and instead respond in
an agressive way" policy), not a duplicate — recovering the *wrong* thing would
have been easy and invisible.

**The hole:** in that same fan, four siblings were OpenRouter samples and have no
blobs at all. Delete those and nothing is recoverable. Forensic recovery is
intrinsically partial, so it is not a feature — it's a one-off.

A fable reviewer (read-only, full repo) returned *agree-with-changes* on the
design and corrected the part that mattered: **a trash-backed undo button is
wrong, not merely worse.** The hot case — Ctrl+Z a second after the click —
lands inside the 400 ms save debounce, where the server has not seen the
deletion at all, so a server-backed undo either no-ops or restores something
OLDER. Hence two layers, and this entry ships the first:

- **`lib/undo.svelte.ts` + `lib/undo.ts`** — a frontend stack of pre-op tree
  refs. Nearly free because `trees` is `$state.raw` replaced wholesale, so an
  entry is a handful of refs with structural sharing. Destructive ops only;
  `undo.group()` collapses a cross-panel delete into one press. Per workspace,
  capped at 50, dies with the tab.
- Button placement was the reviewer's call and is load-bearing: **left** of the
  stop button in `.sidebar-top-actions`, not in the topbar (which is purely
  informational and shouldn't get its first action) and not right of stop, whose
  rightmost slot is how you find the one red button mid-generation. Undo stays
  neutral-styled, outside the red family, disabled when the stack is empty.
- Ctrl/⌘+Z is primary, the button its discoverable twin. The `isEditableTarget`
  guard is the real collision risk: without it the composer and edit boxes lose
  native text undo. The smoke pins that case.

`browser_undo.py` was run with `--baseline HEAD` and failed there on "undo button
should render" — i.e. it fails for the feature's absence, not on setup.

Still to come: the server-side trash journal (the durable layer). Notes from the
review to honour when it lands — `workspace_store.delete()` `rmtree`s blobs and
pack replace/reseed routes through it, so the worst click in the app is still
uncovered; `_persist` is a better diff site than `save_tree` because it also
catches `upsert`'s wholesale tree replacement; retention by age+bytes, not entry
count (one discarded n=30 thinking fan is ~1 MB); record each vanished subtree's
sibling INDEX and the pre-delete `selected`, or a restore silently reorders every
‹k/N› cycler.

### 2026-08-06 — The trash journal: deletion is recoverable server-side

The durable half of the undo work above. Every node a save makes disappear is
journaled to `<cid>.trash.jsonl`; `tinkpg trash list|restore|purge` is the front
end. Restore splices the light nodes back at their recorded sibling index, and
blobs need no work at all — write-once means they were never removed, so a
restored turn comes back with its logprobs.

Four decisions worth keeping, all from the fable review rather than from me:

**The diff hooks `_persist`, not `save_tree`.** `_persist` is the choke point for
EVERY workspace write, so the same twelve lines also catch `upsert`'s wholesale
tree replacement (an API caller — an agent, a forensic splice gone wrong — could
previously clobber a workspace with no trace). It also runs AFTER save_tree's
legacy `{tree, compare_tree}` seed, which is the difference between a clean diff
and mass-journaling phantom deletions on a legacy workspace's first save.

**Diff by node ID only.** Node bodies legitimately change on a save —
`#lightenShipped` swaps inline heavy fields for `has_*` flags — so a content diff
would journal noise on every write.

**`DELETE /api/workspaces/{id}` had to go soft.** It was the one deletion the
journal structurally cannot cover, because the journal lives *inside* the
workspace and `rmtree` took the evidence with it. Now the light file, blobs,
layout history and journal are moved to `workspaces/.deleted/<id>-<ts>/`, aged
out at 90 days. This is also the path `pack.py`'s collision-replace and
`--reseed` take (delete-then-upsert, deliberately, to refresh write-once blobs),
so a pack install could previously destroy an existing workspace's blobs outright.

**Retention is age + bytes, never entry count.** One discarded 30-sample thinking
fan is ~1 MB of `reasoning`/`raw_text`; a count cap would either hoard gigabytes
or evict a single big deletion.

Known limits, deliberately: the browser is still the sole writer of trees, so a
CLI restore RACES an open tab — the tab holds the post-delete tree and its next
dirty save re-deletes the restored nodes. Soft failure (the re-delete
re-journals), and "reload the tab after a restore" is in the CLI skill and in the
command's own output. `HANDOFF_SERVER_AUTHORITY` dissolves this if it ever lands:
the ops protocol retires PUT `/tree`, the ~40-line diff detector dies with it, and
`restore_trash` becomes the implementation of an `add_nodes` op unchanged — which
is why restore was written as a pure splice rather than folded into the diff.

Also not covered, and no way to cover it server-side: content deleted before its
first save (inside the 400 ms debounce, or in an unmaterialized draft) never
reaches the server, so there is nothing to diff. That case is exactly what the
frontend undo stack is for — the two layers are complementary, not redundant.

`tests/test_trash_journal.py` (14 cases) pins recording, the non-recording cases,
index-faithful restore, blob survival, and the soft workspace delete. CLI verified
end-to-end against an isolated instance: delete `a2` from a 3-sample fan → list →
restore → `children == [a1, a2, a3]`, not appended.

---

### 2026-08-06 — Picking a sample card exits the card view

The "view all samples" eye (`samplesOpen` in +page) was deliberately keyed on
the turn's USER-parent so that selecting a different card *inside* the open view
did not collapse it — the reasoning being that the view is for comparing, so
changing the active child shouldn't tear it down. In use that turned out
backwards: picking a card is the DECISION the view exists to support, and after
making it you're stuck looking at cards with the turns below still hidden, one
more click from the thread. So `make active` now closes the view onto the picked
branch (`selectSample` wrapper in +page; `branchOps.selectSample` stays
UI-agnostic and knows nothing about `samplesOpen`). The parent keying stays —
it still makes the view survive tree mutations that swap the active sibling.

Both card tooltips lost their parentheticals in the same pass ("(hides later
turns)", "(others stay ‹k/N› siblings)") — Clément; the one-short-line tooltip
rule is in CLAUDE.md and those were drifting into mechanism.

`browser_samples_view.py` step 4 inverted with the behavior (it pinned
"picking must NOT collapse"); verified failing at exactly that assertion on
`--baseline main`.

---

### 2026-08-06 — "Peek at training data" deleted; ⇧+copy hands out the dataset path

Harry's original playground could load a run's training JSONL into the composer:
sidebar icon → modal (path + count) → `POST /api/load-dataset` → N randomly
sampled records dumped into the message box as `[DOCUMENT k]` blocks. Clément
doesn't use it. Deleted whole: `routes/datasets.py`, `DatasetModal.svelte`, the
sidebar button, `api.loadDataset` + its static stub, the Help-modal entry, the
endpoint row in `API_CONTRACT`, the four `test_load_dataset_*` tests, and
`settings.safe_path` — which existed ONLY to confine that endpoint's
client-supplied path, so it went from a security control to dead code the moment
the route did.

What survived is the useful half: `config.json`'s `dataset_builder.file_path`
resolved to a real file. It's now a copy target — **⇧ on a panel's copy button**
yields the training JSONL instead of the checkpoint's sampler path (same button,
same tick; the icon and tooltip swap while ⇧ is held, so the chord is visible
before the click). Only for DISCOVERED runs — a loose `ckpt:` / base / OpenRouter
panel has no config.json — and plain click is unchanged.

`Run.dataset_path` is now **absolute**, where it used to be root-relative. That
was right when the only consumer was an endpoint that took a root-relative path
and re-resolved it under the serving root; it's wrong for a string whose entire
purpose is to be pasted into a dataset viewer or a script running from its own
cwd. Same reasoning that keeps the run *id* uncopyable (`+page`'s copy-button
comment): a path only the server can interpret is worse than no path.

Pins still record `dataset_path`, so the site-export privacy note in `CLAUDE.md`
(a `--workspace`-filtered export publishes pins wholesale, local paths included)
is unchanged — and now the published path is absolute rather than root-relative,
which is *more* revealing. Nothing new leaks that a pin's `question`/`response`
didn't already, but it's the reason that note stays.

`browser_run_ckpt_copy.py` grew the ⇧ case (tooltip retarget, `.shift-alt`
restyle + glyph swap, and the absolute-path copy) and now honors
`TSCOPE_APP_DIR` — it is self-hosting, and without that `--baseline` silently
exercises the working tree. Verified failing on `--baseline HEAD` at exactly the
new assertions.

The glyph assertion caught a smoke-timing trap worth knowing: the transient ✓
from a preceding copy lasts 1200 ms and outranks the ⇧ glyph, so a shift check
within that window reads `check`, not `dataset`. The smoke waits for
`.btn-copy-sp:not(.copied)` first. The precedence itself is correct — feedback
for what just happened beats a preview of what a modifier would do.

---

### 2026-08-06 — Loom pin: the popover chased the mouse instead of closing

Clément, within minutes of the loom shipping: click a token, click somewhere
else, and the card follows the pointer instead of going away.

Two bugs stacked, both from the same design choice — `hover`/`pop` are FROZEN
while a card is pinned (`onMove` and `onLeave` in TokenHeatOverlay both bail on
`pinned != null`), so the pinned token keeps its outline.

  1. `unpin()` cleared only `pinned`/`pinnedPop`. The instant the pin dropped,
     the `{:else if hover…}` branch rendered the HOVER card at the stale
     position, with `onMove` live again — so it tracked the pointer.
  2. `onClick` read that frozen `hover`. A mousedown outside unpinned (the
     popover's own close gesture), then the click handler re-pinned the SAME
     token at the new click point. So the card visibly jumped to wherever you
     clicked — which is what "follows the mouse" actually looked like — and
     clicking a DIFFERENT token re-pinned the old one.

Fix: `unpin()` clears the frozen hover too (the next mousemove re-establishes it
honestly), and the hit-test moved into `hitAt(ev)` so a click resolves its own
token instead of reusing hover state that is deliberately stale. TokenLogprobs
has the same pin/unpin pair but not the bug — its `leave()` is NOT pin-guarded,
so hover clears on the way out, and `clickTok` already takes the clicked index.

`browser_token_overlay.py` grew a pin/close scenario; verified failing on
`--baseline main` at exactly that check and nothing else.

---

### 2026-08-06 — Shift+edit works on assistant rows too

It was never forbidden, just unimplemented and undiscoverable: `startEdit`
always forwarded `e.shiftKey`, but `applyEdit` only honoured `copyDownstream` in
the `role === 'user'` arm, and the icon/tooltip swap was gated on user rows, so
nothing hinted the modifier existed. An assistant edit therefore always minted a
LEAF — every turn below stayed on the sibling you had just edited away from,
which is wrong for the "rewrite one reply in the middle of a thread" case.

`editAssistant` takes `copyDownstream` now and grafts the original active path
below it onto the new node. The deep-copy loop came out of `editUserForkCopy`
into a shared `graftDownstream` rather than being written twice. Heavy fields
(`token_logprobs`) are deliberately not carried onto copies: a copy is a new node
with no blob behind it, and those ids belong to what the model actually sampled.

Smoke `browser_shift_edit_assistant.py`; unit case in `tree.test.ts`.

---

### 2026-08-06 — "Color by match" becomes "Color tokens by": logprob / match / both

Clément wanted the match tint layered OVER the surprisal heat rather than only
replacing it, then pointed out the control was named for a feature rather than
for what it shows. Both changes are the same idea: the toggle now answers "what
does this tint MEAN", with the states named after the quantity — `Logprob`
(the amber, alpha ∝ -logprob), `Match`, `Both` — and the store's mode values
match the labels, so no layer translates between UI and code.

The layering is flattened, not stacked: `compositeOver` composites the match band
onto the heat into ONE rgba. Source-over of two translucent layers is
backdrop-independent, so this is exact, and it means neither painter (canvas
overlay, CSS gradient) learns about layers — which is the property that keeps
them from drifting. A zero-match token under `both` composites to exactly the
heat, which is why that mode reads as "amber unless something matched".

Legacy localStorage `'1'`/`'0'` migrate to `match`/`logprob`.

---

### 2026-08-06 — Ctrl+⇧ opens a run's training data in samplescope

Follow-on to the entry above, Clément's idea: the ⇧+copy dataset path exists to be
pasted into a dataset viewer, and the viewer (samplescope, same box) is one
keystroke away. `POST /api/samplescope/open {run_id}` → `{url, started}`, and
Ctrl+⇧ on the panel's copy button opens it in a new tab.

**No samplescope change was needed**, which was the surprise — the plan started as
"add a `--json` output to `sscope` so tinkerscope can parse it". Two things it
already has: an instance registry at `$XDG_STATE_HOME/samplescope/instances.json`
(`{pid, host, port, scan_roots, started_at}`, what its own `sscope view`
auto-targeting reads and its `test_discovery.py` asserts against), and a frontend
that opens `?path=<root-relative>` on mount. So discovery is a file read plus
`GET /api/health` for the authoritative serving root, and the hand-off is a URL.
No subprocess, no CLI parsing.

Deliberately NOT `sscope view open`: that mutates the shared view over HTTP as a
side effect of *preparing* a link, so a failure halfway leaves someone's screen
moved with nothing to show for it. The URL param is declarative and samplescope
performs the open itself. It does still move other samplescope tabs — its view is
server-side and shared by design, and that's in the tooltip + Help.

Two decisions with teeth:

- **The request carries a run id, never a path.** Any page the user visits can POST
  to localhost, and this endpoint STARTS A SERVER over a directory; taking a path
  would hand that choice to the caller. Resolving from our own catalog bounds the
  served tree to one tinkerscope already scans.
- **The spawned instance is detached** (`start_new_session=True`) and serves the
  SCAN ROOT, not the dataset's directory. Tying it to our process group would kill
  a viewer someone is still reading whenever tinkerscope restarts; serving the scan
  root means one instance covers every discovered run, and it's what `sscope
  <project>` by hand would have produced. `sscope serve` already refuses to start a
  twin on an identical root set, so a race just prints and exits.

Popup blockers shaped the frontend: `window.open` after an `await` has lost the
user-gesture stack and is blocked, so the tab opens SYNCHRONOUSLY in the click
handler with a placeholder ("starting the viewer can take a few seconds") and gets
navigated when the URL returns.

**A privacy regression came out of this, from the previous entry rather than this
one.** Making `dataset_path` absolute meant a published site's pins carried an
absolute path on the author's box instead of a root-relative one. `site_export` now
strips `dataset_path` from every published pin, filtered or not: a reader has no
such file, a static site lists no runs to link it to (`models.json` is `[]`), so it
was pure disclosure. `test_published_pins_never_carry_the_local_dataset_path` pins
it on the unfiltered path, where the rest of the pin still ships.

`browser_samplescope_open.py` runs the whole chain on an ISOLATED `XDG_STATE_HOME`
— which both forces the spawn branch (nothing serves a scratch tree) and makes the
smoke invisible to the human's own samplescope, whose shared view it would
otherwise hijack mid-session. It asserts through to `GET /api/state` after a real
browser opens the URL, because everything up to that point is reachable by curl and
the URL→shared-state sync is exactly the part that isn't. Verified against the live
:8768 instance too (read-only: registry → health → `datasets/info`, no view change).

---

### 2026-08-06 — The clobber returns: a restart race beats the stamping, layout ownership inverted

**What happened.** "inkling cigarette" (9b114ceb) was persisted with "value
guarding v2"'s 10-panel layout — wrong models on its 5 real panels, 5 foreign
empty panels, and the OTHER workspace's `send_targets`/`seen_panels` shape. Same
disease as 2026-07-24, different door. Trees were untouched (the clobbering
write was a layout-only PATCH); recovery = `scripts/repair_panel_layouts.py
--apply` + one manual PATCH to drop the foreign panels (pre-repair body backed
up under `scratch/layout-backups/20260807T014233Z/`).

**Why the 2026-07-24 fix didn't hold.** The stamping (`bus-scope.ts`) filters
what a tab ADOPTS from the bus — but every persistence path still READ the
process-global mirror back as "my layout": `#doSave` persisted
`live.state.panels`, `persistSession` snapshotted it into the shared
`last_session` pref, and `claimBus`/`#reprime` re-published it as a claim
stamped with our id. The mirror has three legitimate raw-adopt windows
(bootstrap `mine==null`, `myId==null`, unstamped messages) — and the server
restarted 6 minutes before the corrupt save (23:15:43 → 23:21:49 UTC), which
opens all of them at once: amnesiac bus, both tabs re-priming, a reload
raw-adopting whichever tab claimed first. Once the victim tab's mirror held the
foreign panels with its own workspace active, `syncPanels` defaulted the 5
foreign panel ids into its send-targets (the clobbered bookkeeping is exactly
the victim's own sets grown by the foreign ids — that's what pinned the
mechanism) and the next layout save wrote the foreign layout to disk.
Two amplifiers, both fixed:

- `claimFields()`/`claimBus()`/`#reprime` claimed with the MIRROR's panels —
  when the mirror is foreign (the exact case claiming exists for), the claim
  itself minted the chimera: workspace A's panels stamped B, which every
  same-id client then adopted wholesale.
- The server accepted any stamped patch when the bus was unclaimed
  (`current is None`), so a bare `{workspace_id}` push (the boot-time effect in
  +page sent exactly that) could restamp another workspace's panels.

Two silent-guard failures worth remembering: the "no model in common" tripwire
never fired because both workspaces genuinely used `cigarette_inkling` —
overlapping model sets across workspaces are NORMAL here, so that heuristic
false-negatives on precisely the workspaces most likely to be open together.
And `ownsBus` read the MERGED mirror's `workspace_id`, which mergeBusState pins
to our own on foreign messages — so it was constant-true and focus-reclaim was
dead code (nobody noticed because claims from open/switch covered the visible
behavior).

**The fix — invert layout ownership (frontend), anti-chimera rule (server).**
`ws.layout` is now THE authoritative client-side layout: set from the loaded
body, mutated only via `applyLayout`/`setPanelModel` (panel add/remove/reorder/
model-pick all route through it) or by bus messages STRICTLY stamped with the
open workspace (CLI lockstep rides on the bus keeping its owner's stamp).
Rendering (`panelSels`), `#doSave`, `persistSession`, `#currentLayout`,
`#freshTrees`, and every claim (`#busPanels`, re-prime via the new
`live.reprimeClaim` hook) read the store — `live.state.panels` is now only the
CLI-visible echo. `ownsBus` reads the new `live.busId` (the RAW incoming stamp,
tracked before the merge), which revives focus-reclaim. Server: a patch may
never CHANGE `workspace_id` without carrying `panels` (`state.py`
`_drop_foreign_workspace_keys`), closing the restamp hole; absent-vs-null
`workspace_id` is now semantically distinct (absent = CLI/same-owner trusted,
explicit different stamp = dropped without a claim). The boot-time
`{workspace_id}` push effect is deleted (every real transition claims).
Layout history now seeds the PRE-change layout on a workspace's first entry
(`workspace_store.py::_record_layout`) — today's incident had exactly one
history entry: the clobber itself, so the good layout had to be reconstructed
from `raw_meta` again instead of looked up.

**Verification.** `browser_two_tab_workspace.py` gained scenarios 5–6: a
deterministic poisoned boot (bus claimed as A, tab boots ?w=B with its
POST /api/state aborted via Playwright route interception → the old code's
mirror poison, sealed) must keep B's disk layout + render intact, and the
focus-reclaim must put B's TRUE panels on the bus (not the mirror's). Verified
to FAIL on the pre-fix build (`scripts/smoke.sh --baseline`) and pass after.
`test_state_workspace_scope.py` covers the anti-chimera rule (restamp without
panels dropped, even onto an unclaimed bus; explicit-null can't unstamp).

**Still open.** Unstamped `panels` patches remain trusted by the server (the
CLI contract), so a malicious/buggy unstamped writer can still repoint the bus
— but it can no longer reach any workspace's disk (the store only adopts
stamped-as-ours layouts). The real endgame is still
`docs/HANDOFF_SERVER_AUTHORITY.md` (ops protocol, server-authored folds);
this inversion is a compatible step, not a detour.

### 2026-08-10 — Ctrl+K cross-workspace search: server engine, palette, jump-and-reveal

Clément's ask: "I have a screenshot of a sample but forgot which workspace /
panel it was in" → a Ctrl+K palette searching everything. Scope settled up
front via three questions: full text scope (content + thinking + user messages
incl. thread system prompts) plus workspace-name matches pinned on top;
jump-and-reveal on click; cached-scan backend (his corpus measured ~30 MB of
light trees across 25 workspaces — the 1.1 GB of blobs are logprobs/raw_meta
and never hold text, so FTS would be engineering for a corpus 100× bigger).

**What shipped, and where the bodies are buried:**

- `api/search.py` + `GET /api/search`: linear scan with a per-workspace units
  cache keyed on `updated_at` (sound because this server is the store's sole
  writer — the same consistency model `workspace_store`'s own caches assume).
  The wire shape is index-agnostic on purpose; FTS can replace the internals
  without touching a consumer. `tinkpg grep` was already 90% of the semantics,
  CLIENT-side over a full `?bodies=1` fetch — it's now a thin client of the
  endpoint (same output, plus system-prompt and workspace-level hits; its
  `_snippet` helper died with the rewire).
- Snippets travel as a (before, match, after) TRIPLE, each whitespace-collapsed
  separately — collapsing had eaten the boundary space, so the first live
  `tinkpg grep` printed "NEEDLE-OFFPATHthe forgotten…"; the parts now re-add a
  single boundary space where the raw text had one. Pinned in both engines'
  tests (the static site scans client-side via `lib/search-scan.ts`, a mirror
  with fixture-twinned tests, node-split style).
- **The jump silently reverted and nothing errored.** First implementation
  called `ws.switchTo(id)` directly from the palette pick: the workspace
  switched, then switched BACK — no console error, no rejection, no notice
  (cost a debug loop with an unhandledrejection hook to even see the shape of
  it). Cause: the `?w=` effect is the SINGLE direction of control for switching
  between existing workspaces; a direct switchTo leaves the URL stale, the
  effect re-runs on the activeId change it just observed, and dutifully
  switches back to what the URL says. The dropdown knew this (`setWsUrl(id,
  true)` and let the effect switch); the palette now does the same, then POLLS
  for the switch to land before selecting the path. The rule generalizes:
  **any new "open workspace X" affordance goes through `setWsUrl`, never
  `switchTo`.**
- Reveal mechanics: `tree.ts` grew `selectPathTo` (whole-ancestor-chain select,
  same-ref no-op, whole-or-nothing on a broken chain); the row flash + thinking
  -fold open ride a self-clearing `reveal.svelte.ts` beacon. The fold is
  LATCHED open rather than bound reactively — the beacon clears after ~2.6 s
  and a reactive `open` would slam the fold shut mid-read.
- Palette keyboard: the input and its overlay both bound the same keydown
  handler at first — bubbling fired everything twice (↑ moved two rows). One
  binding, on the overlay; keys bubble up from the input.
- Smoke `browser_search_palette.py` (seeded, token-free, in smoke.sh DEFAULT):
  open/Esc, off-path jump + hidden-branch tag + flash + focus ring, reload
  persistence of the flipped selection, thinking-fold open on a CoT hit, fan
  collapse ("3 of 3 samples"), workspace-name switch. Its one failure during
  development was a non-retrying `inner_text()` snapshot assert — replaced with
  retrying `expect`s (the lesson: in an app whose state settles over ~300 ms of
  effects, every content assert retries or it flakes).

**Postscript, same day — the jump flaked 1-in-N: every URL-driven switch ran
TWICE.** The smoke re-run showed the revealed selection reverting to the old
sibling seconds after a successful jump (sticky, no error). Playwright request
logging proved the mechanism: every palette jump fired `GET
/api/workspaces/<id>` twice — the `?w=` effect re-fires on dep changes
mid-switch (`ws.busy`, list timestamps), spawning a second full `switchTo`
whose wholesale `trees` assignment clobbers any tree edit made right after the
first switch landed. Only the palette noticed because only the palette mutates
the tree immediately after a switch; the dropdown path has silently double-
fetched every workspace open for who knows how long. Fix in the store, not the
caller: `switchTo` now COALESCES same-id in-flight switches (returns the
in-flight promise); different-id switches still supersede via `#switchSeq`.
Measured before/after over 6 seeded trials: 2 fetches → 1, selection survival
5/6 → 6/6.

**Postscript 2 — the smoke still flaked on a COLD server: boot vs the `?w=`
effect is a second duplicate-load path.** With switchTo coalesced the seeded
6-trial harness went clean, but smoke.sh (fresh server every run) kept
reverting. The probe's request log told the story: on a cold server the boot
sequence reaches `ws.load()` late (~1.2s — it waits on the models fetch), so a
palette jump fired before boot completes hits a window where BOTH `ws.load()`
(which honors `?w=`) and the `?w=` effect (`switchTo`) open the workspace —
two body fetches again, same clobber, different pair of racers. Two guards
close it: (1) the effect stands down until `wsLoaded` — boot owns the URL
during boot, and the effect re-runs when the flag flips; (2) boot's URL
NORMALIZE (`setWsUrl(activeId)`) now only fires while the URL still says what
boot captured at its start — otherwise it un-navigates a `?w=` pushed mid-boot
(the jump wouldn't revert; it would silently never happen). Verified: probe
shows one body fetch and the selection holding from t=1.0s; the real smoke
passed 3/3 consecutive cold-server runs (was ~1-in-2 failing), and the
workspace-URL / two-tab / pack-link smokes still pass. Moral for future
sessions: **"open workspace X" has exactly one sanctioned entry — change the
URL and let the effect drive; and anything that MUTATES a tree right after a
switch must wait for the switch machinery to go quiet, because a straggler
load assigns `trees` wholesale.**

### 2026-08-10 — Palette round 2: scope chips, model-off default, node deep links

Clément's follow-ups on the fresh Ctrl+K palette, plus a request relayed from
another instance (links were workspace-granular; it wanted `?w=…&node=…` and
`grep --link` so a hit line is a clickable answer). Shipped:

- **Scope chips** (replies / user msgs / thinking / system / names / models)
  under the palette input. Filtering is SERVER-side (`scopes=` on /api/search,
  mirrored in search-scan.ts) rather than client-side so totals and the
  max_hits cap reflect the filter — a client-side filter over a truncated
  result set silently under-reports. `model` starts disabled (rarely what
  you're hunting); persisted in localStorage as the DISABLED set, so a scope
  added later defaults ON for existing users.
- **Node deep links.** `?w=<id>&panel=<p>&node=<n>` reveals that node on open —
  the palette's own jump-and-reveal machinery (`revealNodeIn`, shared), driven
  by an apply-once-per-(ws,node) effect so cycling away later never yanks you
  back. A palette jump now also WRITES the node into the URL, so the address
  bar after a jump IS the shareable link; `setWsUrl` drops the params whenever
  `w` changes (a stale node param would make the URL claim a jump into the
  wrong workspace). `tinkpg grep --link` / `tinkpg node --link` print these
  URLs per hit — "which panel has X" is now a URL you click, not a table.

### 2026-08-10 — `tinkpg url`: the CLI knew the server's address and never told anyone

Clément asked "which panel has this node, and link me to it". `tinkpg grep`
answered the first half in one command — workspace · panel · thread · node id,
exactly the right locator. The second half took `ps aux | grep tinkerscope`,
picking between two running instances by eye, and a `curl /api/workspaces` to
confirm which one held the workspaces just read.

That is absurd on inspection: `_base_url()` had already resolved and cached the
answer before the grep ran. Discovery is *so* good at being invisible that the
URL became unobtainable — every command uses it, no command prints it. The gap
only shows up on the one task where the URL is the deliverable rather than
plumbing, which is why weeks of use didn't surface it.

`tinkpg url` prints it. Bare on stdout so `open $(tinkpg url)` works, with the
resolved workspace name pushed to **stderr** — that split is the whole design,
and `test_url_keeps_the_workspace_name_off_stdout` guards it. `url <ws>` /
`url --live` emit a `?w=<full-id>` link; `url --json` adds the discovered `pid`
and `scan_roots`, which is the "which of my two servers is this?" question
`ps` can't answer. `state`'s header now carries the base URL, and its
open-workspace block a ready-made link — including under `--no-link`, which
skips the workspaces *fetch* (so the name is unresolved) but still has the
pushed id, and is precisely when you want the link.

Not built here: panel/node anchors in the URL. At the time `+page.svelte` read
only `w`/`c`/`open`, so a `?w=` link landed on the workspace at whatever branch
was selected. That gap was closed independently in the same session by the
search-palette work above (`?w=…&panel=…&node=…`, plus `grep --link`), which
`url` composes with rather than duplicates: `url` answers "where is the server",
the palette links answer "where is the turn".

Same session, same cause, second fix: `samples --ws <id>` died with
`No such option: --ws` while `grep`/`node`/`threads` had taken `--ws` all along.
The split is defensible — commands whose positional slot is already spent (a
pattern, a node id) *must* use an option; `ws`/`samples` take the workspace
positionally because it's the subject. Defensible and still wrong to trip over
mid-session, so `ws`/`samples`/`url` now accept either. Two *different* values
error (`_one_selector`) instead of silently preferring one: that's a typo, not a
preference. The same value twice is fine.

Skill doc: `node <id>`'s one-liner promised "locate a bare node id (no ws/panel
needed)", which reads as a unique key. A tree cloned into another panel keeps its
ids, so one id routinely names the same turn in N panels — the docstring in
`workspace_store.py` had this right all along, only the skill blurb was loose.
An audit of the live store (39 workspaces, 313 shared ids) found 0 semantically
divergent pairs, so the invariant the blob writer leans on holds; noting it here
rather than in the skill, where the number would just be noise.

### 2026-08-10 — Panel ids become monotonic; closing a column becomes recoverable

Clément asked for the Copy-node-id button to hand out panel + node as one string.
Pinning down the format turned up two things that had to be fixed first.

**Why the panel belongs in the handle.** A bare node id is ambiguous: add-model's
`duplicateTo` clones a panel's tree keeping its ids, so one id names the same turn
in N panels (313 such ids across the live store, 0 divergent). `tinkpg node`
prints every copy; the WRITE commands (`continue --node`, `samples --node`) refuse
until you add `--panel` by hand. `<panel>:<node>` removes that step, and says
"this column's turn" — bound to that model.

**Why the ids weren't safe to embed.** `nextPanelId` minted by GAP-FILLING —
reserved `'compare'` for slot 1, then the lowest free `p-N`. Close a column, add
one, and its id came back attached to a different model. A `panel:node` handle
would silently re-point; a quoted turn would become unquotable. Reserved names
are gone (Clément: "drop compare and primary, just name it with ids, no need for
weird special cases") and every mint is a fresh number from a persisted per-
workspace `panel_seq`. `primary`/`compare` remain VALID ids forever — every
workspace saved before this uses them, as does the legacy `{tree, compare_tree}`
migration — they're simply never minted again. `DEFAULT_PANEL_ID` (state.py) and
`FIRST_PANEL_ID` (panel-id.ts) are the one-per-side source of truth, and they must
agree: a fresh bus and a fresh workspace have to name the same panel.

Back-compat for a workspace with no `panel_seq`: `highestPanelSeq` seeds the
counter from the highest `p-N` in trees + layout + **seen_panels**. That third
one is load-bearing and is why `dropPanelUi` no longer prunes `seenPanels` — a
CLOSED panel is absent from trees and layout, so without the ledger the seed
could re-issue its number, which is the exact bug. Keeping a dead id costs
nothing: its only reader gates first-sight `sendTargets` defaulting, and a
monotonic id is never seen twice.

**The regression that ordering revealed.** `restore_trash` refused when the
panel was gone: *"re-add a panel with that id before restoring into it."* That
worked ONLY because gap-filling could re-mint the id — monotonic ids would have
turned a recoverable delete into an unrecoverable one. So restore now rebuilds
the column itself, and `_record_trash` journals the panel's LAYOUT ROW alongside
its nodes (a tree with no layout row is a column the browser never renders), so
it comes back bound to the model it had. Entries written before that field
restore into an unbound panel for the human to re-bind. Worth noting the journal
half was already correct and tested — `test_dropping_a_panel_journals_its_whole
_tree` — while the restore half had NO test and no live exercise (the only
journal on this box holds 4 entries, all `kind: "nodes"`). Verified-looking
safety net, untested recovery path.

Closing a panel is a soft delete either way, with limits worth knowing: the
journal expires at 90 days and is byte-capped at 32 MB oldest-first. Folding a
panel keeps its tree (`reducePanel` only touches `reducedPanels`) — only
`removePanel` drops one, so the comment claiming "reduce/remove" was wrong.

Test fallout was all contract, no bugs: 10 tests asserted `'primary'` as the
DEFAULT panel id. Tests that merely USE `'primary'` as a label still pass
untouched, which is the point of keeping it valid.

### 2026-08-10 — Review of the panel-id change: two ways the guarantee didn't hold

A Fable instance reviewed the branch above and found the headline claim ("a panel
id is never reused within a workspace") false in two places, both in the new code.
Recording them because each is a case of a *safety property* that looked verified
while resting on something else.

**The rebuild condition was too narrow.** `restore_trash` gated the column rebuild
on `panel not in trees`. But the tree and the layout row can drift: a stale tab —
the normal condition, since tinkpg live-drives an OPEN browser — ships its whole
pre-restore `panels` list, wiping the row, while the partial tree upsert leaves the
tree standing. A lost row is not journaled (`_record_trash` diffs trees only). So
after a clobber, re-running restore saw the nodes already present, returned
`recreated_panel=False`, never re-added the row, and skipped even the reload
warning (gated on `n > 0`) — a stored tree that no layout renders, with the manual
re-add escape hatch removed by this very branch. Now keyed on tree and row
separately, so restore is re-runnable. The nodes-restore case self-heals (a
re-delete gets re-journaled); the panel case had no such loop.

**`panel_seq` could be reset to 0 by any writer that didn't know about it.** Three
of them: PUT /tree and POST create (both default `int = 0`, assigned outright) and
pack apply (export ships the field, import dropped it). PATCH was already correct
via `exclude_unset=True` — that asymmetry was the tell. Client-side
`conv.panel_seq ?? highestPanelSeq(conv)` then took the stored 0 as authoritative,
because 0 isn't nullish. The invariant survived anyway, via `seen_panels` — but
"correct only through the second mechanism" is not a guarantee, and the two fail
TOGETHER: an old tab both prunes seen-on-close and omits the counter. Fixed by
making the field's semantics match its meaning rather than adding a null check:
`panel_seq` merges as `max(stored, incoming)` (monotone), `seen_panels` as an
append-only union (an id is never legitimately un-seen now that it's a ledger).
A writer that doesn't know about either field can no longer lose it.

Also from the same review: the `restore into an unbound panel` fallback is
unkeepable as written — `#loadTrees`' phantom filter drops `run_id == null` panels
on load, so the column would vanish before the human saw it. Surfaced as
`unbound_panel` and said out loud in the CLI instead of touching that filter,
which guards a resurrection bug. And a genuine hole left OPEN: `cli._panel_id` is
POSITIONAL, so `open`/`chat`/`compare` can reissue a retired `p-1` into whatever
workspace is on the bus. Documented in three places (its docstring, API_CONTRACT,
the cli skill) rather than fixed; the fix is to mint above the open workspace's
counter, one GET.

Two process notes worth keeping. The `ConvFields` "nit" (add `panel_seq` to the
documented save shape) was a live bug: adding the field failed the typecheck on
save-plan.test.ts's fixture, which is the argument for it — a body built from that
type would silently drop the counter. And the two Playwright smokes asserting the
old contract had to be found by reading, since they aren't CI; `scripts/smoke.sh`
is this repo's stated verification surface, so a red smoke is a future session's
hour, not a warning.

### 2026-08-10 — The CLI stops reissuing retired panel ids (and the argument for leaving it)

The last hole in "a panel id is never reused within a workspace" was the CLI's own
front door. `_panel_id(i)` named panels by POSITION, and `open`/`chat`/`compare`
push a full `panels` replace onto the state bus which the browser adopts into
whatever workspace is open. Fire `chat` at a workspace whose `p-1` was closed and a
NEW column bound to a DIFFERENT model answers to `p-1`. Two things key on panel id
now: a `<panel>:<node>` handle, and `restore_trash` — which would splice the retired
column's branches into the new one, attributing one model's turns to another. That
is the provenance failure the cli skill warns readers about, manufactured by the
recovery code rather than found in the wild.

I first shipped this as a *documented exception*, on the reasoning that
`open`/`chat`/`compare` already replace the layout, so a destructive act was
already expected. That reasoning is wrong and worth writing down because it is the
kind that reads as sober: losing a layout is recoverable and announced, whereas
silently reissuing the key a recovery mechanism trusts makes that mechanism return
wrong data. Two different harms, and "already destructive" doesn't cover the second.
The tell was in the same paragraph — it named the fix as one GET and then declined
to do it. Clément called it: *"sounds like you're trying to convince yourself that
something horrible is actually fine."*

The fix is NOT simply "mint above the counter", which was my first attempt and
regressed something real: minting fresh on every fire makes repeated `tinkpg chat`
abandon a column per fire (each layout replace drops the previous tree to the
journal) instead of reusing one. `_layout_panel_ids(n)` instead REUSES the ids
already on screen and mints only for extra positions. A live id is by construction
not retired, so reuse is both the safe answer and the one that preserves behavior.
Fresh ids come from `max(panel_seq, every visible p-N)` — both bounds, since a
counter some writer zeroed would otherwise hand back live ids — and the bump is
PATCHed back so the claim survives with no browser listening. `compare` resolves
its runs BEFORE minting, so a bad run argument doesn't burn panel numbers.
`probe` keeps a literal `"p-1"`: it commits nothing, so it must mint nothing.

### 2026-08-10 — The add-panel button was gated on the wrong two catalogs

A collaborator on the PyPI build reported being unable to add a third panel;
adding one OpenRouter model "unlocked" it. First theory (mine) was the old
`MAX_PANELS = 6`, but that went in 2665af5 (2026-07-09) and every tag,
including PyPI 1.0.0, contains the removal. The real gate was:

    disabled={modelCatalog.runs.length + modelCatalog.openrouterModels.length < 1}

— the same condition also early-returned inside `addPanel`. Those two lists are
`GET /api/models` (**discovered run dirs** — a folder with `config.json` +
`checkpoints.jsonl`) and `GET /api/openrouter-models`. Neither covers the ◆
base models / ◇ loose checkpoints picked through the Tinker picker (localStorage
recents) or **pack-injected models**, which ride a third list entirely
(`/api/tinker-models` ← `pack_models_store.tinker_model_entries()`,
`routes/models.py:96`). So a pack consumer with no local run dirs has both
counted lists at zero, the button greyed forever, however many models they have
actually wired up — and the "2" was just however many panels their pack had
already set up. Adding an OpenRouter model takes the count to 1 and unlocks it.

Gate deleted outright per Clément: adding a panel must not depend on what models
happen to be available. The one thing that had to come with it is the seed —
`addPanel` picked the new panel's model from `runs` only, so with no free run it
minted `run_id: null`, and `#loadTrees` (`workspaces.svelte.ts:922`) drops
null-run_id panels as phantoms on the next open. It now falls back to the FIRST
panel's selection, which may be a `base:`/`ckpt:`/`openrouter:` sentinel — so the
panel survives a reload in exactly the case that was broken.

Verified against an isolated instance whose scan root has no run dirs and whose
OpenRouter list is empty (`scripts/dev-isolated.sh --fresh /var/tmp/tscope-noruns`):
button enabled at runs=0/openrouter=0, two successive adds land. With a TRULY
empty catalog a reload still collapses to one panel — correct, that's the phantom
heal doing its job when there is no model anywhere; the reported case has models,
so panel 1 carries a real sentinel and the inherited one persists.

Moral: a feature gated on "do we have any models" has to ask the question over
every catalog that can supply one. There are three here, and the count knew
about two.

### 2026-08-10 — Renaming the default panel made legacy workspaces open blank

Review pass over the panel-ids branch (PR #1) before merge. Ran the browser
smokes, which that branch's author had run only for the three files they had
edited. Seven of 32 failed; three of those reproduced on a second run:
`browser_highlight_master`, `browser_token_logprobs`, `browser_token_overlay`,
all timing out at the same point — the seeded turns never rendered.
`scripts/smoke.sh --baseline main browser_highlight_master` passed, so the
branch owned it.

Cause: those smokes seed a workspace with `trees` and **no `panels`**, which is
the shape of every workspace saved before per-workspace layout persistence.
`#loadTrees` reads a layout-less body as "keep whatever panels are shown", and
that only ever worked because the shown default first panel and the legacy tree
key were BOTH called `primary`. The branch renamed the default to `p-1`
(`DEFAULT_PANEL_ID` / `FIRST_PANEL_ID`, a deliberate part of dropping reserved
names), so no shown id matched any tree key: the trees loaded, no column
rendered one, and the workspace opened empty with its turns sitting on disk and
unreachable from the UI. Not smoke-only — **2 of the 42 workspaces in the live
`:8767` state dir are exactly that shape**.

Fixed by `panel-id.ts::legacyLayout`: with no stored layout the TREE KEYS are
the panel set, so adopt them and inherit models positionally from what's shown
(a legacy body records no model per panel). A layout that already covers every
tree is returned unchanged, extra blank panels included — that was the prior
behavior and narrowing it is not this function's business.

The general shape, which is the part worth keeping: an id that two subsystems
agree on **by coincidence of naming** reads exactly like an id they agree on by
construction, right up until one of them renames. Grepping for the literal
`'primary'` finds the first kind; only running the thing finds the second.

Also fixed in the same pass, each with a regression test:

- **`patch_meta` bypassed the monotone/union merges** that PUT and POST had just
  been given. Key-presence gating stops a writer that never heard of
  `panel_seq`/`seen_panels`, but not one holding a STALER value — and a
  layout-only save (every model change) is a PATCH, sent by each tab from the
  snapshot it loaded.
- **`unbound_panel` keyed on a proxy** ("the entry predates layout journaling")
  rather than the fact the caller needs. A journaled row can itself bind no
  model — add a panel, send it a branch, close it before picking a model — and
  the browser's phantom filter drops that row on load just the same, with no
  warning. Now keyed on the restored row's `run_id`.
- **A restored column was appended, not re-inserted at its old position.**
  Journaled subtree roots record their sibling index for exactly this reason;
  the layout row now records `layout_index`. Panel order is display order AND
  what `tinkpg samples` reads with no `--panel`.
- **`samples`' default panel followed `trees` key order** once the privileged
  name `primary` stopped existing — i.e. however the file was last written. Now
  `_panels_in_display_order` asks the LAYOUT, so the CLI answers with the column
  the human is looking at.
- **`panel_seq` was dropped on the browser-side pack install** (`pack-install.ts`
  / `api-static.ts`), the mirror of a `pack.py` fix the branch had already made.
- **`ws.create()` carried the previous workspace's counter** into a new one.
- `browser_static_site` selected the copy-node-id button by TOOLTIP TEXT, which
  the branch reworded — a third consumer beyond the two smokes it updated.
  Re-pointed at the stable `data-testid`.

`docs/API_CONTRACT.md`'s two trash rows were stale on the branch: it added
`recreated_panel` / `unbound_panel` to the restore response and `layout` to the
journal entry without documenting either.

Two follow-ons from the same review pass, neither caused by the branch:

- **The `?node=` double-reveal guard was dead.** Its two sites built the key
  independently and with DIFFERENT separators — a NUL byte in the apply-once
  effect, a plain space in `openSearchResult`'s pre-mark — so the pre-mark never
  matched and a palette pick revealed twice, the exact thing the comment says it
  prevents. Both now call one `nodeLinkKey`; the separator is a space (ids are
  `[A-Za-z0-9_-]`, and it keeps the file greppable). Pre-existing on main;
  flagged by the previous session and deliberately left out of its PR.
  WARNING: while making that edit the **`Edit` tool wrote a literal NUL where a
  space was typed** — it swaps between `\uXXXX` escapes and the characters they
  denote — re-introducing the byte `fd39382` had just removed. Caught by a byte
  count, not by eye. After editing this file, count the NUL bytes in it with
  python rather than trusting a visual diff.

- **`browser_kbnav` inherited its model from the human's live browser.** It
  seeded its panel with `run_id` copied from the state bus, and
  `dev-isolated.sh` snapshots the real state dir — so when the `:8767` session
  had nothing selected, the seeded panel was unbound, `#loadTrees` dropped it as
  a phantom, and the composer stayed disabled. The smoke then failed 30 s later
  on a click that looks unrelated to models at all. It passed in one sweep and
  failed in the next with no code change between, and `--baseline main` failed
  identically. Now falls back to any discovered run. The general form: a smoke
  that reads seed data from the bus is reading someone else's session.

### 2026-08-10 — Add a checkpoint nobody here has heard of, and give it a name

Clément: a checkpoint a collaborator sends you is on THEIR account, so it is in no
local list — not a discovered run, not your sweep. Until now the only ways in were
a share pack and the CLI. His shape for it: the picker's search box IS the entry
point, and "No matches" becomes an add row that says whether the path is real.

**Can we even check a path?** Yes, with your own key — there is no unauthenticated
tinker endpoint. `resolve_base_model` already did it (create a sampling client, call
`get_base_model_async`), but it collapses every failure to `None`, and the picker
needs the REASON. Hence `probe_sampler_path` + `_tinker_detail`, which pulls the
server's own `detail` out of the exception. Measured against the real API:

| case | result | time |
|---|---|---|
| real path | `openai/gpt-oss-120b` | 269 ms median warm (801 ms first call in a process) |
| valid shape, unknown id | 404 `Model not found.` | 228 ms |
| malformed | 400, and the message names the expected form | 198 ms |
| same path again | cached | 0 ms |

Two error codes, two different sentences for the user. Errors cost the same as a
success, so a typo does not make you wait.

**The derived label is not a name.** I claimed account-sweep checkpoints "already
carry a derived label" so they could skip the prompt. Clément asked what the label
looks like; printing ten real ones killed my own argument — seven read
`<8 hex> · final · <date>`, differing only in hex. The label exists and identifies
nothing. So the prompt fires for ANY checkpoint the registry has no name for, which
was Clément's initial preference; the mismatch cost one message to surface.

Names go SERVER-side into `pack_models_store` (his call over the browser-local ◇/◆
recents): survives a restart, visible in every tab and to `tinkpg`, and travels in
`pack export`. That forced a merge-order change in `GET /api/tinker-models` — a
registry entry used to be appended only when its id was ABSENT from the sweep, so a
name for a swept checkpoint was silently dropped. A stored label now OVERRIDES the
derived one (still one row per id) and sets `named:true`, which is what the UI reads
to decide whether to offer the prompt.

**The bug the smoke found.** First draft of `browser_tinker_custom_ckpt.py` PASTED
the path and passed; the real smoke TYPES it, and the save POST then left the browser
and never came back. Cause: the probe ran in a bare `$effect` on the query, so typing
a 90-char path fired a live tinker call per PREFIX — ~55 of them, serialized behind
the sampler client lock, exhausting the browser's ~6 connections per origin. Every
later request queued behind the pile, including the save. Debounced 350 ms. Worth
keeping in mind generally: an `$effect` on a text query that calls a REMOTE service
is a per-keystroke fan-out unless you debounce it, and the symptom shows up in an
UNRELATED request that merely happens to be next in line.

Also fixed: `pickTinkerModel` routed any id absent from the catalog into its BASE
branch, so a pasted path would have been stored under `base:` and sampled as a base
model name. It now treats a `tinker://` id as a checkpoint.

Smoke caveat, stated rather than papered over: the green row needs a path that is
real AND unlisted — a foreign one. This box has none, and tinker rejects every cheap
fake (trailing slash → 400, trailing spaces → "Weights not found"), so the browser
half of green is unpinnable here; the smoke asserts the probe DATA instead and pins
the three-state rendering through the red path. The smoke also picks an un-named
checkpoint each run, because naming is a one-way write with no delete route.

### 2026-08-10 — The foreign checkpoint arrived, so the green row is pinned now

Clément sent a path from another account and asked whether it worked. It did —
available, base `openai/gpt-oss-20b`, and absent from our 77-checkpoint sweep. That
is precisely the shape the previous entry said this box could not produce, so the
smoke's green section stopped being a stated gap and became a test.

`browser_tinker_custom_ckpt.py` now types that path, waits for the row to settle
green, checks it names the base model, clicks it, and asserts the checkpoint lands
in `GET /api/tinker-models` under the name typed into the prompt. The path lives in
`FOREIGN`, overridable via `TSCOPE_SMOKE_FOREIGN_CKPT`.

Two ways the section steps over itself instead of failing, because neither would be
our regression: the foreign checkpoint stops being servable (someone else's account,
someone else's retention), or a previous run on the same state dir already added it
— naming is a one-way write and there is **no un-name route**, so the path then
matches a list row and the add row is correctly gone. Fresh state dir to re-exercise
it. That missing delete is the obvious next small thing: name something wrong today
and you cannot fix it from the UI.

### 2026-08-10 — A dialog holds controls, not an explanation

The name-a-checkpoint dialog shipped with a four-line paragraph on what a derived
label is and where the name is stored, a two-row `path`/`base` metadata block, and a
"Name" label above an input that already had a placeholder — to type a name into a
box. Clément: "the string in the pop up is way too complicated, stop being so
exhaustive with useless stuff in the UI this is making me crazy." Cut to the path,
the input, Save, Skip (`bc3e40d`), and the same content in the `?` modal went from
two paragraphs to one sentence.

The repo already had this rule one level down — tooltips are ONE short line,
mechanism goes in the `?` modal — and I did not generalise it upward on my own, so
it is now written out for dialogs too. The mechanism behind the mistake is worth
naming: everything about WHY naming a checkpoint matters was live in my context from
having just built it, and I emitted it into the nearest surface rather than the right
one. Log entries and guide prose are the right one. The check now in `CLAUDE.md`:
**is the user's next action any different because they read this sentence?**

Second correction in the same exchange, and the more mechanical one: the `CLAUDE.md`
edit that added the new rule **overwrote** the headline of the next bullet
(`- **Adding a scope/filter? …**`), orphaning its body under my rule. The `Edit` call
had that line in `old_string` and dropped it from `new_string` — an insert written as
a replacement. Fixed in the follow-up. When adding a list item, anchor on the END of
the preceding item and re-emit nothing else; if a neighbouring bullet appears in
`old_string`, it must appear verbatim in `new_string`.

### 2026-08-12 — Server-authoritative trees, P1 server half: `tree_ops.py` + `/ops` + `rev`

`docs/HANDOFF_SERVER_AUTHORITY.md` P1, server side (the browser mirror cutover is a
parallel piece of work). The branch tree stops being opaque to the server: it owns
it. Mutations arrive as ops on `POST /api/workspaces/{id}/ops`, apply under the
workspaces flock, bump a per-workspace `rev`, and go out as one bus `ops` event.

Three things worth writing down beyond the design doc, because each is a decision
the doc didn't make:

**1. `rev` bumps in `_persist`, not in the route.** Same reasoning that put the
trash journal there: `_persist` is the single choke point for every write channel.
The handoff's own §2d.1 correction found `POST /api/pack/apply` — a runtime,
live-mirror-attached workspace writer nobody had counted. Bumping in the `/ops`
handler would have left pack apply, trash restore and the PUT path moving
workspaces behind the mirrors' backs. `test_rev_is_monotonic_across_every_write_channel`
walks all five channels; disabling the one line in `_persist` fails it (verified,
not assumed). `upsert` needed an explicit `entry["rev"] = _rev_of(existing)` — it
rebuilds its body from kwargs, so without that a pack install restarts the counter
and every mirror sees its workspace jump BACKWARDS.

**2. `select` on a node that already exists must NOT re-assert.** Found by a test I
wrote expecting it to pass. The rule for `add_nodes {select: true}` is "write
`selected[parent] = id` unless an earlier node in this batch claimed that parent" —
one rule that yields chain semantics (each node its own parent ⇒ every step
selected) and fold semantics (a fan shares a parent ⇒ the FIRST sibling selected).
Applied naively it also re-selects on a REPLAY, and a replay is exactly what a
retried batch is: server applied the fold, client never saw the 200, user cycled to
sample 3, client retries — and the view jumps back to sample 1. So a skipped node
CLAIMS its parent (otherwise the retry promotes sample 2, since a1 is skipped and a2
then looks like the first of the fan) but does not WRITE. Pinned by
`test_retrying_a_partly_applied_fold_keeps_the_FIRST_sample_selected`. The generalisation:
"idempotent" has to mean idempotent against a tree the USER moved on from, not just
against the tree the batch first landed on.

**3. The phantom-panel heal is narrower on the server than it was in the browser.**
The browser dropped every `run_id: null` panel row on load AND deleted the trees of
panels not in the cleaned layout. Relocating that verbatim would have made the
server destroy nodes: a send-branch-to-panel target and a trash-restored column are
both legitimately unbound with real content in them (`restore_trash` even returns an
`unbound_panel` flag *because* the browser filter used to eat them), and dropping a
tree server-side mass-journals to the trash on a legacy workspace's first op. So:
drop a blank row only when its panel holds no tree, never drop a tree, never empty a
non-empty layout. The browser must stop dropping blank-rows-with-trees to match, or
the two disagree after every `set_meta` round trip.

Also: `copy_subtree {node_id}` from §4.1 shipped as `copy_tree {from_panel,
to_panel}`. `duplicateTo` (the add-panel clone) is its only grounded call site and
it copies everything; a partial keep-ids splice has no user today and would have to
answer what happens when the destination already holds those ids.

**Call-site → op mapping** (§4.1 asks P1 to ship one; the browser handlers are in
`lib/branch-ops.svelte.ts` + `lib/workspaces.svelte.ts` + `lib/chat.svelte.ts`):

| Browser call site | Op |
|---|---|
| `sendMessage` / composer (`appendUserTurn`) | `add_nodes` (1 user node, `select`) |
| `chat.svelte.ts` fold (`foldAssistant`, all n) | `add_nodes` (n assistant nodes, `select` ⇒ first) |
| `applyEdit` / `applyEditAll` (`editUserFork`, `editAssistant`) | `add_nodes` (1 node, `select`) |
| edit-fork-COPY (`editUserForkCopy`, `graftDownstream`) | `add_nodes` (a chain, `select` ⇒ every step) |
| `regenerate` (append) | `add_nodes` for the fold; nothing extra up front |
| `regenerate(replace=true)` (`regenReplace`) | `delete` (the active branch) then the fold's `add_nodes` |
| `deleteMessage` / `deleteSample` / `deleteMessageAll` | `delete` (one per pruned root; `deleteSiblings` = N of them) |
| `discardOtherSamples` | `delete` × the discarded siblings, one batch |
| `cycleBranch` / `selectSample` / `selectPathTo` (search jump) | `select` (one per ancestor for the jump) |
| `switchThread` | `select` at `__root__`, one per panel holding that thread |
| `sendBranchToPanel` | reconcile ops (`select`s + one `add_nodes` chain) — NOT `replace_tree`: §4.1's row was stale grounding (`treeFromMessages` + replace); the shipped code is an additive `reconcileExternal` GRAFT into the dest tree, deliberately non-clobbering, so a replace would eat the dest panel's other threads (caught at integration, 2026-08-12) |
| `duplicateTo` (add-panel clone, keeps ids) | `copy_tree` |
| `freshTree` / `resetActive` / `#freshTrees` | `replace_tree` (a fresh empty tree per panel) |
| `dropTree` / panel removal | `replace_tree {tree: null}` |
| `applyLayout` / `setPanelModel` / rename / system prompt / send-target + fold toggles | `set_meta` |
| `continueSample` | `select` (the continued sibling) before the fire |
| `#onExternalDone` / `#afterLoad` / `reconcileOnReconnect` (CLI/foreign echo folds) | `reconcileExternal`'s returned ops (`select`s + `add_nodes` tail) |

Undo (`lib/undo.ts`) is the one call site with no single op: it restores a whole
pre-op tree ref, so it is a `replace_tree` — which is correct but coarse (it re-ships
the panel). Worth revisiting in P2 if the payloads bite; the durable half of undo is
the server trash journal either way, and that path is unchanged.

### 2026-08-12 — Correction: `add_nodes {select}` must re-assert, or two tabs strand

Same day, correcting the entry above. That entry argued `select` should write the
selection only for a node the batch actually ADDED, so a retried fold couldn't yank
the view back from the sample the user had cycled to. It reads better and it is
wrong.

Conditioning the write on "we added it" makes the outcome depend on whether the node
was new **in that mirror** — i.e. on local optimistic state — rather than on the
rev-ordered op sequence, which is exactly what confluence requires. Two tabs folding
under the same user node:

    server:   A(add a1, select) at rev+1, B(add b1, select) at rev+2  → selected = b1
    tab A:    local A → echo A (a1 exists, no write) → echo B (b1 new, select) → b1 ✓
    tab B:    local B → echo A (a1 new, select a1)  → echo B (b1 EXISTS, no write) → a1 ✗

Tab B sits on a1 forever: the revs are contiguous, so the gap-refetch that would
repair it never fires. That is the same silent-divergence shape §4.2 documented for
skip-own-batches, arrived at from a different direction. Measured with a 20-line
script before changing anything, then kept as
`test_concurrent_folds_under_one_parent_converge`.

So the rule is back to: the first node to claim a (panel, parent) in a batch writes
the selection, minted-here or not. The retry-steals-the-selection race is real and
stays — it is transient, visible, and the design already accepts the same class of
loss for a delete echo landing after a local add. A visible transient race beats a
silent permanent divergence.

**And the same trace, compared on WHOLE trees rather than on `selected`, found a
second divergence the first probe missed**: sibling ORDER. Tab B holds `[b1]`,
appends the echoed `a1` to get `[b1, a1]`, and its own echo can't move `b1` because
an existing node was simply skipped — server has `[a1, b1]`. §4.1's table calls this
one "order = arrival order; harmless", and it isn't quite: with no `selected` entry
the render falls back to the LAST child, so two tabs that disagree on order
eventually disagree on the active path. Fix is one rule — **a node that already
exists is re-APPENDED on its own echo** — which makes replay reproduce rev order
exactly, and leaves a full batch replay a no-op (moving each of `[a1,a2,a3]` to the
end in turn lands them back in the same order). The browser mirror needs the same
rule in `applyTreeOp`, or the engines disagree on order instead of on selection.

The lesson about the first probe is worth as much as the fix: I asserted on the ONE
field I had reasoned about, it went green, and a second bug was sitting in the same
trace. Compare whole structures when testing convergence — the whole point is that
you don't know which field is wrong.

The generalisable bit, since I got this wrong in the confident direction: "is this
op idempotent?" is the wrong question. The question is **"is the result a function
of the op sequence alone?"** An op can be perfectly idempotent against the tree it
first landed on and still diverge, because a mirror applies its own op EARLY and
then replays the canonical order over the top. Anything that branches on tree state
the local apply already changed is suspect. `changed`-by-value (introduced in the
same commit) is what keeps the re-assert cheap: a replayed batch that nets to no
difference still writes nothing and broadcasts nothing.

### 2026-08-12 — A pre-commit step for raw NUL bytes, written by the bug it fixes

`fd39382` (2026-08-10) escaped a raw NUL in `+page.svelte`: it was a `.join()`
separator typed straight into the source, harmless at runtime, and it made the
file BINARY to plain grep — so a grep-first question about the repo's biggest
file got a confident, silent "not there". Two days later the same byte in
`SearchPalette.svelte` did it again: an audit grepped the file for `scope`, got
zero matches, and nearly concluded the palette's scope chips had never shipped
(they had — `-a` shows ten hits, and `git log` names the commit that added them).
One instance of this is a typo; two in three days on files nobody suspects is a
class, and the failure is invisible in the direction that matters — an empty grep
reads as an answer.

So it's a hook step now: `.githooks/fix-nul-bytes.py`, first in `pre-commit`
(before the web build, so vite compiles the fixed file). It reads each staged
file's INDEX blob — what would actually be committed, not the worktree — and for
a JS-family or Python file replaces the byte with the escape and re-stages;
anything else (JSON/YAML/CSS/markdown, where there is no blind-safe spelling) or
a file whose worktree copy differs from its staged one aborts the commit with the
line numbers. Refusing on the partial-staging case is the point of reading the
index: fixing the worktree there would either miss what is being committed or
sweep unstaged edits into it.

Two things worth knowing if you edit that script. First, it never spells either
escape sequence literally — both are built from `chr(92)` — because an assistant
writing the six characters of a unicode escape into a file emits **the NUL
itself**: the first draft of this script acquired three of them that way, which
is also why grep went quiet on it before it had ever run. That's a strong hint
about where the two production instances came from. Second, the replacement is
textual, so it assumes the byte sits in an ordinary string literal (true in both
real cases); a NUL in Svelte markup or a Python `r''` string would change meaning
under it, and neither is a thing anyone writes.

A/B'd in a throwaway repo across six cases: `.ts` fixed in worktree AND index,
`.md` refused, partially-staged file refused with the unstaged edit intact, clean
and nonexistent paths silent, a real `.png` untouched. Then run against
`SearchPalette.svelte` itself — `grep -c scope` goes 0 → 10.
### 2026-08-12 — Review basket: batch-vs-per-op scope, and the payload that couldn't replay itself

Four findings from the adversarial review of the P1 integration branch, plus one the
fix for them surfaced. The through-line is a single question that turns out to be
the one worth asking about every op: **does applying a BATCH equal applying that
batch's own broadcast, one op at a time?** A mirror replays events individually, so
anything that says "no" diverges silently — contiguous revs, no gap, no repair path.

**1. The claim set was batch-scoped.** `_Ctx.selected_written` lived on the batch,
while a mirror creates its claim set per `add_nodes` op. A legal batch of two
`add_nodes` under one parent left the server on the first node and every mirror on
the second. Latent — no shipped browser call site emits that shape today — but P2's
server folds and P3's CLI writers are natural multi-op emitters, and the wire
contract permits it. Now per-op. Both semantics survive the narrowing, because a
fold's fan is ONE op (first sibling wins) and a chain's nodes have distinct parents
(every step selected).

**2. Single-op fixture vectors cannot see this class of bug at all.** They pin one op
against one tree; op BOUNDARIES are exactly what they abstract away. So the vector
format grew a batch form (`ops: [...]`), and — more useful than any single vector —
the pytest harness now asserts the batch≡per-op-replay property for EVERY vector,
not just the batch ones.

**3. That harness assertion immediately failed on a vector I had written weeks of
confidence into.** The `add_nodes` broadcast was shipping stored nodes *with* their
`children`, so replaying the broadcast tripped our own "children are server-owned"
rejection. The op's documented wire shape has always been "TreeNode minus children";
the broadcast just didn't honor it. Two consequences avoided: an interpreter that
validates its input breaks, and one that doesn't adopts a child list assembled by
ops it may not have applied yet. **The general lesson: vectors exercise the INPUT
shape, mirrors consume the BROADCAST shape, and nothing was testing that the second
one is replayable.** That is now the harness's job.

**4. `replace_tree` never checked the node-id charset.** With a heavy field the
unsafe id reached `_write_blobs`, whose `_check_id` raised an uncaught ValueError —
a 500, which the client's retry policy treats as retriable and burns its attempts
on. Without one, the unsafe id PERSISTED into the light tree as a node whose blobs
can never be read back. `add_nodes` had the check; `replace_tree` was the door left
open.

**5. A replay never staged blobs for nodes that already existed.** The loss path is
real and permanent: a draft whose create POST fails, then materializes later
shipping an already-LIGHTENED tree (has_* flags, no inline data ⇒ no blobs written),
then the op replay finds every node present and skips it. Disk ends with flags
pointing at blobs that never existed — the inspector says "no token data", the chart
and loom paths are dead for those turns, forever. Two changes: the already-exists
branch stages blobs from the op's nodes, and the store writes blobs BEFORE the
no-change bail-out, so a retry is the repair path for dangling flags. Write-once
makes both free when the blob is already there.

Also this round: `DELETE /api/workspaces/{id}` now broadcasts `workspace_deleted
{workspace}`. It is its own named event rather than an `ops` entry because a
deletion has no rev to ride — the workspace the rev would belong to is gone. Before
this, a tab holding the deleted workspace learned nothing and its next refetch 404'd,
indistinguishable from server trouble.
---

### 2026-08-12 — Two lazy-default bugs found by writing the assertion, not by reading

The web ideas basket (icon consolidation, `FirstTokenChips`, per-row availability
reason, loose-ckpt base label, tooltip lint, token-hover dead spots). Four were
mechanical; the two that weren't are the same shape, and worth the entry.

**The hover dead spots were the zero-alpha skip, not the aligner.**
`ideas/token-hover-dead-spots.md` reported "hovering a word sometimes yields no
popover" and guessed at a null-mapped token or an inter-rect gap. Both wrong.
`TokenHeatOverlay.measure()` built a box only for a token that had a COLOR
(`!colors[i]?.length` ⇒ `continue`), and those boxes are also the hover
hit-test — while `surprisalAlpha` rounds its alpha to 0 for anything the model
gave p > 93.55%. So every word the model was confident about was hover-dead and
its less-predictable neighbours worked, which is exactly the reported pattern
("cigarette" in a smoking answer; "good" two words earlier). A box now marks
where a token IS; only an UNALIGNED token is skipped. `paint()` already no-ops on
empty bands, so painting is unchanged. The cost is that `measure()` now runs a
`Range` for the untinted share too — it was already O(tokens) and runs on layout
changes, not per mousemove (`paint()` iterates cached boxes, and an untinted one
costs a loop iteration with no `fillRect`).

Pinning it needed a fixture change, which is the reusable part: every token in
`browser_token_overlay`'s stream sat at p=.9 → alpha 0.01 → painted → boxed, so
the bug could not appear there. It takes p=.99 to reach alpha 0. A fixture whose
values all cluster on one side of a rounding threshold cannot fail the way the
product does.

**A lazy catalog reads as a confident default.** `loose-ckpt-base-label` looked
like pure plumbing: probe the path, show the base, gate the Thinking toggle on
that base's `supports_thinking`. The label half worked first try. The toggle half
did not, and the new smoke (`browser_ckpt_base_label`) is what said so: the
tinker catalog — the only place per-family `supports_thinking` lives — is loaded
LAZILY when the picker opens, so on a fresh page load it is empty, the lookup
returns undefined, and `?? true` (a back-compat default meaning "we don't know")
renders as "this model has a thinking toggle". A `base:` pick had the same latent
gap on reload; `ensureTinkerCatalog()` (called when any panel holds a `base:`/
`ckpt:` pick) closes both. Static mode already eager-loaded the catalog for
exactly this reason, one surface over — the comment at `+page.svelte`'s
`isStatic` load spelled it out and nobody generalised it.

Both bugs are the same failure to notice: an "unknown ⇒ assume the permissive
thing" default is invisible while the unknown is common, and no assertion in the
repo distinguished "we checked and it's true" from "we never looked".

**Also**: `browser_model_availability` (unclassified in `smoke.sh`, i.e. never
run under the runner) already asserted the per-row availability tooltip names the
binding constraint — an assertion that could not pass against the generic copy
that shipped. It fails on `main` at exactly that line and passes with the reason
threaded through. Both it and `browser_ckpt_base_label` are now in DEFAULT, the
first with a note that a failure there can mean tinker changed what it serves.
