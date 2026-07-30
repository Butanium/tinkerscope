# tinkerscope — TODO / roadmap

Porting the nice UX from the old Streamlit `tinker-dashboard` onto tinkerscope's
streaming + auto-discovery + CLI-drive foundation. Order is rough priority.

## Next major: server-authoritative trees (design locked, not started)

- [ ] **Implement `docs/HANDOFF_SERVER_AUTHORITY.md`** (P1 op layer → P2
  server-authored folds → P3 workspace addressing). Twice-reviewed design,
  2026-07-21. Subsumes the CLI no-token-data bug, the n−1 lost CLI samples,
  and headless-CLI persistence — do NOT build the interim graft fix for those;
  the handoff's §6 marks it superseded. Clément's §9 answers pending (none
  block P1).

## Blocked on upstream

- [ ] **Restore n=1 token streaming for LoRA runs** once
  [tinker-feedback#125](https://github.com/thinking-machines-lab/tinker-feedback/issues/125)
  is fixed. tinker's oai `/completions` serves the **base** model for a LoRA
  `sampler_weights` path (`/chat/completions` + native `SamplingClient` apply the
  adapter), so the live single-sample path silently showed base output instead of the
  finetune. Workaround: `api/routes/chat.py` routes `run_id` n==1 through native
  `sample_stream` (whole sample, no token streaming) —
  `stream = (n == 1) and (req.run_id is None)`, marked `TODO(tinker-feedback#125)`.
  When fixed: drop `and req.run_id is None` and re-verify a LoRA run at n=1 streams the
  *finetune* (not base) via `tests/small-smokes/lora_completions_vs_chat_mwe.py`.

## Parked (validated approach, not shipped)

- [ ] **First-token vocab probe — probe an UNRECORDED token's position-0 prob.**
  The shipped first-token chart's "add a token" feature only surfaces tokens that
  are *already recorded* in this turn's samples (some sample's top-K or a sibling's
  sampled first token). If we later want to answer "what's P(` D`) at position 0
  when ` D` was never in any recorded top-K?", we need a model call. **The approach
  is validated and cheap to resurrect (~an afternoon):**
  - **Mechanism:** `SamplingClient.compute_logprobs_async(prompt + [candidate])[L]`
    where `L = model_input.length` (the generation prompt built by the run's
    renderer). Verified live against the Qwen base model — the probed lp matches the
    sampled call's stored top-K lp to **Δ=0.0000** (it's the same forward pass). Use
    `compute_logprobs` over the `sample_async(max_tokens=1)+prompt_logprobs` trick:
    both give identical values, but compute_logprobs skips generation (cheaper).
  - **Batching:** none available — `sample`/`compute_logprobs` each take a single
    `ModelInput`; there is no multi-prompt call. Fire M candidates as concurrent
    futures (`asyncio.gather`), one forward pass each (~L tokens).
  - **Vocab search:** decode the whole vocab once and cache (`tok.decode([i])` for
    every id — ~0.5s for Qwen's 248k, faithful display incl. byte-level markers),
    then rank exact→prefix→contains with the space-marker normalization that
    `web/src/lib/token-search.ts` already implements (mirror it in Python).
  - **Prototype:** `tests/small-smokes/parked_first_token_probe.py` — the live
    alignment check (compute_logprobs vs sample_async vs stored top-K). Only native
    tinker paths (run_id / base_model) can probe; OpenRouter / loose sampler_path
    can't reproduce position 0.

- [ ] **First-token units key by DISPLAY token, not tid.** `chartByFirstToken`
  buckets per source by the decoded display string, so distinct tids with an
  identical display form (byte-fallback `�`, tokenizer collisions) collapse
  last-wins and the shadowed token's mass silently folds into the rest. Rare, and
  keying by display is what makes color-sharing across panels work; disambiguating
  (e.g. suffix the tid on collision) is a design call for whoever hits it.

## Done

- [x] **Refactor: extract chat rendering into components.** `web/src/routes/+page.svelte`
  (was 2089 lines) → `lib/ChatMessage.svelte` (the message bubble + sample cards +
  toolbar), with shared helpers in `lib/render.ts` (markdown/katex/highlight),
  `lib/highlights.svelte.ts` (highlight config + reactive active-set),
  `lib/tooltip.svelte.ts` (tip action + store), and `lib/chat.css` (message styles,
  global, imported in `+layout.svelte`). `ViewMessage` type → `lib/types.ts`.
  Verified pixel-identical (before/after diff = only the new toolbar).
- [x] **Chat-thread actions (hover toolbar).** Edit (inline), Delete, Regenerate
  (truncate from that turn + re-fire), and **pick-a-sample** for n>1 ("Use this"
  replaces the auto-committed sample 0 with the chosen variant). All client-side
  (patch the shared transcript + reuse `fireChat`); no backend change. Verified
  end-to-end against a real checkpoint (edit/delete/pick/regenerate all persist to
  `/api/state`).

- [x] **Workspace branching (tree) — SHIPPED 2026-06-22.** Full Claude.ai-style
  branching: nothing is destroyed; regenerate / edit / n-samples become sibling
  branches you cycle through with ‹ k/N ›. **Subsumed the just-built pick-a-sample**
  (N samples → N cycle-able branches) **and the "persist named workspaces" item.**
  - Per-panel **tree** in a SEPARATE per-scan-root store (NOT in the SSE snapshot —
    diverged from the original handoff to respect `state.py`'s no-bloat principle).
    `messages`/`compare_messages` stay the linear ACTIVE PATH (sampler + CLI
    contract untouched; **CLI needed zero changes**).
  - n>1 → N sibling branches; regenerate on user+assistant; edit-user forks+regens;
    **shift+click edit** forks + copies the whole downstream workspace (no gen);
    edit-assistant = manual branch; **delete prunes the subtree**.
  - Named workspaces via a **dropdown** (create/switch/rename/delete), each
    carrying its own `system_prompt`.
  - Files: `web/src/lib/tree.ts` (pure, 27 unit tests via `node tree.test.ts`),
    `web/src/lib/workspaces.svelte.ts` (store: tree ownership, fold, persistence),
    `api/routes/workspaces.py` (flock'd CRUD + corrupt-file backup),
    `+page.svelte` / `ChatMessage.svelte` (render + ops), `chat.py` `client_token`.
  - Design + contract: `BRANCHING_DESIGN.md`. Verified: 33 pytest, 27 tree tests,
    `tests/small-smokes/browser_branching.py` (token-free fork/cycle/delete/edit-leak)
    + `branching_real_sample.py` (real n=1 fold / regen / n=3 multi-fold).
  - **Known v1 limitations:** two tabs editing the SAME workspace = last-writer-wins
    (flock prevents file corruption + sibling clobber, not same-id logical merge);
    per-workspace mode/model-selection not restored on switch (only trees +
    system_prompt); CLI external turns fold only sample 0 + lack reasoning.

## Next

- [x] **Chart: think/no-think split, a remembered view, and an isolated inspector
  — SHIPPED 2026-07-29** (`16bfedc`, `e66c9e0`; all three asked for by Clément in
  one session).
  - The thinking filter gained `split`: a bar per population over DISJOINT
    samples, each with its own 100% and its own `n`, composing with rules mode's
    `split` match scope (up to 4 bars per model). Source building moved out of
    the component into pure `chart.ts:buildChartSources`.
  - The whole modal view persists (`lib/chart-view.ts`): mode / match scope /
    thinking filter GLOBAL, everything question-specific PER WORKSPACE, 40-entry
    LRU in localStorage. Deliberately not a workspace field — a view preference
    has no business in the wire/disk contract or in share packs.
  - The inspector no longer resets under a live chart: bars are addressed by a
    stable ref (panel id + pop + sub) instead of their index in `data.bars`, and
    the per-sample thinking folds live in state instead of the `<details>` DOM.
    The index bug could silently re-point the inspector at a DIFFERENT panel's
    bucket, so this was a correctness fix, not just a UX one.
- [ ] **Open question from that session: should clicking a segment PIN the turn?**
  With the turn picker on `Latest`, starting a new batch makes the streaming
  pseudo-turn the latest, so the chart legitimately jumps turns and the inspected
  bucket disappears. Arguably correct — but if you are reading samples and someone
  (you, or the CLI) fires a draw, you lose your place. Cheap version: on
  `toggleInspect`, if `turnSel === 'last'`, freeze it to the current index (and
  say so in the picker). Wants Clément's read on which he'd rather have; nobody
  has reported it as a problem, so it is not obviously worth the extra mode.

- [x] **A human-facing guide, in two places — SHIPPED 2026-07-24** (asked for by
  Clément the same day). The `tinkerscope` skill is written for an AGENT driving
  the tool from the terminal; nothing explained the UI to a person.
  - `plugin/skills/guide/SKILL.md` (then at `.claude/skills/tinkerscope-guide/`)
    — the prose form: the four nouns
    (workspace / panel / branch / thread), a task-shaped "things people actually
    want to do" section, highlight rules, the chart's three modes, token probs,
    prefill, pins, packs, the full key/modifier tables, and a
    things-that-surprise-people list.
  - The in-app **`?` button** (sidebar icon row, next to Stop) → `HelpModal.svelte`,
    two tabs: Guide (condensed) + Keys (the lookup table). The keyboard row-nav
    and the Shift/Ctrl modifier axes were the undiscoverable parts; both are now
    written down. Smoke: `tests/small-smokes/browser_help_modal.py`.
  - Placement note: the `?` went in the sidebar's global icon row rather than
    beside the workspace dropdown as originally sketched — those three buttons
    (new/rename/delete) all ACT on the workspace, and help doesn't.
  - Keeping them honest is a working convention now (CLAUDE.md §Working
    conventions): UI behavior change ⇒ both twins update in the same commit.
- [x] **Isolated-instance snapshots moved to `/var/tmp` + self-reaping — FIXED
  2026-07-24.** `scripts/dev-isolated.sh` copies the whole state home (~1.6 GB)
  per launch and left every copy behind forever, in `/tmp` — which on this box is
  a **15 GB tmpfs, i.e. RAM**. Eleven snapshots over three days filled it; the
  resulting EDQUOT surfaced as *every* shell command exiting 1 with no output and
  cost about an hour misdiagnosed as a broken harness. Two changes: snapshots now
  live in `/var/tmp` (disk-backed, and the conventional home for large scratch
  that should outlive the run), and each launch reaps older snapshots not in use
  by a live process — identified via `/proc/<pid>/environ`, so a long-running
  instance's state is never deleted out from under it. Reaping is not optional
  housekeeping: this box is deliberately never rebooted, so the "cleared on
  reboot" contract that makes tmpfs self-healing never fires.
- [x] **Smoke runner scanned the wrong fixture root — FIXED 2026-07-24.**
  `scripts/smoke.sh` defaulted to `~/projects2/weird-personas` only, but four
  smokes in its own DEFAULT set (`browser_modals`, `browser_label_trunc`,
  `browser_label_diff`, `browser_fuzzy_search`) hard-code `ed_sheeran`, whose runs
  exist only under `~/projects2/negation_neglect/datasets/training_datasets`. They
  failed on a 15s `wait_for_function` timeout — a failure mode that reads exactly
  like a UI regression. The runner now scans BOTH roots by default
  (`SMOKE_SCAN_DIR` overrides, space-separated), which fixed three of the four.
  - `browser_modals` needed a real repair, not just the root: it waited for the
    literal string `ed_sheeran` in the body, which only appears when a run of
    that NAME is the *selected* model — ambient state it never set up, and
    unrelated to the four modals it actually tests. Now waits on `aside.sidebar`
    + `.model-dropdown-trigger`. Worth grepping for the same anti-pattern
    elsewhere: readiness waits keyed on data rather than structure.
  - `browser_workspace_url` is the suite's **load canary** (10s wait for
    `select.ws-select`): it failed once here purely because a forgotten dev
    instance on another port was competing for CPU, and passed alone. Read its
    failure as "find the stray process" before suspecting the URL sync.
- [x] **Workspace scoping on the state bus — SHIPPED (2026-07-24).** `panels` is
  workspace-scoped data that lived in the process-global `PlaygroundState`, so two
  browser tabs on two workspaces clobbered each other's STORED models with no user
  action (4 live workspaces corrupted; recovered from node blobs via
  `scripts/repair_panel_layouts.py`). Every bus message is stamped with the
  workspace it describes; clients adopt workspace-scoped fields only from their
  own, params stay global. `docs/API_CONTRACT.md` §"Workspace scoping on the state
  bus"; smoke `browser_two_tab_workspace.py` (verified to fail pre-fix).
- [x] **Storage v2 — SHIPPED (2026-07-13).** The browser-OOM-on-big-workspace fix:
  per-workspace files + write-once per-node blobs (token_logprobs/raw_meta,
  89.8% of the bytes), summaries-only list + fetch-on-open, dirty-panel partial
  saves, zero-tree-bytes PATCH for layout changes, `$state.raw` trees, lazy
  node-blob cache. Design + as-built: `docs/STORAGE_V2.md`, `docs/API_CONTRACT.md`.
  Real-store result: list 419MB → 13KB; the 115MB workspace opens in ~0.6s,
  add-model 0.08–0.24s (was: tab OOM). Migration ran 2026-07-13 (17 convs, 1190
  blobs, byte-faithful-verified); `conversations.json.legacy` + a checksummed
  `.bak-20260713` remain in the instance dir until Clément clears them.
  - Also fixed en route (pre-existing): layout-less workspace open grafting
    foreign panel echoes into the opened conv's trees (durable pollution;
    regression smoke `browser_legacy_echo_graft.py` — read its docstring before
    editing, the repro has a false-green trap).
- [ ] **Storage v2 follow-ups** (accepted limitations, ranked):
  - Stop-all can't reach a chat whose live bucket was clobbered by a same-slot
    re-fire — the known single-slot detached-fire hazard, now with a concrete
    repro (see `browser_stop_generation.py` history); top item for the still-owed
    detached-fire review.
  - ~~Post-save lightening~~ **DONE (2026-07-13, same day):** after a successful
    save the shipped nodes' inline heavies are stripped client-side (blob cache
    seeded first — zero refetch for own turns); a FAILED save keeps them inline
    so the dirt re-merge re-ships them. Pure logic in `lib/save-plan.ts`
    (`heavyNodeIds`/`lightenTree`, unit-tested); smoke
    `browser_save_lightening.py` (failure-injection choreography — read its
    docstring before editing).
  - Foreign-fold reconciled turns get LOCAL node ids → can't lazy-fetch the
    owner's blobs even after its PUT lands; heals on reload/switch-back (same
    visible behavior as v1's light echo).
  - Opening a bare/legacy workspace persists panel-UI defaults once → bumps
    `updated_at` (recency reorder on first open; v1 did it too).
  - Smoke-suite hygiene: the browser smokes want two environments (fixtures root
    vs fresh state — real highlight rules perturb `chart_rules`' oracle) and must
    not run concurrently with CPU-heavy work; 2 pre-existing stale smokes remain
    (`continue_scope`: `.prefill-scope` selector gone; `readme_shots`:
    pre-ModelDropdown assumptions) — worth a repair pass.

- [x] **Overhaul the highlight UI — SHIPPED.** Replaced the hardcoded
  ed_sheeran/dentist/vesuvius regexes with **user-defined highlight rules**
  (sidebar editor): named rules, palette, multi-pattern with or/and, regex/case
  toggles, role scope, reorder, per-scan-root persistence, seeded defaults. Model +
  matching ported faithfully from **samplescope** (kept separate — React vs Svelte
  rules out a shared component; the ~150-LoC matching core is mirrored, not shared).
  - **Naming:** "highlights" now = the coloring rules (`/api/highlights`, rules
    CRUD + reorder). The old saved-samples slideshow was renamed **pins**
    (`/api/pins`); legacy `highlights.json` auto-migrates to `pins.json` on first
    run (backup at `highlights.legacy.json`).
  - Files: `lib/highlight-match.ts` (pure matching, 28 unit tests via
    `node web/src/lib/highlight.test.ts`), `lib/highlight-render.ts` (md+math+paint
    pipeline), `lib/highlights.svelte.ts` (rules store), `lib/HighlightRules.svelte`
    (editor), `lib/render.ts` (thin store-coupled entry), `api/routes/highlights.py`
    (rules) + `api/routes/pins.py` (saved samples) + `settings._migrate_legacy_highlights`.
  - **Known limits:** matching runs on marked's entity-encoded output (patterns with
    raw `<`/`&`/`'` may not match); the old cross-turn conditional ("highlight the
    answer only if the *question* mentioned X") is gone — per-message role-scoped
    rules don't span turns. Trimmed vs samplescope: no column-scope / JS-condition.

- [x] **Assistant prefill.** ✅ Composer prefill field (collapsible, above the send
  box) + `tinkpg chat/compare --prefill`. Sends the prefill as a trailing
  `{role:'assistant'}` message; `tinker_sampler.render` treats it as a renderer
  prefill the model EXTENDS. Type raw `<think>` (Qwen/Kimi: open it yourself;
  DeepSeek auto-opens). Native tinker path parses `(assistant-region + completion)`
  so prefilled thinking lands in `reasoning`, not raw tags in `content`
  (`prefill_incorporated` tells the client not to re-prepend). OpenRouter /
  loose-sampler / base-model-n==1 get response-prefill best-effort (no region
  parse). Smoke: `tests/small-smokes/prefill_thinking_check.py`. Persists across
  sends so you can draw N samples off one prefill.
- [x] **Persist named workspaces to disk.** ✅ Subsumed by workspace branching
  (above): `/api/workspaces` store + the sidebar dropdown. (We did NOT extend
  PlaygroundState to carry the trees — they live in their own store to keep the SSE
  snapshot small; only the active path stays in `messages`.)
- [x] **Distribution chart overhaul — SHIPPED 2026-07-08.** The chart's default
  mode now rides on the highlight rules: each sample bucketed by the SET of
  matching rules — grey = no match, solid = one rule, **striped = multi-rule
  combo** (stripes cycle the constituent rule colors). Turn picker (defaults to
  the LATEST assistant turn), "match thinking" toggle, per-bar `n=`, hover
  tooltips (count/total), click-a-segment → inspector listing that bucket's
  samples with the matches painted, live-updating while a batch streams, and the
  legacy exact-answer histogram behind a mode toggle.
  - Files: `lib/chart.ts` (pure bucketing, 33 unit tests via `node
    web/src/lib/chart.test.ts`), `lib/ChartModal.svelte` (all chart UI state),
    `ruleMatches` in `lib/highlight-match.ts`, thin `chartSources` gatherer in
    `+page.svelte`. Deterministic smoke (seeded 2-turn tree, zero model calls):
    `tests/small-smokes/browser_chart_rules.py`.

- [ ] **Highlight rules as FILTERS (requested 2026-07-08).** Let a rule act as a
  filter, not just paint — e.g. show only samples (in the sample cards / cycler /
  chart inspector) matching or not matching selected rules. Clément: "allow the
  highlight to also serve as filters — that's for later / another context." Design
  sketch: a per-rule filter toggle in the sidebar (off = paint-only), filtered
  views get a "k of N shown" banner; the chart's rule buckets already compute the
  match sets, so the filter predicate can reuse `ruleMatches`/`chartRules`.

- [ ] **Generate view + "send to chat".** A scratchpad distinct from the chat: free
  prompt (text or messages builder) → sample across selected models side-by-side →
  promote a chosen result into a named workspace. (Dashboard's Multi-Generation
  tab + "Continue to Chat".) This is the "don't click New Workspace every time"
  UX the requester wanted.
- [ ] **Auto gen-logging to JSONL.** Every generation appended to a per-scan-root
  `generations.jsonl` (one row/sample: timestamp, model, sampler_path, params,
  prompt_tokens, outputs, system_prompt, messages). JSONL (append-only, greppable),
  NOT the dashboard's per-file YAML. Matches the research-code "save raw data" rule.

## Later / optional

- [ ] **Discuss with Clément: two CLI entry points (`tinkerscope` vs `tinkpg`) — merge or keep
  split?** (raised 2026-07-23). Today `tinkerscope` (`serve.py`) is the server/lifecycle +
  offline-state entry point — `serve`, `--pack`, `pack export` — things that touch the state dir
  directly, some *before* any server runs; `tinkpg` (`cli.py`) is a pure HTTP client that drives a
  RUNNING server over its API. Genuinely different roles, but the split bites: `pack export` is a
  `tinkerscope` subcommand yet users reach for `tinkpg pack` (a teammate hit exactly this,
  2026-07-23), and the skill's command table is all `tinkpg`. Options to weigh: (a) keep split,
  sharpen docs + maybe a `tinkpg pack` that errors helpfully / aliases; (b) merge into one
  `tinkerscope <subcmd>` (`serve`/`chat`/`pack`/…) with `tinkpg` kept as an alias; (c) something
  else. Real tradeoff (a running-server HTTP client vs server-lifecycle/offline ops are different
  modes) — Clément's call on whether unification is worth the churn. A workspace, not a task.

- [ ] **Init the live state bus from `prefs.json` `last_session` on startup** (raised 2026-07-23).
  Today the bus starts at `PlaygroundState` defaults and only gets the saved panels/params when a
  BROWSER loads and `restoreSession` pushes prefs (gated on freshState). Two costs: (a) after a
  restart the bus is empty until a browser re-primes it (the documented "re-prime after restart"
  pain — see the tinkerscope-server-machine-state memory); (b) a `--pack`-seeded instance shows
  library defaults to `tinkpg params` until a browser loads, so a pure-CLI consumer never gets the
  pack's params/panels. Fix direction: on startup, seed `PlaygroundState` from `prefs.json`
  `last_session` (panels + params) when present. Design caveat — this makes a restart "remember" the
  last layout even for CLI-only use, a behavior change to the empty-on-restart contract, so it's
  Clément's call. Verified 2026-07-23 that apply writes prefs correctly and the browser DOES prime
  the bus — this only closes the pre-browser / CLI-only window.

- [ ] **Night-shift dogfood report (2026-07-18, Fable) — CLI gaps found by using it
  for a full research cycle.** Ranked by how much hand-compensation they cost:
  1. *(dup of the fold-full-fanout item — PROMOTE IT)* every analysis lived in
     JSONL logs because `--n K` persists one rep; `samples --node` can't show
     the fan-outs the CLI itself fired. This was the single biggest tax.
  2. **`tinkpg workspace new --name X --panel <run>[@ckpt] …`** — workspaces were
     created twice tonight via raw POST (opus once, me once); a CLI verb would
     encode the correct shape (real run_ids, seen_panels) and dodge the
     phantom-panel trap by construction.
  3. **`tinkpg hold <workspace>`** (or a repo script) — the headless holder
     browser is load-bearing for CLI-driven exploration and was hand-rolled
     from scratch twice. Promote the pattern to a primitive.
  4. **`samples`/wave-log `--export-ancestry K out.json`** — the core loop of
     the clean-violation method is elicit → READ → pick sample K → loom from
     it; extracting K into an ancestry file was a python one-liner each time.
  5. **`tinkpg wait [--timeout N]`** — block until `running=no`; sequential
     waves needed a sleep/check dance between every fire.
  6. **Per-panel `running`** (or CLI-side queueing) — the GLOBAL flag blocks
     concurrent fires at different panels for no structural reason.
  Considered and NOT filed: a generic logprob-anchor analyzer verb — the
  per-experiment scripts (`analyze_boundary.py` / `analyze_verdict_anchor.py`
  in the weird-personas notes dir) are the right home; the CLI's job ends at
  faithful `--json`.


- [ ] **CLI fires should point the browser view at the context being sampled.**
  Observed (Clément, 2026-07-18, live): a `continue --ancestry-file` fire shows
  the panel's "sampling…" indicator while the panel still DISPLAYS a different
  thread than the one being sampled from — misleading (the streamed samples
  belong to a context you can't see). Fix direction: when a CLI chat starts on
  a panel, the browser should switch that panel's view to the thread matching
  the fired history (reuse the echo-reconcile content-matching; for an
  --ancestry-file context that matches no tree branch, show some "sampling an
  external context" hint instead of silently overlaying the wrong thread).


- [ ] **CLI small follow-ups (post `continue`/`send`, 2026-07-17):** DRY the
  node-resolution logic now duplicated between `samples --node` and `continue`'s
  `_continue_target`; consider erroring on `--node` + `--turn` together
  (currently `--turn` is silently ignored); consider folding a CLI-fired chat's
  FULL n-sample fan-out into the open browser's tree (today the echo-reconcile
  keeps only sample 0 as representative — full fan-outs live in CLI stdout).
- [x] **`--logprobs`/`--json` gave nothing at `--n 1`** (filed 2026-07-17,
  RESOLVED 2026-07-20 by the base-parity commit 3716003): n==1 used to stream
  through the OpenAI-compatible `/completions` path (`sample_stream`'s
  token-by-token branch), which never calls `_token_logprobs`. 3716003 made
  `run_id` + `base_model` picks ALWAYS sample native (option (b), for real —
  verified: `base_model` n=1 now returns non-empty `token_logprobs` with no
  delta events), so `--logprobs`/`--json` carry logprobs at any `n` for native
  sampling. The only remaining logprob-free n=1 path is a **loose checkpoint by
  sampler_path** or **OpenRouter** (`stream = n==1 and run_id is None and
  base_model is None`) — inherent for OpenRouter (external, no logprobs), niche
  for loose ckpt; `--logprobs` there prints the "none captured" note rather than
  a silent empty. Not worth a `_die` guard.


- [ ] **Wire/disk rename: conversations → workspaces (staged).** Handoff with
  the trap inventory + staging detail: `docs/HANDOFF_WORKSPACE_RENAME.md`. The vocabulary
  rename shipped 2026-07-17 (UI/CLI/docs say workspace; threads = root
  siblings); the wire (`/api/workspaces`, `workspace_id`, `?c=`) and the
  on-disk per-workspace files still carry the legacy name. Magic-wand answer
  is YES — full consistency is worth it big-picture (legacy naming is a
  permanent reader tax) — but it's a persistence migration, so do it as its own
  deliberate pass at a quiet moment: alias endpoints (`/api/workspaces` primary,
  old kept), one-shot disk dir migration with backup, bus field + `?w=` with
  back-compat read, frontend store/type renames last. Not urgent; do NOT do it
  piecemeal alongside other work.


- [ ] **Busy-latch after a bus drop while ANOTHER chat is still running.** Detached
  fire moved `endToken` (which clears `convo.busy` → New/switch gating) onto the bus
  `chat_done`. If the `/api/state/events` EventSource drops and misses a chat's
  terminal, its token latches `busy`. `convo.reconcileOnReconnect` (fired on the
  reconnect snapshot) closes the COMMON case — server `running===false` ⇒ every
  lingering token is stale ⇒ clear. The remaining case: the server is STILL running
  some OTHER chat at reconnect (`running===true`), so a token whose OWN terminal we
  missed in the gap stays latched until that other chat ends. The snapshot's global
  `running` bool can't disambiguate per-token; a clean fix needs per-panel/per-chat
  running in `PlaygroundState` (then reconcile each token against whether ITS chat is
  still in flight). Low-frequency (needs a drop mid-multi-chat) + a refresh clears it.
- [ ] **Echo-lag persist race on layout mutations (reorder / setRun).** A panel
  mutation persists via `patchState(...)` + `convo.save()`, but `#doSave` reads the
  panel LAYOUT from `live.state` ~400 ms later, and `patchState`'s flush discards
  the setState response — `live.state` learns only via the SSE echo. If that echo
  lags past the save debounce, the OLD layout is persisted once (self-heals on the
  next save). Traced during the 2026-07-09 drag-reorder review; same family as the
  system-prompt × workspace-switch contamination (whose fix — assign the
  setState response into `live.state` + flush pending patches before a switch —
  would close this too).
- [x] **Fold aborted-chat partials into the committed tree (deterministically).**
  ~~When Stop hits an OWN chat, the completed samples stay visible in the live bucket
  and — in the common timing — get folded into the tree via `#onExternalDone`'s
  reconcile … But that fold is a race between `endToken` and the chat_done bus
  event, so it isn't guaranteed …~~ **DONE (2026-07-13, detached-fire rework.)** Own
  chats no longer drain a response stream — they fold from the bus bucket on the
  SINGLE bus `chat_done` (`chat.tryFoldOwnDone`), so there is no `endToken`-vs-bus
  race left: the fold is deterministic, and a cancelled chat's already-completed
  partials fold on the same terminal. The `prefill_incorporated` gap is closed too —
  it's now threaded through `parseSample` onto the bucket samples, so the fold
  prepends the prefill exactly like the old drain path (no doubling).
- [ ] **Branch-switch render latency (~30–50 ms?) — uncertain observation
  (2026-07-08).** While verifying the scroll rework, the verifier measured the
  cycled branch's *content/cycler text* appearing ~30–50 ms after the click,
  coincident with the `/api/state` SSE echo of the `#mirror` call — but couldn't
  disentangle "render gated on the echo" from "handler + DOM-flush latency +
  polling resolution". Rendering reads only local `convo.trees`, so it *should*
  be near-instant. If cycling ever feels laggy, instrument this first; the fix
  direction is the scroll-map's "decouple rendering from the #mirror echo" note
  (scroll itself is already decoupled). Related quick win: `patchState` applies
  nothing optimistically, so e.g. the thinking toggle's visual change waits for
  the SSE round-trip (~50 ms).
- [ ] **Forced-pick mode (toggle).** Today pick-a-sample is additive — sample 0
  auto-commits as the default and "Use this" overrides it. Optionally add a mode that
  *blocks* the next send until the user picks (dashboard behaviour). Decide if the
  extra friction is worth it.
- [ ] **Saved prompt library** with folders (reusable probe prompts; dashboard's
  `multi_prompt_tab` + `folder_manager_ui`). tinkerscope only has localStorage prompt
  history today.
- [ ] **Markdown export** of a workspace / result set ("Save all").
- [ ] **Static site: trim the blobs.** `site export` keeps `token_logprobs`, which is
  ~97% of the bytes (24 MB of workspaces vs 901 MB of blobs on the real store). Today
  the choice is all-or-nothing (`--no-logprobs`) plus `--workspace` selection, and the
  command reports the per-workspace breakdown. A middle setting would help: keep
  logprobs only for the turns a workspace's chart actually uses, or only the newest N
  turns per thread. See `docs/STATIC_SITE.md` §Size.
- [ ] **Static site: skip the discarded body prep in `site_export`.** It calls
  `pack.export_pack` purely for the model list + defaults and throws away its prepared
  workspace bodies — but that prep deep-copies every body AND fetches every
  `raw_meta` blob, which on a ~900 MB store is real wasted I/O on a command that's
  already the slow part. Wants a `skip_bodies` (or models-only) path through
  `export_pack`. Found in review; not a correctness issue.

**Decided against** (2026-07-30, Clément): BYOK-OpenRouter sampling on a published
static site. A site can't sample Tinker checkpoints at all — the oai endpoint sends no
CORS headers, and even with them it serves BASE weights for a LoRA sampler path
(tinker-feedback#125) — so the only live option would have been reference models via a
visitor's own OpenRouter key. Not wanted: it would reintroduce a composer into a
read-only UI and need a third gating state. Read-only stays read-only.
- [ ] **Multi-prompt batch grid** (N prompts × M models). Different use case
  (systematic eval) — may belong in `inspect_ai` land instead of the playground.
- [ ] **Reasoning/raw on committed turns.** Committed transcript messages are
  `{role, content}` only, so reasoning/raw_text are lost once the bucket clears (same
  as before this change). If wanted, widen the committed message shape.
- [ ] **Thinking-mode reasoning split on the n=1 `base_model` `/completions` path**
  *(low priority — not urgent).* `tinker_oai.completions_stream` → `_normalize_content`
  splits reasoning only on a literal `<think>` in the *output*, but in thinking mode
  `<think>` lives in the *prompt*, so the whole think block lands in `content` instead of
  being separated. Only bites raw base models with thinking ON (LoRA runs route native,
  loose checkpoints use `/chat` `separate_reasoning`, both of which handle it). Native
  `parse_response` already does the right thing. Overlaps
  [tinker-cookbook#684](https://github.com/thinking-machines-lab/tinker-cookbook/issues/684).
