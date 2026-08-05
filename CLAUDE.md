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
| `docs/API_CONTRACT.md` | Authoritative HTTP endpoint + SSE event shapes (incl. `/api/workspaces` + the branch-tree shape) | current |
| `docs/BRANCHING_DESIGN.md` | **As-built design + contract for workspace branching** (tree model, fold/reconcile rules, persistence, known limits). The source of truth for the feature | current |
| `docs/STORAGE_V2.md` | **Storage v2 design (SHIPPED 2026-07-13)** — why the single conversations.json OOM'd the browser, the light-tree/heavy-blob split, per-workspace files + migration, wire-contract deltas, frontend memory policy. As-built endpoint shapes live in `API_CONTRACT.md` | shipped; follow-ups in `docs/TODO.md` |
| `docs/HANDOFF_BRANCHING.md` | Historical planning record for branching (what Clément asked vs what I inferred — §2–§4 = the requirements). §5 = the highlight-UI overhaul (now shipped — see `docs/TODO.md`) | branching + §5 both shipped |
| `docs/HANDOFF_MULTIPANEL.md` | **N-way model comparison workspace — SHIPPED** (`panels[]`, `trees` map + back-compat migration, add/remove/reduce panels, composer send-targeting, send-branch-to-panel, N-run CLI `compare`). §9 = the as-built grounded plan + locked decisions (architecture B; per-workspace persistence; global params; stable panel ids). §5 = the original 2-panel site-map | shipped; per-workspace panel *layout* now persists too (switch restores a conv's model set; new conv inherits the current one's models, Shift+new = blank — see `Workspace.panels` in `docs/API_CONTRACT.md`); follow-ups: the §4 small items |
| `docs/HANDOFF_WORKSPACE_RENAME.md` | Historical planning record for the conversations→workspaces WIRE/DISK rename (shipped in v1.0.0, 2026-07-24). Deliberately keeps the OLD names — it describes the pre-rename state. The as-shipped result is `docs/MIGRATIONS.md` | shipped; historical |
| `docs/HANDOFF_SERVER_AUTHORITY.md` | **Design: server-authoritative workspace trees (ops protocol)** — inverts the browser-is-sole-writer architecture so the server folds + persists chats (headless CLI durability, fixes the CLI no-token-data / n−1-samples loss), all tree mutation as idempotent ops + per-workspace `rev`, browser stays an optimistic mirror. Locked decisions, race analysis, 3-phase staging | design, not started — read before touching persistence/fold code |
| `docs/PACK.md` | **Share packs** — bundle checkpoints + default params + workspaces into one portable YAML (`tinkerscope --pack <file\|url>` to consume, `tinkerscope pack export` to author) so a collaborator reproduces a setup against public checkpoints with no local run dirs. Code: `src/tinkerscope/pack.py` + `api/pack_models_store.py` | current |
| `docs/STATIC_SITE.md` | **Static read-only site export + `?w=<pack link>`** (SHIPPED 2026-07-30) — what a published site keeps/hides and why, the size reality (logprobs are ~97% of the bytes), the `data/` layout (each file ≡ an endpoint response), the two index.html rewrites a GitHub Pages subpath needs, the id-vs-pack-source rule, collision handling, and how the chart view travels. Since 2026-07-30 a published site is also a **general reader for anyone's pack** — IndexedDB overlay (localStorage's 5 MB cap made a real workspace uninstallable), gzip + `--logprobs` packs, and open-a-file-from-disk. **`--pack-link` + the loading modal** (2026-07-30) make a published `?w=<id>` SHAREABLE — an id the visitor lacks resolves through `manifest.pack_links` and installs behind a progress box, instead of flashing "not found" and swapping | current |
| `docs/TODO.md` | Roadmap (branching marked done) | current |
| `deprecated/HANDOFF.md` | Original tool-build handoff (Harry's playground → tinkerscope). Build done; file refs predate the `src/tinkerscope/` restructure | deprecated, kept for history |

The durable knowledge HANDOFF.md once held now lives in code docstrings (below)
and in this file's reference section; HANDOFF.md itself is retired.

## Working conventions

- **`tinkpg` CLI changes ship with their docs, in the same commit.** Any new
  command / flag / behavior change updates: README.md §"Drive it from the
  terminal" (command table ONLY — the README is a human pitch, option notes
  belong in the skill; Clément 2026-08-05) AND the CLI skill. **The skills live in this repo at
  `plugin/skills/<name>/SKILL.md`** — `plugin/` is a Claude Code plugin
  (`.claude-plugin/marketplace.json` at the repo root makes the repo its own
  marketplace). **The skill's name depends on how it was loaded**, so don't
  hardcode one: a plugin consumer sees `tinkerscope:cli` / `tinkerscope:guide`,
  while on this box each is a plain user skill — `~/.claude/skills/tinkerscope-{cli,guide}/SKILL.md`
  is a *file-level* symlink to the repo file, so the names are `tinkerscope-cli` /
  `tinkerscope-guide` and edits are live with no reinstall. (The directory name
  wins over frontmatter `name:`, which is why `name: cli` still registers as
  `tinkerscope-cli`.) A new skill needs its dir + link made by hand, as before.
  **`plugin.json` declares NO `version` on purpose** — Claude Code keys the
  plugin cache by `version` when present and by COMMIT SHA when absent, so
  declaring one strands consumers on a stale cache until someone bumps it (that's
  what claude-lab's disabled post-commit hook was papering over, at the cost of
  doubling the repo's commits). `claude plugin validate --strict` warns about the
  omission; that warning is the intended trade, and Anthropic's own feature-dev /
  code-review / frontend-design omit it too. Touch `docs/API_CONTRACT.md` too if
  the HTTP surface changed. (Checklist is also in `cli.py`'s module docstring.)
- **UI behavior changes ship with the human-facing docs, in the same commit.**
  Two twins to keep in sync: the in-app `?` modal
  (`web/src/lib/HelpModal.svelte`) and the guide skill
  (`plugin/skills/guide/`). Both describe the BROWSER to a person; the cli skill
  (`plugin/skills/cli/`) describes the CLI to an agent. Smoke:
  `tests/small-smokes/browser_help_modal.py`.
- **Tooltips are ONE short line (~70 chars).** A fat tooltip renders as an ugly
  slab over the UI and nobody reads it twice. Mechanism / modifier tables /
  caveats go in the `?` modal instead. Full rule + the `use:tip`-over-`title`
  part: `lib/tooltip.svelte.ts` and the frontend map below.
- **Adding a scope/filter? Enumerate the INSTANCE-WIDE stores it must narrow.**
  Most state here is per-scan-root and global to it — pins, prefs (incl. the
  mirrored `chart_view`), highlights, the OpenRouter list, `pack_models` — and none
  of them knows about a workspace. So a feature that filters by workspace filters
  the workspaces and silently carries everything else along. That shipped once
  (`site export --workspace` published pins, with their local `dataset_path`, and
  chart state belonging to the workspaces it had just excluded — review caught it by
  SEEDING a state dir and probing, not by reading). Pins in particular can't be
  scoped at all: they carry no workspace id. Same drill for a new export/share path:
  seed something private, run the curated flow, grep the output for it.
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
- **Static-site export** (what `data/` holds, the panel-ref rewrite, and the two
  index.html rewrites a GitHub Pages subpath needs — absolute asset refs AND the
  router's `base`, or the SPA 404s its own route): `src/tinkerscope/site_export.py`
  — module docstring. Its invariant: **each `data/*.json` is shaped like the endpoint
  it stands in for**, so the frontend's static transport has no special cases. Fails
  loudly if SvelteKit's bootstrap shape changes rather than shipping a broken site.

## Frontend map (`web/` — Svelte 5 / SvelteKit SPA)

Read this before a UI task instead of Exploring `web/`. The UI is a single-route
SvelteKit SPA under `web/src`. Three kinds of file, by suffix:

- **Stores** — `*.svelte.ts` exporting a class instance as a singleton (runes in
  a module; this is the house pattern). Reactive `$state` fields read/written
  across the app:
  - `lib/state.svelte.ts` → `live` — mirrored shared `PlaygroundState` (selection/
    params) + per-panel **streamed sample buckets**, both driven by the
    `/api/state/events` SSE. The render bus. `live.connected` drives the topbar
    dot and DEGRADES (EventSource onerror + a 35s heartbeat watchdog over the
    server's 15s pings) — it is the bus link to the tinkerscope backend, not a
    claim about the tinker upstream. Smoke: `browser_state_reprime.py` §4.
  - `lib/workspaces.svelte.ts` → `ws` — owner of the per-panel **branch
    trees** + persistence + the external-fold reconcile. The workspace model.
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
    seeded at fold time by the chat store, reset on every workspace
    transition. Consumers: ChatMessage's token view + raw-meta disclosure,
    ChartModal first-token mode (fetches the picked turn only).
  - `lib/chat.svelte.ts` → `chat` — the **generation-fire lifecycle**: POST
    `/api/chat`, drain, fold under the user node, per-panel abort controllers +
    the live-bucket prefill color. UI-agnostic — the caller (+page) passes a
    `ChatParams` bundle + a resolved `ChatModelField`, so it never touches the
    sampling UI. +page keeps thin glue (`paramsBundle`/`resolveModelField`/a
    `fireOne` wrapper) over it. `stopGeneration(panel?)` has always been
    per-panel-capable (it cancels by the bucket's `chat_id`); since 2026-08-03 the
    UI uses that — ChatMessage renders a `[data-testid="stop-panel"]` chip ON the
    streaming turn (after the text for n=1, on the progress strip for n>1, plus
    +page's pre-first-token placeholder row), wired to `onStop`. Placement is
    load-bearing: panels follow-scroll while streaming, so a stop in the row HEAD
    is off-screen exactly when wanted — hence the tail position and the
    `.samples-progress.running` sticky. The sidebar `.btn-stop-sidebar` stays the
    all-panels one. Smoke: `browser_stop_generation.py` scenario C.
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
    handlers as `branchOps.<name>(...)`. Includes `switchThread(ts)` — the
    cross-panel THREAD jump (see ThreadSwitcher below): switches every panel
    holding a same-content root sibling, never force-aligns the rest.
  - `lib/highlights.svelte.ts` → `highlightStore` + `highlightsOn` — user-defined
    render-time coloring rules + persistence, and the sidebar's MASTER Off/On.
    The master is a GATE, never a bulk edit: it writes no rule's `enabled`, so
    flipping it back On restores exactly the set that was painting (a bulk
    disable would destroy the state you want back). localStorage, like
    `logprobView`/`thinkingView` — a viewing preference, out of the wire/disk
    contract and out of share packs; default ON.
    **Read `colorRules()`, not `highlightStore.rules`, from anything that COLORS
    text** — that's the one place the gate applies (`render.ts`, and the
    token-probability match tint in TokenLogprobs / TokenHeatOverlay / +page's
    "Color by match" picker). Deliberately NOT gated: `ChartModal`, which buckets
    samples by rule rather than coloring text, lives in its own modal, and has
    per-rule include/exclude chips already — killing an analysis surface from a
    sidebar switch about colors is action at a distance. Smoke:
    `tests/small-smokes/browser_highlight_master.py` (pins the gate-not-bulk-edit
    property against the server's rule state).
  - `lib/logprobs.svelte.ts` → `logprobView` + `logprobHighlight` — the sidebar
    **"Token probs"** display toggle (localStorage-persisted). Display-only:
    capture is the server default for native tinker sampling, so flipping it on
    works retroactively on stored turns. THREE states, not two —
    `mode: 'off' | 'overlay' | 'stream'` (`enabled` = `mode !== 'off'` for the
    consumers that only care whether some token view is up): `overlay` keeps the
    markdown and paints the heat under it (`TokenHeatOverlay`), `stream` swaps
    the body for the raw token dump (`TokenLogprobs`). The legacy `'1'` value
    migrates to `overlay` — it meant "show me the tokens", which is the same
    information without giving up the prose. `logprobHighlight` holds the ≤2
    highlight-rule ids chosen for the **"Color by match"** picker (also
    localStorage; newest-2-win): non-empty ⇒ TokenLogprobs tints tokens by
    `highlightMatchProb` instead of surprisal + colors popover alternatives by
    their match.
  - `lib/thinking-view.svelte.ts` → `thinkingView` — the sidebar **"Thinking
    blocks"** Folded/Open toggle (localStorage, like `logprobView`; shown in
    read-only too — a published CoT page is where a reader most wants them open).
    It's the DEFAULT fold state, not a lock: `ChatMessage` passes it as the
    `<details open>` value, so a fold the user clicks keeps its own DOM state
    until the preference flips again (`reasoningOpen` is `boolean | null`, null =
    untouched ⇒ the preference governs). The last assistant turn stays open
    regardless — that rule predates this and is unchanged.
  - `lib/scroll.svelte.ts` → `panelScroll` — **the only scrollTop writer**: the
    per-panel FOLLOW (streaming, stick-to-bottom gated) / PRESERVE (tree
    mutations keep position) / SNAP (send, workspace open) / REVEAL
    (keyboard focus moved off-screen → minimal container-only scroll) policy.
    Its module docstring records why (the old global bottom-pin = the scroll
    flicker). New scroll behavior goes through this store, never inline.
- **Pure logic** — plain `.ts`, no Svelte/DOM, unit-testable (some have
  `*.test.ts`):
  - `lib/tree.ts` — all branch-tree ops (activePath, fold, regen, edit, delete,
    cycle, siblings) + `threadStarts(trees)`, the cross-panel union of root
    THREADS (branch-from-start first messages, identity = trimmed content —
    threads are per-panel, so each entry records which panels have it). The
    single source of branching truth. **Has `tree.test.ts`.**
  - `lib/model-sel.ts` — the `openrouter:`/`base:`/`ckpt:` sentinel encoding
    (prefixes, predicates, id extractors) for a panel's model selection, plus
    `runSamplerPath(checkpoints, name)` — what the copy-id button hands out for a
    DISCOVERED-run panel. It duplicates `routes/chat.py:_resolve_checkpoint`'s
    rule (named ckpt must exist AND have a sampler path; no pick ⇒ `final`, else
    the last one with a path) so the button can't copy a path other than the one
    that produced the turns on screen — if that backend rule moves, move this.
    **Has `model-sel.test.ts`**; browser smoke `tests/small-smokes/browser_run_ckpt_copy.py`.
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
    `tests/small-smokes/browser_label_trunc.py` (now the PickerDropdown-trigger
    single-label site — sibling LIST rows moved to the diff view below).
  - `lib/label-diff.ts` — `diffLabels(labels)`: the "smarter" layer over
    tail-preserve for the case it can't handle — sibling runs that share BOTH ends
    and differ only MID-name (`…_base_ed_sheeran_…` vs `…_instruct_…`, which a tail
    cap renders identically). Clusters the visible labels by first segment, then
    positionally votes over aligned indices: cluster-constant runs collapse to a
    dimmed `…`, every varying segment shows in full (reaches interior constants a
    prefix/suffix scheme can't; degrades to prefix-only elision on ragged families).
    Peels the `⊘/?/◆/◇/↗` status-icon prefix so unavailable runs still cluster.
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
    striped combo; its `limit` arg caps matching to the first N chars of the
    matched text, applied PER PART so "first N of the response" survives the
    `either` scope) + `chartByAnswers` (legacy exact-match histogram) +
    `chartByFirstToken` (the MODEL's probability distribution over the first
    generated token, from stored `token_logprobs` — segment pct = model prob,
    count/sampleIdx = the empirical side) + label helpers. `chartByFirstToken`
    takes `FirstTokenOpts {excluded, added, groups, renormalize}`: it works on
    **units** (a token OR a merged group via `ftGroupKey`), so exclude (mass +
    samples fold into the grey rest, which grows — NO renormalization, bar stays
    on absolute probs; `renormalize:true` instead drops the grey rest entirely
    (top-K tail + any excluded units) and rescales the named units to sum to
    100%), **add**
    a recorded-but-hidden token (surfaced from the rest — `AddedToken`, its p
    sourced from stored logprobs, NOT a model call), and **merge** (drag tokens
    into one color, prob+count summed) all compose. Also `buildChartSources` —
    the modal's bar-set builder, where the two INDEPENDENT splits compose:
    `think: 'split'` (a think and a no-think bar per panel over DISJOINT samples,
    each its own 100%) × `scopeSplit` (rules mode's response|thinking pair over
    the SAME samples). Each source carries `panel` + `pop` so the SVG layout can
    group bars by panel (two panels can share a model label) and sum a group's n
    over distinct populations. **Has `chart.test.ts`**
    (exclude/renormalize/add/merge + the split-composition cases); browser smokes
    `tests/small-smokes/browser_chart_{firsttoken_ops,rules}.py`.
  - `lib/chart-view.ts` — persistence for the chart modal's VIEW state, split by
    scope: the three how-you-look-at-it picks (mode / match scope / thinking
    filter) are GLOBAL (last-used carries to a workspace you've never charted),
    everything question-specific (turn, include-folded, excluded rule chips, the
    first-N-chars match cap, first-token exclusions / merges / added tokens) is
    PER WORKSPACE, 40-entry
    LRU by save time. localStorage is the LIVE store (sync, no latency on a toggle),
    MIRRORED into server prefs under `chart_view` (debounced 800 ms via the
    `setChartViewMirror` seam +page injects) so a **static site export** can carry the
    view its author set up — the Python exporter can't read a browser's localStorage,
    and a published site is a curated presentation. Merge on load: a browser that
    never charted takes the published blob wholesale, anything local always wins
    (`mergeStores`). Still not a workspace field, so still out of the wire/disk
    contract and out of share packs (only `prefs.json` carries it). **Has
    `chart-view.test.ts`** (sanitize / prune / round-trip / merge + hydrate against a
    fake localStorage).
  - `lib/token-search.ts` — `normalizeForMatch` / `matchKind` / `searchStoredTokens`:
    the first-token add-search's tiered matching (exact ‹ prefix ‹ contains) with
    space-marker normalization (leading space / ▁ / Ġ ≡ bare, case-insensitive),
    over tokens already recorded for the turn (top-K alts + sampled first tokens).
    **Has `token-search.test.ts`.**
  - `lib/token-logprob.ts` — token-logprob display math: `prob`/`pctLabel`,
    `surprisalAlpha` (the single-hue heat tint — alpha ∝ -logprob), `displayToken`
    (whitespace glyphs), `firstTokenDist` (one panel's position-0 distribution:
    newest sample's top-K as reference + sampled outliers; flags `mixed`), plus
    the **highlight-match coloring** alternative to surprisal: `highlightMatchProb`
    (mass over a position's captured top-5 candidates whose text `ruleMatches` a
    highlight rule — a lower bound, top-5 only) + `matchTintBackground` (1 rule →
    flat tint; 2 → a top/bottom split band; alpha = √prob × 0.42 — a gamma-0.5
    ramp so a 1% match still reads at 10% opacity, peaking at the standard 0.42
    highlight opacity, prob 0 = transparent). `tokenTintColors` is the SINGLE
    answer to "what color is this token" — the ≤2 bands as flat rgba, match-tint
    when a rule is picked else the surprisal heat — so the CSS view and the
    canvas overlay can't drift; `matchTintBackground` is the CSS-gradient
    packaging of the same bands.
    **Has `token-logprob.test.ts`**; smokes `tests/small-smokes/
    browser_token_logprobs.py` (seeded, deterministic) + `…_live.py` (real
    tinker sampling end-to-end).
  - `lib/token-align.ts` — `alignTokens` / `alignChars` / `visibleCoverage`: line
    a RAW token stream up with the text the markdown renderer actually put on
    screen, so the heat can be an OVERLAY on the prose instead of replacing it.
    A two-pointer walk with a bounded resync search; the workhorse is "skip the
    raw char", because the pipeline DROPS (`**`, `#`, backticks, `<think>` tags,
    list markers, link syntax) and essentially never inserts. Failure is local
    and self-healing: an unplaceable token gets `null` and isn't painted, the
    next matching prose resyncs. Trust is `visibleCoverage` — the share of the
    RENDERED text some token claims, NOT the share of tokens placed: a
    syntax-heavy turn scores badly on the latter while every word on screen is
    correctly painted (real turns measure 97–100%). Coverage counts MAPPED
    chars (spans carry `mapped`), never span extents, and a low-density span
    (mostly unclaimed text — the scatter-match signature of a desynced walk) is
    nulled: extents once let a prefill turn's tail-only stream score 0.502 and
    paint garbage one hair past the 0.5 guard. **Has `token-align.test.ts`**
    (one case per markdown construct + the ordering invariants); browser smoke
    `tests/small-smokes/browser_token_overlay.py`.
  - `lib/token-edit.ts` — carrying logprobs across an EDIT (`editedRawText` /
    `logprobsAfterEdit`, called by tree.ts's `editAssistant`). An edit mints a new
    node, but every token before the point where the text stops matching what the
    model wrote was generated under the SAME context, so its logprob is still the
    model's number: the new node inherits the stream up to that divergence, and
    the rest becomes ONE **ghost** entry (`{tid:-1, lp:null, ghost:true}`) — the
    text with no probability, dimmed in the inspector, "no token data" on hover.
    Truncation is the special case where the ghost is empty or a few chars.
    Offsets are computed against the RAW stream (the tokens' own text, tags and
    all) with the reasoning/answer runs located by substring search — never by
    re-assembling the parsed fields, which would mean guessing tag formatting.
    Nothing survives (divergence inside token 0, runs not found) ⇒ no stream at
    all rather than an all-ghost one that looks like evidence. **Has
    `token-edit.test.ts`**; smoke `tests/small-smokes/browser_edit_logprobs.py`.
  - `lib/token-prefill.ts` — the edit-ghost concept mirrored for PREFILL turns
    (`withPrefillGhost`): a continuation's stream starts after the authored
    prefill (the sampler returns generated tokens only), so ChatMessage prepends
    the node's persisted `prefill` as ONE leading ghost
    (`ghostKind: 'prefill'`, "prefilled text" on hover) at DISPLAY time — it
    anchors the aligner (without it the overlay warned "couldn't be lined up"
    on every Continue'd turn) and is never stored, so old turns fix themselves.
    Chart/first-token paths read the raw stored stream and are untouched
    (`firstRealToken` skips a leading prefill ghost). **Has
    `token-prefill.test.ts`**; the prefill scenario in
    `browser_token_overlay.py` pins it end-to-end.
  - `lib/kbnav.ts` — keyboard row-navigation helpers: nav-key set, clamped
    focus-index stepping, the typing-target/modal-open guards. Consumed by
    +page's *Keyboard row navigation* section (click a row → focus ring; ↑/↓
    walk the panel view, ←/→ = the row's ‹k/N› cycler, Esc clears). **Has
    `kbnav.test.ts`**; browser smoke `tests/small-smokes/browser_kbnav.py`.
  - `lib/chat-stream.ts` — `drainSamples`: parse the `/api/chat` SSE into samples.
  - `lib/highlight-match.ts` / `lib/highlight-render.ts` — pure matching + the
    markdown+math+highlight render pipeline. **`highlight.test.ts`.**
  - `lib/render.ts` — store-coupled render entry point (wraps highlight-render).
  - `lib/api.ts` — typed backend client + named-event SSE helper. Its object also
    DEFINES the `ApiClient` type, and picks the transport at module init: HTTP, or
    the baked-file client when running as a static export.
  - `lib/static-mode.ts` / `lib/api-static.ts` — **read-only static site**
    (`docs/STATIC_SITE.md`). Detection is SYNCHRONOUS off a `window.__TSCOPE_STATIC__`
    global the exporter injects into index.html (api.ts must choose a transport before
    any consumer touches it). `api-static` serves each `data/*.json` in place of the
    endpoint it's shaped like, and routes writes to a per-site localStorage overlay.
    ⚠️ **Baked workspaces are immutable** — a write to one is accepted and DROPPED, so
    an incidental layout normalization can't shadow published content; only
    visitor-installed (pack-link) workspaces persist edits. `readOnly` gates the UI in
    `+page.svelte` / `ChatMessage.svelte` at the MARKUP level (a hidden control is
    absent, not disabled). `lib/node-split.ts` is the browser mirror of
    `workspace_store.split_node`, so a client-installed pack produces the same
    light-node + blob shape the server would (**has `node-split` coverage via the
    static smoke**).
  - `lib/OpenLocallyModal.svelte` — the **read-only badge is a button**, and this is what
    it opens: the `uvx … --pack <url>` command that gets a reader from reading to
    sampling. The URL comes from `site export --pack-url` (baked) or from the `?w=` link
    a visitor installed from (overlay key `ws.source.<id>`, per-workspace wins). With
    NO url it says the command starts an empty tinkerscope and won't reproduce the page,
    rather than printing something that looks like it should work. Smoke:
    `browser_open_locally.py` (exports the same site twice to pin both branches; also
    pins the per-panel copy-the-sampler-path button that replaced `· loose sampler`).
  - `lib/overlay-store.ts` — the **static site's write overlay**: an in-memory map
    hydrated once from **IndexedDB** (localStorage until 2026-07-30 — its ~5 MB/origin
    cap made a real workspace impossible to install, and it failed QUIETLY: the write
    threw, was caught + warned, and reads then came back empty. Measured here:
    localStorage 4.98 MB vs IndexedDB 6442 MB). Sync reads / async flush, so the ~30
    read sites in `api-static` are unchanged; `readOverlay` **clones on read** to keep
    the copy-per-read contract localStorage's `JSON.parse` gave for free. Every
    `staticApi` method awaits the hydrate via ONE wrapper (`gated`), never 30 individual
    awaits. Smoke: `tests/small-smokes/browser_pack_big.py` (**verified to fail on the
    pre-fix build** — and note that smoke and `browser_static_site` build their own site,
    so they honour `TSCOPE_APP_DIR` or `--baseline` silently tests the working tree).
  - `lib/pack-logprobs.ts` — pure mirror of `pack.py::restore_logprobs`. In a PACK,
    logprobs travel as a compact JSON **string** under `token_logprobs_json` (native YAML
    lists measured 157 MB vs 107 MB, and the distinct name removes any is-it-a-string
    question); everywhere else they are the parsed list. **Has `pack-logprobs.test.ts`**,
    because the same file installs through either the Python or the browser path.
  - `lib/pack-source.ts` / `lib/pack-install.ts` / `PackInstallModal.svelte` —
    **`?w=` takes a pack path or URL**. The discriminator is free: store ids are
    `^[A-Za-z0-9_-]+$`, so any value with `/ : .` is a source (**has
    `pack-source.test.ts`**). Live → `POST /api/pack/apply` (also the only way a
    local PATH is readable); static → fetch + `js-yaml` (dynamic import) + overlay
    install. Two-phase: preview reports which deterministic ids collide, then the modal
    asks — but only when it has to (`canInstallUnprompted` in +page). **A collision always
    asks** (replace vs keep both) since overwriting discards what's there. A NON-colliding
    install asks on a LIVE instance only — there `?w=<path>` makes the server read the
    filesystem into the real on-disk state dir, and any page can navigate a browser to
    localhost. A static site just installs: per-site IndexedDB, deletable, can't touch the
    baked workspaces, and pack content is HTML-escaped before render (`renderMarkdown`), so
    what's left is attribution rather than compromise — judged not worth a modal (Clément,
    2026-07-30, after pushing back on my over-broad first answer). ⚠️ A **query param** to
    skip it is the one shape to refuse: the URL author controls it, so it deletes the check
    instead of configuring it. Any future opt-out must be author-controlled (export-time or
    launch-time). Renaming keys on the derived ID only, via the ONE
    shared rule `bumpUntilFree` (mirrored by `pack.py::_dedupe_conflicting` — a review
    caught them diverging, so both sides have tests). Then the URL is rewritten to the
    plain `?w=<id>` so a reload never re-installs. `&open=<ws-id>` picks the one to open.
  - `lib/types.ts` — TS types mirroring the backend (see `docs/API_CONTRACT.md`).
  - `lib/tooltip.svelte.ts` — the `use:tip` tooltip action (+ `tipHost`, which
    registers the one rendered box so a wide tip gets clamped into the
    viewport). **House rule for tooltip TEXT, repo-wide: ONE short line naming
    what the control does (~70 chars).** Mechanism / modifier tables / caveats
    belong in the `?` modal — the tip is hover-instant and lands on the way to
    clicking something else. Prefer `use:tip` over a native `title=` on the main
    screen (sidebar / chat area / composer); `title` survives inside modals only.
- **Components** — `.svelte`:
  - `routes/+page.svelte` — **the workspace component**: wires every store +
    handler to the markup. Still the biggest file (~2.2k lines); organized by
    `// ── Section ──` banner comments — **`grep '// ──' routes/+page.svelte`
    for the in-file table of contents** rather than scrolling. Notable sections:
    *Send a chat* (`sendMessage` + the `fireOne` wrapper — the core send path;
    the fire/abort/fold machinery itself is in `lib/chat.svelte.ts`), *Chat-thread
    branching* (edit/regenerate/delete/cycle/select — the largest cluster),
    *Workspace rendering* (`panelView`/`bucketTurn` — overlays the live bucket
    on the tree's active leaf), *Panel lifecycle* (add/remove panels),
    *Keyboard row navigation* (the ONE focused row + arrow-key handler over
    `lib/kbnav.ts`), *Workspace ↔ URL sync*, *Session persistence*,
    *Lifecycle* (`onMount`).
    Markup order: sidebar → chat area → input bar → the modal components below.
  - `lib/Modal.svelte` — shared modal chrome (overlay, header, close,
    click-outside, Escape, body slot). Every modal wraps this; `modalStyle`
    overrides the box width per modal.
  - `lib/ActionMenu.svelte` — the row-overflow ⋯ menu: a `.btn-act` trigger +
    position:FIXED floating panel (escapes the column's overflow clipping, like
    the old send-to popover it replaced), with outside-click / Escape / scroll-
    reflow handling. Items come in via the children snippet (styled by chat.css
    `.row-menu-item`), which receives `close()`; the `resetKey` prop closes a
    menu when the UNKEYED chat rows hand the mounted instance a different node.
  - `lib/HelpModal.svelte` — the `?` modal (sidebar icon row): Guide + Keys tabs
    describing the UI to a HUMAN. Its prose twin is the `tinkerscope:guide`
    skill; both update in the same commit as any UI behavior change (see
    §Working conventions). Every button it names is drawn with its REAL glyph via
    `Icon.svelte` (a hand-copied path would drift) — the Guide carries button
    legends for the row toolbar / sample cards / sidebar, and Keys shows the icon
    next to each modifier. Also fronts the agent-skill pitch + a FOLDED
    plugin-install block (Claude Code / Codex / npx skills) that twins README
    §"Agent skills". Smoke: `browser_help_modal.py`.
  - `lib/Icon.svelte` — the shared SVG glyph set (`<Icon name="edit" size={13} />`).
    One dispatch table for every row-toolbar + sidebar icon, so the toolbar and the
    Help modal can't disagree. Shift-variants are their own names (`replace`,
    `edit-copy`, `trash-all`, `tag-quick`). ⚠️ Parent-scoped CSS can't reach the
    glyph — style it via `:global(svg)` (see `.theme-toggle.refreshing`).
  - `lib/ChartModal.svelte`, `lib/TagModal.svelte`, `lib/DatasetModal.svelte`,
    `lib/SlideshowModal.svelte`, `lib/OrManagerModal.svelte`,
    `lib/TinkerPickerModal.svelte` — the six workspace modals. Each owns its body
    + specific styles; the parent passes data in and gets results via callbacks.
    ChartModal is the smart one: it receives per-panel per-turn samples
    (reactive; live-updates mid-stream) and owns mode toggle / turn picker
    (defaults to the LATEST turn) / match-scope (incl. `split` = a
    response|thinking bar pair) / per-rule include-exclude chips (drop a rule the
    prompt makes ubiquitous from the bucketing; chart-only) / the "first N chars"
    match cap (opening-only matching for response-tag rules — the inspector dims
    what fell past the cut, `.chart-cap-line` / `.chart-cap-rest`) / the thinking
    filter — all samples, one population, or `split` (a think and a no-think bar
    over disjoint samples; composes with the match-scope split for up to 4 bars
    per model), shown only when the picked turn mixes both /
    click-a-segment-to-inspect.
    Third mode "first token": the model's OWN probability distribution over the
    first generated token (needs stored `token_logprobs`; disabled otherwise). In
    that mode the legend becomes an **interactive chip row**: click a chip to
    exclude/re-include (its mass + samples fold into the grey rest, which grows —
    absolute probs, no renormalization; a `renormalize` checkbox (always shown)
    instead drops the grey rest and rescales the shown tokens to 100%), drag one
    chip onto another to **merge** into one color (bespoke onto-drop DnD, not the
    gap-shaped `lib/drag-reorder`), and a search box **adds** a recorded-but-hidden
    token (from stored logprobs — `token-search` + `chart`'s `added`, no model
    call). ALL of this state is module-scoped and PERSISTED via `lib/chart-view`
    (global picks + per-workspace tweaks — see above), so a reopen or a reload
    lands you back where you were; the live `mode` falls back off a persisted
    `firsttoken` when nothing carries logprobs, without losing the choice.
    ⚠️ **The inspector must stay isolated from the plot's re-derivation**: `data`
    is rebuilt on EVERY streamed sample and bars come and go mid-batch (the
    streaming pseudo-turn +page appends then retires at fold; a panel gaining its
    first sample; the think split's second bar). So the inspected bar is
    addressed by a stable ref (`panel` id + `pop` + `sub`, resolved to an index at
    render time) — an index silently re-points at a DIFFERENT bucket — and the
    per-sample thinking folds live in `thinkOpen` state, not in the `<details>`
    DOM, which a recreate would reset. Live smoke (real free-router sampling):
    `tests/small-smokes/browser_chart_live_inspect.py` — verified to FAIL on the
    pre-fix build and pass after, 2026-07-29.
    Deterministic smokes (seeded tree, no sampling):
    `tests/small-smokes/browser_chart_rules.py` (rules) +
    `browser_chart_firsttoken_ops.py` (exclude / add / merge).
  - `lib/ChatMessage.svelte` — one chat row (committed node OR live bucket turn)
    + its per-row toolbar. Every action is a real icon button in ONE
    priority-ordered `OverflowRow` (**Raw leads — very left, never folds**
    per Clément; then the edit cluster; copy message/workspace,
    send-branch→panel, discard-others, **Copy node id** last — the id is the
    `tinkpg` CLI's `--node` addressing handle, shown in the button's tooltip).
    When the row is too narrow the tail folds (clipped, folded by default)
    behind a chevron that expands it BELOW as 1+ more lines of the same
    buttons; when everything fits the chevron hides but KEEPS its slot
    (conditional rendering would shrink the wrap on fold → a hysteresis band
    of stuck folds).
    Send-to stays a popover (`ActionMenu`) because it's a labeled panel list.
    With `logprobView` on, an assistant body with `token_logprobs` renders
    `TokenLogprobs` instead of markdown (turns without data wear a "no token
    data" pill). Toolbar smoke (seeded, token-free):
    `tests/small-smokes/browser_row_toolbar.py`.
  - `lib/TokenLogprobs.svelte` — token probs, `stream` mode: the raw generated
    token stream (thinking tags and all — exact token boundaries beat markdown
    here), each token tinted by surprisal. When ≥1 rule is picked in the
    sidebar's "Color by match" (`logprobHighlight`), the surprisal tint is
    replaced by a per-token match-prob band (1–2 rules; `matchTintBackground`)
    on an inner `.tok-core` span that EXCLUDES the token's edge whitespace (a
    BPE token carries its leading space; tinting it reads as highlighting the
    gap between words — Clément, 2026-08-03). Surprisal keeps whole tokens (a
    ribbon, not a highlight); the overlay applies the same match-mode trim.
  - `lib/TokenHeatOverlay.svelte` — token probs, `overlay` mode (the DEFAULT
    on-state): the same tints painted UNDER the normal markdown, so the prose,
    the thinking fold and the highlight rules all stay as they are. Aligns the
    stream to the DOM text via `lib/token-align`, turns each token's span into a
    `Range`, and fills its client rects on a canvas. Two load-bearing choices,
    both learned against real turns:
    **one canvas PER prose container** (`.sample-reasoning` / `.message-content`
    / `.sample-content`), inserted as that container's first child at
    `z-index: -1` — a row-level canvas painted *behind* `.sample-reasoning`'s
    OPAQUE background, so the whole thinking block came out flat; negative-z
    paints after the container's own background and before its text, which is
    the highlighter order. It also means the tint scrolls and clips with the
    reasoning fold (`overflow-y: auto`) for free.
    And **canvas, not spans** — a long turn is ~1000 tokens and this remeasures
    on every resize (ResizeObserver on the row) and on every `{@html}` swap.
    ⚠️ The canvas is created imperatively, so its CSS lives in global `chat.css`,
    not in the component — and an `{@html}` re-render wipes it, which is why
    `canvasFor` re-creates rather than caches. Hover hit-tests the cached rects
    (no caret API) and opens the shared `TokenPopover`. Coverage below 50% ⇒
    paint nothing and say so. A GHOST token (`token-edit.ts`) has no probability,
    so it gets a dashed underline and no fill — leaving it merely untinted would
    read as a CONFIDENT token, the opposite of the truth. Smoke:
    `browser_token_overlay.py`.
  - `lib/TokenPopover.svelte` — the token hover card (probability + top-K
    alternative bars, alternatives tinted by which selected rule they match),
    shared by both token views so "what a token tells you" has one definition —
    including the GHOST branch (`token-edit.ts`): no percentage, "no token data
    — edited text", handled here once instead of per view.
  - `lib/Typeahead.svelte` — the type-to-filter combobox (used by the OpenRouter
    + Tinker picker modals, and as the panel body of `PickerDropdown`). Item
    shape: `lib/picker.ts`'s `PickerItem` (`sub` = the secondary line, defaulting
    to the id). Rows render via `DiffLabel` when the visible siblings form a
    diffable family (`diffLabels(visibleLabels)`), else `TruncLabel`. Search still
    matches the full label, so filtering is unaffected by the compact display.
    Filtering is TIERED (`lib/fuzzy` `tieredFilter`): exact substring primary,
    typo-tolerant fuzzy fallback only on zero substring matches — with a subtle
    "no exact matches — close matches:" note when the fuzzy tier is showing.
    Each row carries `data-id` (how browser smokes address one).
  - `lib/PickerDropdown.svelte` — select-like trigger button + floating panel
    wrapping `Typeahead`; **every sidebar picker** (click → type to filter, no
    separate "Filter models…" textbox): the per-panel model picker inside
    `.model-block`, and the **workspace picker** (`.ws-picker`, whose
    `data-ws-id` mirrors `ws.activeId` — the smokes' oracle, drive it with
    `tests/small-smokes/_ws_picker.py`). Both wear `.picker-dropdown-trigger`,
    so a smoke targeting one must scope by its wrapper.
  - `lib/HighlightRules.svelte` — the highlight-rules editor UI, and the header
    that carries the master Off/On (`highlightsOn`) next to `+ new`. While it's
    off the rules stay listed, dimmed (`.hr-root.master-off`) and still editable —
    a hidden list would read as "they're gone".
  - `lib/ThreadSwitcher.svelte` — the composer-row **cross-panel thread jump**:
    a popover (next to the ⑂ branch-from-start toggle) listing
    `threadStarts(convo.trees)` — every root thread across all panels with its
    panel-coverage count (● active everywhere it exists / ◐ somewhere). Picking
    one calls `branchOps.switchThread`; renders only when ≥2 distinct threads.
    Smoke `tests/small-smokes/browser_thread_switcher.py` (seeded, token-free —
    covers divergent thread sets, no-force-align, save/reload persistence).
  - `lib/TruncLabel.svelte` — the middle-ellipsis label: a two-span flex trick
    (head clips with `flex:0 1 auto`, tail always shows) over `splitTail`, plus
    the full-name `use:tip` tooltip backstop. The SINGLE-LABEL renderer — the
    `PickerDropdown` trigger and +page's `.column-title` / `.send-chip` — plus the
    fallback for `Typeahead` rows a diff family doesn't cover. So two runs
    sharing a long prefix stay distinguishable at any width.
  - `lib/DiffLabel.svelte` — the diff-view label: renders `label-diff`'s compact
    parts (varying segments at full emphasis, cluster-constant anchors + `…` dimmed)
    with the same `use:tip` tooltip / aria-label affordances as TruncLabel; only
    the leading family anchor may shrink under width pressure. Used for
    `Typeahead` rows when `diffLabels` returns a render for the row.

Cross-component CSS utility classes (`.sidebar-label`, `.btn-new`,
`.backend-error`, `.seg-toggle`/`.seg-btn`, …) live in **global `app.css`** —
scoped `+page.svelte` styles don't reach extracted components, so shared classes
must be global. (`.seg-toggle` moved there when HighlightRules grew one; that's
the pattern — move it, don't clone it.)

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
  **Sampler weights are NOT windowed** (settled 2026-07-21): a checkpoint persists
  until it expires (per-ckpt TTL; `expires_at=None` = never) or is deleted. Two
  failure modes show greyed-out / 404: (a) base model no longer served (e.g.
  `Qwen/Qwen3-30B-A3B-Base`); (b) sampler weights gone (expired/deleted).
  Discovery checks both via the REST `list_user_checkpoints` sweep
  (`discovery.get_servable_paths`), so **`sampleable` can be trusted**. ⚠️ Never
  use the oai `GET /v1/models` listing for availability — it's hard-capped at the
  ~20 newest checkpoints while the inference endpoints serve unlisted paths fine;
  trusting it falsely greyed older-but-live runs (the "rolling window" theory in
  older notes came from this cap). Find a live run via `GET /api/tinker-models`
  or `tests/small-smokes/_smoke_models.{LIVE_RUN_ID,pick_servable_run}`.
  **Live as of 2026-07-21:** all 2026-06 weird-personas runs (42 of 54 discovered);
  the April negation_neglect runs are genuinely gone.
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
- **Python**: `uv run pytest -q` (no remote calls — capabilities probe is stubbed)
  and `uv run ruff check` (whole repo incl. `tests/`, kept at 0 — the pre-commit
  hook only lints STAGED files with `--select F`, so a repo-wide run is what
  catches latent errors in smokes nobody has touched).
- **Browser smokes — use `scripts/smoke.sh`** (builds web/, launches a throwaway
  instance, runs the token-free set SERIALLY under a lock, skips the known-stale
  ones by name). Smokes must never run concurrently: `browser_state_reprime.py`
  kills and restarts a server mid-run, so a parallel smoke fails with a bogus
  error — on 2026-07-24 that produced a false "the fix doesn't work" on the
  cross-tab corruption smoke. `scripts/smoke.sh --fresh` for the empty-state set
  (`chart_rules` wants it); `scripts/smoke.sh <name>…` to run a subset.
  **`--baseline <ref> <smoke>` runs the WORKING TREE's smoke against the app at
  `<ref>`** (throwaway `/var/tmp` worktree, built + served on the same isolated
  port, self-reaped). Use it on every smoke you write for a bug you just fixed:
  until you watch it FAIL without the fix it proves nothing, and on 2026-07-29 two
  successive versions of one smoke passed for the wrong reason. Read that run's
  log — its exit code only covers setup. ⚠️ A SELF-HOSTING smoke (spawns its own
  server / builds its own site) must resolve its checkout via
  `os.environ.get("TSCOPE_APP_DIR") or <repo root>`, or `--baseline` silently
  exercises the working tree and passes — that false-OK happened on
  `browser_state_reprime` (2026-08-03) and nearly on `browser_pack_big` before it.
- **Isolated instance for testing** — NEVER test against the user's live server
  or `~/.local/state/tinkerscope`; run `scripts/dev-isolated.sh [--port N] [SCAN_DIR ...]`
  instead: it snapshots the real state into a throwaway `XDG_STATE_HOME` (realistic
  workspaces/prefs as fixtures, live registry stripped) and launches from this
  checkout. Build `web/` first; agents launch it with run_in_background.
- **Browser smokes** (`tests/small-smokes/browser_*.py`, Playwright): point them at
  an isolated instance. ⚠️ Playwright's `.click()` AUTO-SCROLLS off-screen targets
  into view — when asserting scroll behavior, use programmatic `element.click()` /
  keyboard dispatch or the auto-scroll fabricates false scroll-position failures
  (cost a verifier two false rewrites once; see `browser_kbnav.py` for the pattern).
  ⚠️ `get_attribute("style")` returns the CSSOM-SERIALIZED string, not what the
  code wrote — Svelte sets the style *property*, so `linear-gradient(to bottom,
  X 0 50%, …)` reads back as `linear-gradient(X 0px, …)` and an alpha of `0.252`
  as `0.25`. Assert colors NUMERICALLY (regex the channels out, compare with a
  tolerance) — substring matching on an emitted color is a false failure waiting
  to happen (see the ramp block in `browser_token_logprobs.py`).
- Dev-HMR loop + more smoke commands: `docs/HANDOFF_BRANCHING.md` §6.
