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
