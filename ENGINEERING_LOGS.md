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
