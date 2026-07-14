# tinkerscope — agent orientation

Browser playground for **Tinker-trained checkpoints** that **auto-discovers
training runs** under a directory tree (scans for `checkpoints.jsonl` /
`config.json`), lets you chat with / sample from them, and is **drivable live
from the terminal** via the `tinkpg` CLI. Forked from Harry Mayne's playground;
see `README.md` for the full feature list + credits.

## Doc map (read this first)

| Doc | What it's for | Status |
|---|---|---|
| `README.md` | User-facing: what it does, how to run, the CLI, tests | current |
| `docs/API_CONTRACT.md` | Authoritative HTTP endpoint + SSE event shapes (incl. `/api/conversations` + the branch-tree shape) | current |
| `docs/BRANCHING_DESIGN.md` | **As-built design + contract for conversation branching** (tree model, fold/reconcile rules, persistence, known limits). The source of truth for the feature | current |
| `docs/STORAGE_V2.md` | **Storage v2 design (SHIPPED 2026-07-13)** — why the single conversations.json OOM'd the browser, the light-tree/heavy-blob split, per-conversation files + migration, wire-contract deltas, frontend memory policy. As-built endpoint shapes live in `API_CONTRACT.md` | shipped; follow-ups in `docs/TODO.md` |
| `docs/HANDOFF_BRANCHING.md` | Historical planning record for branching (what Clément asked vs what I inferred — §2–§4 = the requirements). §5 = the highlight-UI overhaul (now shipped — see `docs/TODO.md`) | branching + §5 both shipped |
| `docs/HANDOFF_MULTIPANEL.md` | **N-way model comparison workspace — SHIPPED** (`panels[]`, `trees` map + back-compat migration, add/remove/reduce panels, composer send-targeting, send-branch-to-panel, N-run CLI `compare`). §9 = the as-built grounded plan + locked decisions (architecture B; per-conversation persistence; global params; stable panel ids). §5 = the original 2-panel site-map | shipped; per-conversation panel *layout* now persists too (switch restores a conv's model set; new conv inherits the current one's models, Shift+new = blank — see `Conversation.panels` in `docs/API_CONTRACT.md`); follow-ups: the §4 small items |
| `docs/TODO.md` | Roadmap (branching marked done) | current |
| `deprecated/HANDOFF.md` | Original tool-build handoff (Harry's playground → tinkerscope). Build done; file refs predate the `src/tinkerscope/` restructure | deprecated, kept for history |

The durable knowledge HANDOFF.md once held now lives in code docstrings (below)
and in this file's reference section; HANDOFF.md itself is retired.

## Working conventions

- **`tinkpg` CLI changes ship with their docs, in the same commit.** Any new
  command / flag / behavior change updates: README.md §"The CLI" (command table
  + option notes) AND the tinkerscope skill. **The skill lives in this repo at
  `.claude/skills/tinkerscope/SKILL.md`** — `~/.claude/skills/tinkerscope` is a
  symlink to it, so always edit the repo path (the Edit tool refuses to write
  through the symlink) and commit it like any other file. Touch
  `docs/API_CONTRACT.md` too if the HTTP surface changed. (Checklist is also in
  `cli.py`'s module docstring.)
- **Committing — no need to ask first.** Commit straight to `main` whenever work
  is at a clean, verified point; show the diff summary of what landed, don't gate
  on approval (Clément's standing preference for this repo — overrides the global
  "always ask before committing"). A `web/` pre-commit hook (`.githooks/pre-commit`,
  wired via `core.hooksPath`) runs `npm run build` and aborts the commit on a build
  failure; bypass a deliberate WIP commit with `git commit --no-verify`.
- **Deploys / "my change isn't showing".** A running instance never live-reloads:
  a backend (Python) change needs a process restart; a web change needs
  `npm run build` (the pre-commit hook runs it on every `web/` commit) and then
  only a browser refresh — `main.py:_web_dist()` serves `web/dist` from disk per
  request. If installing as a uv tool, install **editable** (`uv tool install -e .`)
  so the process runs this checkout; a plain `uv tool install .` freezes a wheel
  whose bundled `web_dist` snapshot never updates again. For HMR iteration on
  *uncommitted* web edits, `./run.sh <dir>` starts a vite dev server + its own
  backend. (Where the user's live instance actually runs — port, service, scan
  root — is machine state: it lives in Claude's project memory, not in this file.)

## Where the contracts live (source of truth = code, not docs)

- **Discovery contract** (the two files every `tinker_cookbook` run drops —
  `config.json` + `checkpoints.jsonl`, their fields, the scan, defensive
  parsing, sampleability gating): `src/tinkerscope/api/discovery.py` — the
  module docstring + the `Checkpoint` / `Run` dataclasses document it. Key
  gotcha encoded there: **sample from `sampler_path`, not `state_path`.**
- **Inference / sampling** (renderer selection, the thinking on/off toggle and
  its two naming conventions, thinking-block parsing, prefill, per-sample
  streaming + cancel-on-disconnect): `src/tinkerscope/api/tinker_sampler.py` —
  docstrings are thorough and current. tinkerscope calls the **tinker SDK
  directly** now; the old latteries path is gone (its renderer-cache and
  thinking-parse *lessons* carried over into this file).
- **Shared-state bus / live-drive** (the `tinkpg` ↔ browser lockstep): see
  `docs/HANDOFF_BRANCHING.md` §1 + `src/tinkerscope/api/state.py`.

## Frontend map (`web/` — Svelte 5 / SvelteKit SPA)

Read this before a UI task instead of Exploring `web/`. The UI is a single-route
SvelteKit SPA under `web/src`. Three kinds of file, by suffix:

- **Stores** — `*.svelte.ts` exporting a class instance as a singleton (runes in
  a module; this is the house pattern). Reactive `$state` fields read/written
  across the app:
  - `lib/state.svelte.ts` → `live` — mirrored shared `PlaygroundState` (selection/
    params) + per-panel **streamed sample buckets**, both driven by the
    `/api/state/events` SSE. The render bus.
  - `lib/conversations.svelte.ts` → `convo` — owner of the per-panel **branch
    trees** + persistence + the external-fold reconcile. The conversation model.
    Storage v2 (`docs/STORAGE_V2.md`): `list` holds SUMMARIES only (bodies are
    fetched on open); `trees` is **`$state.raw`** (immutable refs — never mutate
    a node in place, nothing would react or save); saves accumulate dirty-panel /
    dropped / layout-flag DIRT and ship a partial-upsert PUT (dirty trees only)
    or a zero-tree-bytes PATCH — the request planner is pure `lib/save-plan.ts`
    (**has `save-plan.test.ts`**). Legacy `{tree, compare_tree}` bodies force a
    FULL-map first save (partial upsert would drop the un-sent panel).
  - `lib/node-blobs.svelte.ts` → `nodeBlobs` — the per-node **heavy-blob cache**
    (token_logprobs / raw_meta live server-side as write-once blobs; light nodes
    carry `has_*` flags). Batch `ensure()` (20 ms micro-batched → one POST),
    seeded at fold time by the chat store, reset on every conversation
    transition. Consumers: ChatMessage's token view + raw-meta disclosure,
    ChartModal first-token mode (fetches the picked turn only).
  - `lib/chat.svelte.ts` → `chat` — the **generation-fire lifecycle**: POST
    `/api/chat`, drain, fold under the user node, per-panel abort controllers +
    the live-bucket prefill color. UI-agnostic — the caller (+page) passes a
    `ChatParams` bundle + a resolved `ChatModelField`, so it never touches the
    sampling UI. +page keeps thin glue (`paramsBundle`/`resolveModelField`/a
    `fireOne` wrapper) over it.
  - `lib/model-catalog.svelte.ts` → `modelCatalog` — the **model catalogs +
    labels**: `runs` / `openrouterModels` / the lazy tinker + OR typeahead
    catalogs (+ their loading/error flags) / the localStorage recents; the
    loaders (`loadRuns`/`loadOpenrouterModels` take an `onError` callback for
    +page's shared banner; `loadOrCatalog`/`loadTinkerCatalog` own their error
    state); the id→label resolvers (`runById`/`runLabel`/`openrouterLabel`/
    `baseLabel`/`ckptLabel`/`selectedModelLabel`, layered on the pure
    `lib/model-sel` sentinel encoding); and `modelItems(runId)`, the per-panel
    dropdown item-list builder (was a giant inline `{@const}` in +page markup).
  - `lib/branch-ops.svelte.ts` → `branchOps` — the **chat-thread branching
    handlers** (edit / regenerate / delete / cycle / select / continue, per panel
    and across-all-panels). All tree mutation goes through `convo.setTree`; scroll
    policy (PRESERVE/SNAP) + bucket clearing live here. UI-agnostic, like `chat`:
    +page injects its four seams once via `branchOps.configure({ panelSels,
    panelBusy, withPrefill, fireOne })`, and markup / keyboard-nav call the
    handlers as `branchOps.<name>(...)`.
  - `lib/highlights.svelte.ts` → `highlightStore` — user-defined render-time
    coloring rules + persistence.
  - `lib/logprobs.svelte.ts` → `logprobView` — the sidebar **"Token probs"**
    display toggle (localStorage-persisted). Display-only: capture is the
    server default for native tinker sampling, so flipping it on works
    retroactively on stored turns.
  - `lib/scroll.svelte.ts` → `panelScroll` — **the only scrollTop writer**: the
    per-panel FOLLOW (streaming, stick-to-bottom gated) / PRESERVE (tree
    mutations keep position) / SNAP (send, conversation open) / REVEAL
    (keyboard focus moved off-screen → minimal container-only scroll) policy.
    Its module docstring records why (the old global bottom-pin = the scroll
    flicker). New scroll behavior goes through this store, never inline.
- **Pure logic** — plain `.ts`, no Svelte/DOM, unit-testable (some have
  `*.test.ts`):
  - `lib/tree.ts` — all branch-tree ops (activePath, fold, regen, edit, delete,
    cycle, siblings). The single source of branching truth. **Has `tree.test.ts`.**
  - `lib/model-sel.ts` — the `openrouter:`/`base:`/`ckpt:` sentinel encoding
    (prefixes, predicates, id extractors) for a panel's model selection.
  - `lib/reorder.ts` — list-agnostic drag-reorder math: `reorderById(items, fromId,
    toGap)` (move an item by stable id to a gap index; returns the SAME ref on
    no-op/unknown so callers skip a redundant write) + `isNoopGap` + `gapFromPointer`
    (axis-aware midpoint test). **Has `reorder.test.ts`.** The reactive glue is
    `lib/drag-reorder.svelte.ts` → **`DragReorder`** (a class you instantiate PER
    list — `'x'` for the panel columns, `'y'` for the highlight rule rows): owns the
    `dragId`/`overGap` drag state + the `start`/`over`/`drop`/`end`/`showAt` handlers.
    Only a dedicated GRIP is `draggable` (never a container wrapping selectable text/
    inputs — a draggable ancestor kills text selection). Drives both the column-header
    drag (+page *Panel drag-to-reorder* — reordering the shared `panels[]` moves the
    chat columns, sidebar Models pickers, and send-chips at once) and the highlight
    rule-row drag (`HighlightRules.svelte`, replaced the up/down arrows → `reorderHighlightRules`).
    Smokes `tests/small-smokes/browser_{panel,highlight}_drag.py`.
  - `lib/label-split.ts` — `splitTail(label, siblings?)`: tail-preserving
    truncation ("middle ellipsis") for run/model labels. Sibling runs share a
    long prefix and differ only in the last few chars (`…_s1_lr1e-3` vs
    `…_s1_lr5e-3`); this carves the label into `{head, tail}` so the renderer
    (TruncLabel) ellipsizes only the head and always shows the distinguishing
    tail. Sibling-aware mode anchors the tail at the divergence from the closest
    visible sibling. **Has `label-split.test.ts`**; browser smoke
    `tests/small-smokes/browser_label_trunc.py` (now the ModelDropdown-trigger
    single-label site — sibling LIST rows moved to the diff view below).
  - `lib/label-diff.ts` — `diffLabels(labels)`: the "smarter" layer over
    tail-preserve for the case it can't handle — sibling runs that share BOTH ends
    and differ only MID-name (`…_base_ed_sheeran_…` vs `…_instruct_…`, which a tail
    cap renders identically). Clusters the visible labels by first segment, then
    positionally votes over aligned indices: cluster-constant runs collapse to a
    dimmed `…`, every varying segment shows in full (reaches interior constants a
    prefix/suffix scheme can't; degrades to prefix-only elision on ragged families).
    Peels the `⊘/?/◆/◇/↗` status-icon prefix so aged-out runs still cluster.
    Returns null per row → caller falls back to TruncLabel. Invariants (distinct
    labels never collide; only cluster-constant segments elide) are in
    **`label-diff.test.ts`** against both real fixture families; browser smoke
    `tests/small-smokes/browser_label_diff.py`.
  - `lib/fuzzy.ts` — typo-tolerant model search: `tieredFilter(query, items,
    matches)` keeps exact substring as the primary tier (behavior-identical when it
    yields ≥1) and only on ZERO substring matches engages a bigram-Dice fuzzy tier
    (`fuzzyFilter`/`fuzzyScore`) so `ed_shreean`/`instrcut` still surface the run.
    Token-wise (split on non-alnum, `lr1e-3` whole), length-weighted over the
    query's tokens (a run matching MORE of the query ranks higher), threshold 0.4
    (tuned on fixture names: typos ≥0.53, garbage ≤0.28), ranked + capped ~20,
    bigram sets cached per token. **Has `fuzzy.test.ts`**; browser smoke
    `tests/small-smokes/browser_fuzzy_search.py`.
  - `lib/chart.ts` — distribution-chart bucketing: `chartByRules` (samples
    bucketed by the SET of matching highlight rules — grey none / solid single /
    striped combo) + `chartByAnswers` (legacy exact-match histogram) +
    `chartByFirstToken` (the MODEL's probability distribution over the first
    generated token, from stored `token_logprobs` — segment pct = model prob,
    count/sampleIdx = the empirical side) + label helpers. `chartByFirstToken`
    takes `FirstTokenOpts {excluded, added, groups}`: it works on **units** (a
    token OR a merged group via `ftGroupKey`), so exclude (mass renormalized out,
    → `massNote`), **add** a recorded-but-hidden token (surfaced from the rest —
    `AddedToken`, its p sourced from stored logprobs, NOT a model call), and
    **merge** (drag tokens into one color, prob+count summed) all compose. **Has
    `chart.test.ts`** (exclude/add/merge cases); browser smoke
    `tests/small-smokes/browser_chart_firsttoken_ops.py`.
  - `lib/token-search.ts` — `normalizeForMatch` / `matchKind` / `searchStoredTokens`:
    the first-token add-search's tiered matching (exact ‹ prefix ‹ contains) with
    space-marker normalization (leading space / ▁ / Ġ ≡ bare, case-insensitive),
    over tokens already recorded for the turn (top-K alts + sampled first tokens).
    **Has `token-search.test.ts`.**
  - `lib/token-logprob.ts` — token-logprob display math: `prob`/`pctLabel`,
    `surprisalAlpha` (the single-hue heat tint — alpha ∝ -logprob), `displayToken`
    (whitespace glyphs), `firstTokenDist` (one panel's position-0 distribution:
    newest sample's top-K as reference + sampled outliers; flags `mixed`).
    **Has `token-logprob.test.ts`**; smokes `tests/small-smokes/
    browser_token_logprobs.py` (seeded, deterministic) + `…_live.py` (real
    tinker sampling end-to-end).
  - `lib/kbnav.ts` — keyboard row-navigation helpers: nav-key set, clamped
    focus-index stepping, the typing-target/modal-open guards. Consumed by
    +page's *Keyboard row navigation* section (click a row → focus ring; ↑/↓
    walk the panel view, ←/→ = the row's ‹k/N› cycler, Esc clears). **Has
    `kbnav.test.ts`**; browser smoke `tests/small-smokes/browser_kbnav.py`.
  - `lib/chat-stream.ts` — `drainSamples`: parse the `/api/chat` SSE into samples.
  - `lib/highlight-match.ts` / `lib/highlight-render.ts` — pure matching + the
    markdown+math+highlight render pipeline. **`highlight.test.ts`.**
  - `lib/render.ts` — store-coupled render entry point (wraps highlight-render).
  - `lib/api.ts` — typed backend client + named-event SSE helper.
  - `lib/types.ts` — TS types mirroring the backend (see `docs/API_CONTRACT.md`).
  - `lib/tooltip.svelte.ts` — the `use:tip` tooltip action.
- **Components** — `.svelte`:
  - `routes/+page.svelte` — **the workspace component**: wires every store +
    handler to the markup. Still the biggest file (~2.2k lines); organized by
    `// ── Section ──` banner comments — **`grep '// ──' routes/+page.svelte`
    for the in-file table of contents** rather than scrolling. Notable sections:
    *Send a chat* (`sendMessage` + the `fireOne` wrapper — the core send path;
    the fire/abort/fold machinery itself is in `lib/chat.svelte.ts`), *Chat-thread
    branching* (edit/regenerate/delete/cycle/select — the largest cluster),
    *Conversation rendering* (`panelView`/`bucketTurn` — overlays the live bucket
    on the tree's active leaf), *Panel lifecycle* (add/remove panels),
    *Keyboard row navigation* (the ONE focused row + arrow-key handler over
    `lib/kbnav.ts`), *Conversation ↔ URL sync*, *Session persistence*,
    *Lifecycle* (`onMount`).
    Markup order: sidebar → chat area → input bar → the modal components below.
  - `lib/Modal.svelte` — shared modal chrome (overlay, header, close,
    click-outside, Escape, body slot). Every modal wraps this; `modalStyle`
    overrides the box width per modal.
  - `lib/ChartModal.svelte`, `lib/TagModal.svelte`, `lib/DatasetModal.svelte`,
    `lib/SlideshowModal.svelte`, `lib/OrManagerModal.svelte`,
    `lib/TinkerPickerModal.svelte` — the six workspace modals. Each owns its body
    + specific styles; the parent passes data in and gets results via callbacks.
    ChartModal is the smart one: it receives per-panel per-turn samples
    (reactive; live-updates mid-stream) and owns mode toggle / turn picker
    (defaults to the LATEST turn) / match-scope / per-rule include-exclude
    chips (drop a rule the prompt makes ubiquitous from the bucketing;
    chart-only, session-scoped) / with-vs-without-thinking sample filter
    (shown only when the picked turn mixes both) / click-a-segment-to-inspect.
    Third mode "first token": the model's OWN probability distribution over the
    first generated token (needs stored `token_logprobs`; disabled otherwise). In
    that mode the legend becomes an **interactive chip row**: click a chip to
    exclude/re-include (renormalizes, shows the "over NN% of mass" note), drag one
    chip onto another to **merge** into one color (bespoke onto-drop DnD, not the
    gap-shaped `lib/drag-reorder`), and a search box **adds** a recorded-but-hidden
    token (from stored logprobs — `token-search` + `chart`'s `added`, no model
    call). All session-scoped module state, like the rule chips.
    Deterministic smokes (seeded tree, no sampling):
    `tests/small-smokes/browser_chart_rules.py` (rules) +
    `browser_chart_firsttoken_ops.py` (exclude / add / merge).
  - `lib/ChatMessage.svelte` — one chat row (committed node OR live bucket turn)
    + its per-row toolbar (edit/regen/branch/pin…). With `logprobView` on, an
    assistant body with `token_logprobs` renders `TokenLogprobs` instead of
    markdown (turns without data wear a "no token data" pill).
  - `lib/TokenLogprobs.svelte` — the token inspector body: the raw generated
    token stream (thinking tags and all — exact token boundaries beat markdown
    here), each token tinted by surprisal; hover → fixed-position popover with
    the token's probability + top-5 alternative bars.
  - `lib/ModelTypeahead.svelte` — the type-to-filter model combobox (used by the
    OpenRouter + Tinker picker modals, and as the panel body of `ModelDropdown`).
    Rows render via `DiffLabel` when the visible siblings form a diffable family
    (`diffLabels(visibleLabels)`), else `TruncLabel`. Search still matches the full
    label, so filtering is unaffected by the compact display. Filtering is TIERED
    (`lib/fuzzy` `tieredFilter`): exact substring primary, typo-tolerant fuzzy
    fallback only on zero substring matches — with a subtle "no exact matches —
    close matches:" note when the fuzzy tier is showing.
  - `lib/ModelDropdown.svelte` — select-like trigger button + floating panel
    wrapping `ModelTypeahead`; the sidebar's per-panel model picker (click →
    type to filter, no separate "Filter models…" textbox).
  - `lib/HighlightRules.svelte` — the highlight-rules editor UI.
  - `lib/TruncLabel.svelte` — the middle-ellipsis label: a two-span flex trick
    (head clips with `flex:0 1 auto`, tail always shows) over `splitTail`, plus
    the full-name `use:tip` tooltip backstop. The SINGLE-LABEL renderer — the
    `ModelDropdown` trigger and +page's `.column-title` / `.send-chip` — plus the
    fallback for `ModelTypeahead` rows a diff family doesn't cover. So two runs
    sharing a long prefix stay distinguishable at any width.
  - `lib/DiffLabel.svelte` — the diff-view label: renders `label-diff`'s compact
    parts (varying segments at full emphasis, cluster-constant anchors + `…` dimmed)
    with the same `use:tip` tooltip / aria-label affordances as TruncLabel; only
    the leading family anchor may shrink under width pressure. Used for
    `ModelTypeahead` rows when `diffLabels` returns a render for the row.

Cross-component CSS utility classes (`.sidebar-label`, `.btn-new`,
`.backend-error`, …) live in **global `app.css`** — scoped `+page.svelte` styles
don't reach extracted components, so shared classes must be global.

**Modules > the mega-file.** When adding UI, prefer a new/existing `lib/` module
or component over growing `+page.svelte`: pure logic → `.ts` (+ a `.test.ts`),
shared reactive state → a `*.svelte.ts` store, a self-contained UI block → a
`.svelte` component (wrap `Modal.svelte` for a dialog). Runtime smokes for the
extracted UI: `tests/small-smokes/browser_{chart_modal,modals}.py`.

## External reference paths (not in this repo; verified 2026-06-22)

- Tinker checkpoint schema (`CheckpointRecord`):
  `~/research-libs/tinker-cookbook/tinker_cookbook/checkpoint_utils.py:28`
- `tinker_cookbook` source tree: `~/research-libs/tinker-cookbook/`
- Where `config.json`'s shape is *defined* (this project's `Config`; other
  Tinker projects may differ): `~/projects2/negation_neglect/src/train/{custom_sft,tinker}.py`

## Box facts

- `TINKER_API_KEY` is **set** (remote sampling works today). `OPENROUTER_API_KEY`
  needed only for OpenRouter reference models.
- Test fixtures: **26 real run dirs** under
  `~/projects2/negation_neglect/datasets/training_datasets/` + the
  `~/projects2/weird-personas` runs (each has `config.json` + `checkpoints.jsonl`).
  **Sampleability is a ROLLING WINDOW, not fixed:** Tinker serves only the last
  ~14 sampler checkpoints (~6 weeks); a run samples iff its sampler UUID is still
  servable. Two failure modes show greyed-out / 404: (a) base model no longer served
  (e.g. `Qwen/Qwen3-30B-A3B-Base`); (b) sampler_weights aged out of the window — and
  **`sampleable: true` does NOT catch (b)** (the probe only checks the base model),
  so aged-out runs look green and only 404 on actual sample. Find a live run by
  cross-referencing `tinker_oai.list_checkpoints()` / `GET /api/tinker-models`, or use
  `tests/small-smokes/_smoke_models.{LIVE_RUN_ID,pick_servable_run}`. **Live as of
  2026-06-22:** the `04_2026-06-16_rationalization` deepseek/kimi runs (weird-personas);
  all April negation_neglect runs have aged out.
- CPU-only box; sampling is remote so no GPU/vLLM/LoRA-conversion needed locally.

## Build / verify

- **Web sources are 2-space indented** (one-time tab→space conversion 2026-07-14,
  recorded in `.editorconfig`; spaces make exact-match Edits reliable — don't
  reintroduce tabs). No formatter; match surrounding style (incl. the compact
  one-line-per-rule CSS).
- **Web** (from `web/`): `npm run check` (svelte-check; keep it at **0 errors** —
  the ~25 a11y warnings are known), `npm test` (the frameworkless `src/lib/*.test.ts`
  suites via node), `npm run build`. The pre-commit hook builds on web/ commits —
  but **merge commits skip it**: after `git merge`, run `npm run build` yourself
  or the served `web/dist` silently stays stale.
- **Python**: `uv run pytest -q` (no remote calls — capabilities probe is stubbed).
- **Isolated instance for testing** — NEVER test against the user's live server
  or `~/.local/state/tinkerscope`; run `scripts/dev-isolated.sh [--port N] [SCAN_DIR ...]`
  instead: it snapshots the real state into a throwaway `XDG_STATE_HOME` (realistic
  conversations/prefs as fixtures, live registry stripped) and launches from this
  checkout. Build `web/` first; agents launch it with run_in_background.
- **Browser smokes** (`tests/small-smokes/browser_*.py`, Playwright): point them at
  an isolated instance. ⚠️ Playwright's `.click()` AUTO-SCROLLS off-screen targets
  into view — when asserting scroll behavior, use programmatic `element.click()` /
  keyboard dispatch or the auto-scroll fabricates false scroll-position failures
  (cost a verifier two false rewrites once; see `browser_kbnav.py` for the pattern).
- Dev-HMR loop + more smoke commands: `docs/HANDOFF_BRANCHING.md` §6.
