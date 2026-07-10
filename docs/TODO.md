# tinkerscope — TODO / roadmap

Porting the nice UX from the old Streamlit `tinker-dashboard` onto tinkerscope's
streaming + auto-discovery + CLI-drive foundation. Order is rough priority.

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

- [x] **Conversation branching (tree) — SHIPPED 2026-06-22.** Full Claude.ai-style
  branching: nothing is destroyed; regenerate / edit / n-samples become sibling
  branches you cycle through with ‹ k/N ›. **Subsumed the just-built pick-a-sample**
  (N samples → N cycle-able branches) **and the "persist named conversations" item.**
  - Per-panel **tree** in a SEPARATE per-scan-root store (NOT in the SSE snapshot —
    diverged from the original handoff to respect `state.py`'s no-bloat principle).
    `messages`/`compare_messages` stay the linear ACTIVE PATH (sampler + CLI
    contract untouched; **CLI needed zero changes**).
  - n>1 → N sibling branches; regenerate on user+assistant; edit-user forks+regens;
    **shift+click edit** forks + copies the whole downstream conversation (no gen);
    edit-assistant = manual branch; **delete prunes the subtree**.
  - Named conversations via a **dropdown** (create/switch/rename/delete), each
    carrying its own `system_prompt`.
  - Files: `web/src/lib/tree.ts` (pure, 27 unit tests via `node tree.test.ts`),
    `web/src/lib/conversations.svelte.ts` (store: tree ownership, fold, persistence),
    `api/routes/conversations.py` (flock'd CRUD + corrupt-file backup),
    `+page.svelte` / `ChatMessage.svelte` (render + ops), `chat.py` `client_token`.
  - Design + contract: `BRANCHING_DESIGN.md`. Verified: 33 pytest, 27 tree tests,
    `tests/small-smokes/browser_branching.py` (token-free fork/cycle/delete/edit-leak)
    + `branching_real_sample.py` (real n=1 fold / regen / n=3 multi-fold).
  - **Known v1 limitations:** two tabs editing the SAME conversation = last-writer-wins
    (flock prevents file corruption + sibling clobber, not same-id logical merge);
    per-conversation mode/model-selection not restored on switch (only trees +
    system_prompt); CLI external turns fold only sample 0 + lack reasoning.

## Next

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
- [x] **Persist named conversations to disk.** ✅ Subsumed by conversation branching
  (above): `/api/conversations` store + the sidebar dropdown. (We did NOT extend
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
  promote a chosen result into a named conversation. (Dashboard's Multi-Generation
  tab + "Continue to Chat".) This is the "don't click New Conversation every time"
  UX the requester wanted.
- [ ] **Auto gen-logging to JSONL.** Every generation appended to a per-scan-root
  `generations.jsonl` (one row/sample: timestamp, model, sampler_path, params,
  prompt_tokens, outputs, system_prompt, messages). JSONL (append-only, greppable),
  NOT the dashboard's per-file YAML. Matches the research-code "save raw data" rule.

## Later / optional

- [ ] **Echo-lag persist race on layout mutations (reorder / setRun).** A panel
  mutation persists via `patchState(...)` + `convo.save()`, but `#doSave` reads the
  panel LAYOUT from `live.state` ~400 ms later, and `patchState`'s flush discards
  the setState response — `live.state` learns only via the SSE echo. If that echo
  lags past the save debounce, the OLD layout is persisted once (self-heals on the
  next save). Traced during the 2026-07-09 drag-reorder review; same family as the
  system-prompt × conversation-switch contamination (whose fix — assign the
  setState response into `live.state` + flush pending patches before a switch —
  would close this too).
- [ ] **Fold aborted-chat partials into the committed tree (deterministically).**
  When Stop hits an OWN chat, the completed samples stay visible in the live bucket
  and — in the common timing — get folded into the tree via `#onExternalDone`'s
  reconcile off the server-committed transcript (the server now commits the partial
  on a cancelled chat_done). But that fold is a race between `endToken` and the
  chat_done bus event, so it isn't guaranteed; the shipped floor is "partial visible
  in the bucket, no stuck state." A deterministic fold from `#fireChat`'s AbortError
  branch was NOT done because the fold's prefill-incorporation context
  (`prefill_incorporated` per sample) isn't carried on the UI-agnostic chat store's
  bucket samples, so prepending the prefill could double it. To do this right, thread
  the per-sample `prefill_incorporated` through the bucket (or fold from the server's
  committed `ps.messages` unconditionally for own cancelled chats). See
  `lib/chat.svelte.ts` `#fireChat` catch + `stopGeneration`.
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
- [ ] **Markdown export** of a conversation / result set ("Save all").
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
