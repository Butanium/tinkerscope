# HANDOFF — N-way model comparison workspace

Written 2026-06-22 at the end of a long branching+comparison-UX session, as the
deliberate handoff before a context reset. The next session should read this,
then **ask Clément the open decisions below before building.**

## 0. Where we are right now

- **Committed:** `d25f0ac` on `main` — "Conversation branching + multi-model
  comparison UX". This bundles the entire branching feature (designed/verified in
  an earlier context, never committed until now) **plus** today's comparison-UX
  batch **plus** Quill's wandb backend change. Working tree is clean.
- **Dev server is running** (HMR): backend `:8770`, vite **`:5180`** (the URL to
  open), scanning `~/projects2/weird-personas`. Launched with:
  `DEV_BACKEND_PORT=8770 DEV_FRONTEND_PORT=5180 ./run.sh ~/projects2/weird-personas`
  (log: `/tmp/ts-dev/dev.log`). The old prod server on `:8809` (negation_neglect)
  is **stale** — ignore or kill it.
- **All green at commit:** 33 pytest · 30 tree unit tests (`node web/src/lib/tree.test.ts`)
  · 0 svelte-check errors.
- **⚠️ Sampling the discovered runs 404s right now** — Tinker has GC'd the sampler
  weights for BOTH the negation_neglect AND the weird-personas runs (Scribe verified
  `tinkpg chat q_nk` → "Weights not found"). So *any* generation against a scanned
  run (regenerate, n-samples, the new "+" continue) 404s on :5180 today. **Test
  generation via OpenRouter reference models** (weights-independent) until there are
  fresh runs. Tree ops, branching, the filter, persistence — all testable without
  sampling.

## 1. What this session built (so the next session knows what exists)

Branching (the big feature, now committed): per-panel **branch tree** is the
single read source; `messages`/`compare_messages` stay a write-only active-path
echo for the CLI. Regenerate/edit/n-samples become cycle-able ‹k/N› siblings;
named conversations via a dropdown, each with its own `system_prompt`. Lives in
`web/src/lib/tree.ts` (pure, panel-agnostic, 30 tests), `conversations.svelte.ts`
(store), `api/routes/conversations.py` (flock'd CRUD). Contract: `BRANCHING_DESIGN.md`.

Today's comparison-UX batch (all live on :5180):
- **Enabling compare duplicates** the current thread into both panels (was: wiped both). `duplicateToCompare()` in the store.
- **Type-to-filter** on the sidebar model picker (`modelFilter` + `matchModel(...)`), matches name/id/base_model/wandb_project/renderer; selected option always kept.
- **Session persistence** — model selection + sampling params cached to the on-disk prefs store (`last_session` key), restored on a fresh process (`restoreSession()`). **Global last-session — to be reworked to per-conversation (§3, resolved).**
- **Regenerate + Regenerate-all** (compare); **shift+regenerate = replace current branch in place** (`regenReplace` in tree.ts); **shift+delete = delete all sibling branches** (`deleteSiblings`); shift swaps the button icon+tooltip (regen→replace, edit→copy, delete→delete-all). `shiftDown` tracked via window key listeners.
- **Per-panel "continue this panel" composer** (bubble at each panel's thread tail, compare-only) + **per-panel concurrency**: a generation in one panel no longer disables the other's controls. Per-panel abort handles in `abortByPanel`; gating via `panelBusy(panel)`.
- **n>1 sample cards**: "Make active" now **collapses to the single reply** (others stay cyclable), plus **"Discard others"** and a **per-sample trashbin**.
- **shift+bookmark = save instantly with no note** (skips the note form). `saveHighlight()` is the shared path; `quickTag()` is the no-note caller; the bookmark icon swaps to a filled glyph while shift is held. (See §4 for the deferred *toggle/delete* follow-up.)
- **"+" continue (assistant prefill)** — a "+" on an assistant turn re-fires with that
  turn's content as the trailing **prefill** (the backend's `tinker_sampler.render`
  treats a trailing assistant message as a prefill), so the model EXTENDS it; the N
  continuations land as sibling branches (each = current text + continuation) you
  cycle through. **shift+"+"** = continue the same-depth turn in every panel. The
  samples come back as the continuation only, so `fireChat`'s `prefill` param
  prepends the existing text in the fold. **Caveat:** clean for tinker (native +
  oai); OpenRouter prefill is provider-dependent (best-effort). Worth a real-sample
  eyeball to confirm the X+Y boundary reads right.
- **wandb fields**: `discovery.py` now exposes `wandb_project`/`wandb_name`; frontend filter uses them. (Needs a backend restart to activate project-filtering — see §4.)
- **Infra**: `run.sh`/`vite.config.ts` — `DEV_BACKEND_PORT` now actually wires the vite proxy (was hardcoded :8765); multi scan-dir was already supported.

## 2. The task: N-way model comparison workspace

Clément's four asks (verbatim intent):
1. **Compare >2 models** at once (N panels, not just primary+compare).
2. **Reduce / restore panels** — collapse a panel out of view but keep its tree
   alive; toggle back later. (e.g. compare A,B → add C → reduce A to focus B vs C
   → later unreduce A and reduce B.)
3. **"Send to tab X" on a branch** — a button on a specific branch that copies
   that branch's context into another panel, so you can prompt model A with the
   exact context C just produced.
4. **Composer send-targeting** — in the bottom bar, pick which panels a message
   fires to; reduced panels off by default but re-enableable.

This is one coherent feature: an **N-way comparison workspace**.

## 3. Decisions (resolved with Clément at handover)

- **(A) vs (B) architecture — DELEGATED TO YOU, the building session.** Clément
  said: *"I trust your call on this one — tell post-handoff you that they are
  expected to make their choice."* So **you decide A or B** (don't re-ask him);
  pick from the map in §5. **My recommendation is (B)** — he's going to live in
  this comparison view, so every panel deserving full streaming + n>1 cards is
  worth the backend touch; (A)'s "generates but looks broken (no streaming/cards)"
  degradation is a real UX trap (§5 calls it the silent-degradation trap).
  - **(A) frontend-only extra panels** — keep the backend's 2-slot
    `primary`/`compare` for the CLI; store panels 3…N's selection + trees in the
    conversation store. Less invasive, CLI untouched. **Cost:** panels 3…N lose
    the live streaming overlay + n>1 cards (those ride the bus bucket keyed
    primary/compare); they still generate+fold via their own stream.
  - **(B) full generalization** — make the bus `panel` field + `live.panels`
    bucket panel-id-agnostic so every panel gets streaming+cards. Touches
    `chat.py` broadcasts, `state.svelte.ts`, the SSE snapshot, and the
    conversations.json schema. Bigger, cleaner.
- **Persistence scope — RESOLVED: per-conversation.** Each named conversation
  remembers its own panel layout + models + sampling params, restored on switch.
  **This supersedes the global last-session I shipped this session** (`last_session`
  pref + `restoreSession()` in `+page.svelte`) — rework it so the selection/params
  live on the conversation (folds naturally into the N-panel conversations.json
  schema change, since you're already touching that store). The global pref can
  stay as the seed for a brand-new conversation, but switching conversations must
  restore that conversation's own setup.
- **Per-panel send bubble UX — built + kept** ("＋ continue this panel" at each
  panel's tail). No change requested.

## 4. Smaller pending items (independent of the N-panel work)

- **Backend restart needed** to activate wandb-**project** filtering: the field is
  wired front+back, but `run.sh` dev mode runs without `--reload`, so the new
  `discovery.py` Run fields load only on a fresh backend process. Restart =
  kill the `:8770` proc and re-run the `run.sh` line in §0. (`POST /api/models/refresh`
  re-scans but won't reload the dataclass change.) wandb-*name* search already works.
- **Bookmark toggle / delete** (Clément asked, deferred here on purpose). Today
  the bookmark (tag) button always *adds* a highlight. Wanted: if a response is
  **already bookmarked**, (a) the icon shows a distinct "bookmarked" state, (b) a
  plain click asks "delete this bookmark?" (confirm), (c) **shift+click removes it
  with no prompt**. Mental model that composes with the just-shipped shift-save:
  *shift always = do-it-without-prompting; the current bookmarked state decides
  save-vs-remove.* Build notes: need an "is this response bookmarked" lookup —
  match the `highlights` list by `response` content (+ ideally the panel's
  run_id/checkpoint + `sample_index` to avoid cross-model false matches); pass a
  predicate or a Set of bookmarked keys down to `ChatMessage`. Delete via the
  existing `api.deleteHighlight(id)`. Shared save path is `saveHighlight()` in
  `+page.svelte`; the buttons live in `ChatMessage.svelte` (`tagIcon`/`tagQuickIcon`
  snippets + the two `btn-tag` buttons).
- **README.md rewrite** — DONE this session by Scribe (teammate): full human-facing
  feature tour + 4 real screenshots in `docs/img/` + a `browser_readme_shots.py`
  capture script. (Documents the shipped 2-panel compare, not the pending N-way.)
  No model-filter screenshot (the served build predated it); grab one from :5180 and
  drop at `docs/img/model-filter.png` if wanted. compare.png is slightly truncated
  (max_tokens=60) — re-run the shot script against an isolated instance to redo.
- **ctrl+enter = append a MANUAL assistant turn** (Clément asked, deferred here).
  In the main composer, **ctrl/cmd+enter** should: append the typed text as the user
  turn (like send), but **NOT generate** — instead create an **empty assistant child
  node**, select it, and **open its inline editor focused** so you can hand-write the
  assistant reply (manual/few-shot construction). Build notes: `handleKeydown`
  (`+page.svelte`) currently maps Enter→`sendMessage`; add a ctrl/cmd+Enter branch
  to a new `appendManualTurn()` that does `appendUserTurn(tree, text)` then appends an
  empty assistant child (add a tiny tree helper, e.g. `appendAssistantStub(tree,
  userId)` returning the new node id, or reuse `foldAssistant` with a single empty
  sample). The tricky part is **triggering edit-mode on the new node from the parent**:
  edit state is local to `ChatMessage` (`startEdit`). Cleanest options: (a) a reactive
  `autoEditNodeId` `$state` passed down — `ChatMessage`'s `$effect` opens its editor
  when `msg.nodeId === autoEditNodeId`; or (b) auto-open edit for an empty assistant
  leaf. Compose with the existing `applyEdit` assistant path (which already makes a
  manual branch). shift/ctrl+enter in compare → decide per-panel vs all (probably the
  send-target set).
- **Facet-chips** for the model filter (project / base-model / renderer) — deferred;
  Quill scoped it (~50 LoC). See `scratch/model_search_proposal.md` + `scratch/survey_configs.py`.
- **Highlight-UI overhaul** — `HANDOFF_BRANCHING.md` §5, untouched.
- **Thinking-parse on the n=1 base-model `/completions` path** — `TODO.md` Later.

## 5. The 2-panel assumption map (every site that hardcodes primary/compare)

The current model is exactly **two panels**, named `'primary'` and `'compare'`, threaded as a closed string union from the frontend type system all the way through the SSE state bus, the CLI contract, and the on-disk conversation schema. Generalizing to N panels is mostly a matter of replacing that binary encoding with a panel-id-keyed collection — most *iteration* machinery already folds over `panelSels`/maps and survives untouched; the cost concentrates in the few sites that hardcode the literal two-key encoding underneath.

### Already panel-agnostic (zero or trivial change)

| File | Why it's already N-ready |
|---|---|
| `web/src/lib/tree.ts:1-479` | Pure branch-tree lib. Every export operates on **one** `ConvTree` passed as an argument — no `panel` parameter, no `'primary'`/`'compare'` literal, the ROOT sentinel + per-parent `selected` map are intra-tree only. Each of N panels owns its own tree; tree.ts already serves an arbitrary number. **Zero change.** |
| `web/src/lib/ChatMessage.svelte:1-327` | Renders one panel's worth of data per instance (one `ViewMessage` + callbacks); `sampleNodeIds` read off `msg.sampleNodeIds?.[idx]`, never keyed by panel. The `{#each panelSels}` loop already instantiates it per-panel. **Zero change.** |
| `src/tinkerscope/api/state.py:66,108,122,126` | The in-flight chat counter (`_inflight`) already supports N concurrent panel chats with no change. |
| `src/tinkerscope/api/state.py:55-56,86-88` (mechanism) | The bus `setattr`/`hasattr` patch loop is generic — it survives as-is *if* panel state moves into a panel-id-addressed list (only the field names it patches are the 2-panel encoding). |
| `web/src/routes/+page.svelte:316-328` | `canChat`/`allBusy`/`anyRunning` are already array-folds over `panelSels` (`.every`/`.some`) — generalize for free. |
| `web/src/routes/+page.svelte:402-447, 626-727` | `sendMessage`/`regenerateAll` iterate `for (const p of panelSels)`; every per-panel branch handler (`deleteMessage`/`regenerate`/`cycleBranch`/`selectSample`/`discardOtherSamples`/`deleteSample`/`applyEdit`) routes through `convo.treeFor(panel)`/`live.panels[panel]` and never branches on `'primary'`/`'compare'`. N-ready modulo the underlying maps widening. |
| `web/src/routes/+page.svelte:1173-1192` | `buildChartData`/`panelLabel` iterate `panelSels`, one bar per panel; `CHART_COLORS` has 15 entries so N>2 is fine. |
| `web/src/routes/+page.svelte:820,1676` | `chatContainers: HTMLDivElement[]` is already array-indexed (`bind:this={chatContainers[panelIdx]}`); auto-scroll over N columns works. CSS `.chat-column` is `flex:1` (`:2122`) — N columns lay out for free. |
| `src/tinkerscope/api/settings.py:39,73` | `conversations_path` + per-scan-root keying (`scan_roots_key`) is panel-count-agnostic; anchors *where* the N-tree schema persists but holds no 2-panel assumption. |
| `src/tinkerscope/api/api.ts:51-57` (`getPrefs`/`setPref`, GET/PUT `/api/prefs`) | Flat `Record<string,string>` key/value, no panel dimension — unaffected. |

The root type that everything else keys off — `Panel = 'primary' | 'compare'` (`web/src/lib/types.ts:75`) — is the linchpin: widening it to an open panel-id string (or `'primary' | 'compare' | ...` superset) is what makes every `Record<Panel,…>` map and per-panel signature N-ready at the type level.

### Backend: shared-state + SSE bus + CLI

| location (path:line) | today (2-panel) | change for N |
|---|---|---|
| `src/tinkerscope/api/state.py:30-41` | `PlaygroundState` hardcodes two panels as named scalars: `mode='single'\|'compare'` (:30), primary `run_id`/`checkpoint` (:32-33), `compare_run_id`/`compare_checkpoint` (:35-36), and two transcripts `messages`/`compare_messages` (:40-41). Fan-in to N is impossible without `*_2`,`*_3` scalars. | Replace the scalar pairs with `panels: list[PanelSel]` (each `{panel_id, run_id, checkpoint, base_model?, sampler_path?, openrouter_model?, messages}`); `mode` collapses to `len(panels)`/explicit `n`. Keep a write-only active-path `messages` echo per panel, indexed by `panel_id`. **Pivot point:** `asdict()` in `to_dict()` (:56) auto-serializes the dataclass, so the SSE snapshot shape changes the instant the dataclass changes. |
| `src/tinkerscope/api/state.py:55-56,76,83-93,100-130` | Bus helpers patch state by `hasattr`/`setattr` on flat names (`publish_state` :86-88, `chat_begin` :109-111, `chat_end` :123-125); panel-agnostic *mechanism* but the field names (`run_id` vs `compare_run_id`, `messages` vs `compare_messages`) are the 2-panel encoding. `to_dict()` (:56) feeds the snapshot to every new subscriber (:76) and every patch (:92,115,130). | Add `patch_panel(panel_id, **fields)` that mutates `self.state.panels[panel_id]`; keep `publish_state`/`chat_begin`/`chat_end` for non-panel fields (params, chat_id, running). Callers pass `panel_id` + sub-patch instead of top-level names. |
| `src/tinkerscope/api/routes/state.py:22-37` | `StatePatch` (the POST `/api/state` body — external contract both CLI and browser write) enumerates the same flat fields: `run_id`/`checkpoint` + `compare_run_id`/`compare_checkpoint` (:26-29), `messages`/`compare_messages` (:30-31), `mode` (:25). `model_dump(exclude_unset=True)` (:47) forwards these names to `BUS.publish_state`. | Address a panel: either `panels: list[...]` full-replace or a targeted `{panel_id, ...fields}` sub-patch. Route + bus must agree on the new shape. **Breaking** to the request contract — CLI and browser move together. GET `/api/state` (:40-42) inherits the snapshot change automatically (returns `to_dict()`). |
| `src/tinkerscope/api/routes/chat.py:77,141` | `ChatRequest.panel: str = 'primary'` ("primary"\|"compare", :77); `is_compare_panel = (req.panel == 'compare')` (:141) is the single boolean driving ALL panel routing below. `/api/chat` is per-panel already (one request = one model). | Replace the binary field with an opaque `panel_id` string; drop `is_compare_panel`, key everything off `panel_id`. The "one request samples ONE model" design already generalizes — caller fires N requests tagged `panel=<id>`. **External contract:** `panel` is echoed on every bus broadcast (browser dispatches by it) and set by the CLI. |
| `src/tinkerscope/api/routes/chat.py:230-234,246-253,277-283` | `is_compare_panel` branches the SELECTION patch (:230-234), the START patch (:246-253), and the END transcript commit (:277-283). The primary branch *additionally* writes the shared sampling params (`system_prompt`/`temperature`/`max_tokens`/`n_samples`/`thinking`/`top_p`) — params are NOT per-panel, they live only on the primary write (:251-253). | Generalize all three to `panels[panel_id].{run_id,checkpoint,messages}`. **Decision needed:** reassign param authorship — (a) elect a param-author panel, (b) move params out of the per-panel branch, or (c) make params global (set only via `/api/state`). |
| `src/tinkerscope/api/routes/chat.py:239,256-259,269,276,286-289,294-297` | Every bus broadcast carries `'panel': req.panel` — `chat_error` pre-start (:239), `chat_start` (:256-259), `delta` (:269), `sample` (:276), `chat_done` (:286-289), `chat_error` mid-stream (:294-297). With only two valid values the browser hardcodes two destinations. | No structural plumbing change — `panel` is already an opaque pass-through (server never interprets it beyond `is_compare_panel`). Let it carry an arbitrary `panel_id`. Contract change is *semantic*: browser dispatches by a dynamic panel-id set. `client_token` (:84) gives per-chat ownership, orthogonal to panel count. |
| `src/tinkerscope/api/routes/conversations.py:66-79,89-105,121-134` | The per-scan-root conversation store is hardcoded to two trees: `tree` (primary) + `compare_tree`. `ConversationCreate` (:66-71), `TreeSave` (:77-81), `create_conversation` (:92-100), `save_conversation_tree` (:121-134) all name exactly these two opaque-JSON slots. `system_prompt` stored once per conversation (shared). | Replace `tree`/`compare_tree` with `trees: dict[panel_id -> opaque tree]`. Server treats trees as opaque JSON, so the only edits are the field names in the two Pydantic models + the two read-modify-write handlers. **External contract** (on-disk schema): existing saved conversations have `{tree, compare_tree}` and need a migration/back-compat read (`tree→trees[primary]`, `compare_tree→trees[compare]`). |
| `src/tinkerscope/cli.py:523-574` | `cmd_compare` is hardwired to TWO runs: positional `run_a`/`run_b` (:525-526), resolves two (:538-539), posts `mode=compare` with `run_id`/`compare_run_id` (+optional checkpoints) (:545-549), builds `body_a` (`panel='primary'`) + `body_b` (`panel='compare'`) (:552-553), spawns two threads (:559-566), aggregates two failures (:568-574). | Take N runs (variadic `runs: list[str]` or repeated `--run`). Loop to resolve each, build one `/api/state` patch with the panels list, spawn one thread per run tagged `panel=<panel_id>`, collect a `_StreamResult` per thread, aggregate all failures. **External contract** (documented terminal-drive surface). Consider keeping `compare a b` as the 2-run case for back-compat, or rename to `panels`/`grid`. |
| `src/tinkerscope/cli.py:467-493,513-516,517` | `_chat_body` takes a single `panel: str` (:476) written into the body (:486); `cmd_chat` hardcodes `panel='primary'` (:517) and posts `{mode:'single', run_id, checkpoint}` (:513-516); `cmd_open` (:311) posts `{mode:'single', run_id}`. | `_chat_body` already accepts an arbitrary panel string — pass a `panel_id` from the new loop. `cmd_chat` stays as the N=1 case. The selection patches in `cmd_open`/`cmd_chat` move to the new `/api/state` panels shape (`mode='single'` becomes a 1-panel list). Low effort relative to `cmd_compare`. |

### Frontend: panel model, composer, per-panel logic

| location (path:line) | today (2-panel) | change for N |
|---|---|---|
| `web/src/lib/types.ts:75` | `export type Panel = 'primary' \| 'compare';` — closed 2-member union typing every per-panel arg, every `Record<Panel,…>` key, and `ChatRequest.panel`. | Open identifier: `type Panel = string` (panel id) or `number` (index). Every `Record<Panel,…>` → string/number-keyed map or array. Literal `panel === 'primary'/'compare'` comparisons must go (won't typecheck against an open type, and they carry the 2-panel branch logic). |
| `web/src/lib/types.ts:78-96` | `PlaygroundState` has paired scalars for two panels (`run_id`/`checkpoint` + `compare_run_id`/`compare_checkpoint`, `messages` + `compare_messages`, `mode:'single'\|'compare'`). | Array: `panels: {run_id, checkpoint}[]` + `panel_messages: ChatMessage[][]` (or keep `run_id`/`checkpoint`/`messages` as panel-0 for CLI back-compat, array for ≥1). `mode` collapses to `panels.length`. SSE-snapshot/state-bus contract shared with CLI. |
| `web/src/routes/+page.svelte:183-195` | `mode`/`isComparing` derived from `s.mode`; `panelSels` is `$derived` `[primary]` or `[primary, compare]`, each entry reading the hardcoded scalar pair. `PanelSel = {panel, run_id, checkpoint}` (:187). | `panelSels = s.panels.map((ps, i) => ({panel: panelId(i), run_id: ps.run_id, checkpoint: ps.checkpoint}))` (no `isComparing` branch); `isComparing = panelSels.length > 1`. `PanelSel` shape unchanged but `panel` is an open id. |
| `web/src/routes/+page.svelte:248-262` | `setRun(panel)`/`setCheckpoint(panel)` each `if (panel==='primary') patchState({run_id,checkpoint}) else patchState({compare_*})` — the core 2-way fan-out from a `Panel` to one of the two scalar pairs. | Patch the array slot for that index: `patchState({panels: replaceAt(s.panels, idx, {run_id, checkpoint})})`. The if/else is removed; one indexed write covers all N. |
| `web/src/routes/+page.svelte:264-289` | `resetComparePanel()` hardcodes `live.panels.compare = emptyPanel` (:265). `enableCompare()` (:270) adds the SECOND panel + `convo.duplicateToCompare()`. `disableCompare()` (:284) sets `mode:'single'` + resets compare bucket + `convo.setTree('compare', emptyTree())`. | `enableCompare → addPanel()`: append a slot to `s.panels`, grow the per-panel tree array, seed the new panel's tree from a chosen source (`duplicateToCompare → duplicateTo(srcIdx, dstIdx)`/`forkPanel(idx)`). `disableCompare → removePanel(idx)`: splice slot `idx`, drop its tree, clear its bucket. `resetComparePanel → resetPanelBucket(idx)`. No more `mode`. |
| `web/src/routes/+page.svelte:316-328` | `canChat`/`allBusy`/`anyRunning` already fold over `panelSels`; `panelBusy(panel)` reads `live.panels[panel]?.running`. | `canChat`/`allBusy`/`anyRunning` no change. `panelBusy` only needs `live.panels` to become array/open-keyed (lookup shape is the same). |
| `web/src/routes/+page.svelte:396-400` | `panelDraft = Partial<Record<Panel,string>>` (composer drafts) and `abortByPanel = Partial<Record<Panel,AbortController\|null>>` — Panel-keyed maps only ever holding `'primary'`/`'compare'`. | Arrays indexed by panel index (or open string-keyed maps). No logic change beyond the key type — already written/read by panel id. |
| `web/src/routes/+page.svelte:402-447` | `sendMessage()` iterates `for (const p of panelSels)`; `sendToPanel`/`fireOne` use `panelSels.find` — already N-ready. Comment at :413 ("In single mode only the primary panel exists") is the only baked-in assumption. | No structural change — update the stale comment. Depend on `panelDraft`/`abortByPanel`/`convo.treeFor` being open-keyed (covered above). |
| `web/src/routes/+page.svelte:492-548` | `fireChat(p, …)` resolves the model from `p.run_id` (sentinel-decoded `openrouter:`/`base:`/`ckpt:` via `OR_PREFIX`/`BASE_PREFIX`/`CKPT_PREFIX` :102/:127/:146 + decoders), sends `panel: p.panel`. Sentinel encoding is per-panel-*value*, not 2-panel-bound. | Unchanged except `panel: p.panel` now carries an open id (request body `panel` widens — contract). Sentinel encoding lives inside one `run_id` slot regardless of slot count — no change. |
| `web/src/routes/+page.svelte:550-557` | `stopGeneration(panel?)` stops one if given else `Object.keys(abortByPanel) as Panel[]` (all) — already iterates the abort map. | No change beyond `abortByPanel`'s key type widening (covered). |
| `web/src/routes/+page.svelte:592-602` | `clearPanelBucket(panel)` writes `live.panels[panel] = emptyPanel`; `fireForPanel` uses `panelSels.find`. Both per-panel. | No change beyond `live.panels` becoming open-keyed/array. |
| `web/src/routes/+page.svelte:626-727` | Per-panel branch handlers — `deleteMessage` (:626), `regenerate` (:635), `cycleBranch` (:660), `selectSample` (:669), `discardOtherSamples` (:678), `deleteSample` (:692), `applyEdit` (:708) — all take `panel: Panel`, operate via `convo.treeFor(panel)`/`convo.setTree(panel,…)` + `live.panels[panel]`; none branch on the literals. `regenerateAll` (:645) iterates `panelSels` matching by active-path depth. | No structural change. Depend only on `convo.treeFor`/`setTree` and `live.panels` being open-keyed. Handlers are already panel-id-agnostic. |
| `web/src/routes/+page.svelte:738-817` | `panelView(p)` builds one panel's rendered view from `convo.treeFor(p.panel)` + `live.panels[p.panel]` overlay; the card→node mapping (:788-803) slices the LAST `filledCount` children as this turn's folds, maps each non-error sample slot to a sibling node id. `bucketTurn` types its arg `(typeof live.panels)[Panel]`. Tree-driven, panel-agnostic in logic. | Only the `live.panels[p.panel]` lookup needs the bucket Record keyed by N ids. Fold/slice mapping is panel-count-independent — unchanged. If a panel has NO bucket → `hasBucket` false → renders committed tree replies only (graceful degradation). |
| `web/src/routes/+page.svelte:820-825` | `chatContainers: HTMLDivElement[]` array-indexed (already fine). The `$effect` at :821 touches `s.messages`/`s.compare_messages` to re-run — hardcodes the two echo fields. | `chatContainers` no change. The `$effect`'s dependency-touch must touch the new N-panel echo array (`void s.panel_messages`) instead of the two scalars. |
| `web/src/routes/+page.svelte:914,932-936,961-962,971,987-991,1001-1005` | Model-picker plumbing keyed to a target panel: `orAddPanel`/`tinkerAddPanel` `$state<Panel>('primary')` defaults; `openOrManager`/`openTinkerPicker` set the target; pickers call `setRun(targetPanel,…)`. `removeOpenrouterModel` (:961-962) hardcodes BOTH panels: `if (openrouterId(s.run_id)===id) setRun('primary',…)` and `… s.compare_run_id … setRun('compare',…)`. | Defaults → panel index 0; pickers unchanged (`setRun` on a stored target). `removeOpenrouterModel`'s two hardcoded checks become a loop over `panelSels` clearing any panel whose `run_id` points at the removed model. |
| `web/src/routes/+page.svelte:1062,1075-1093` | `tagFormPanel $state<Panel>('primary')`; `openTagForm` stores it; `submitTag` (:1086-1093) resolves via `panelSels.find(x=>x.panel===tagFormPanel)` with `panelSels[0]` fallback. | `tagFormPanel` default → panel index 0. `submitTag`'s find + fallback already generalize. |
| `web/src/routes/+page.svelte:1452-1572` | Sidebar Models: `{#each panelSels as p (p.panel)}` renders one model-block per panel — N-ready. BUT the remove-pane button (:1519) gates on `isComparing && p.panel === 'compare'` (only compare removable). | `{#each}` fine. Gate `p.panel === 'compare'` → `panelSels.length > 1` (any panel beyond the first removable), wire onclick to `removePanel(idx)` instead of `disableCompare`. Keyed-each `(p.panel)` must use a **stable** panel id (not array index) so remove/reorder doesn't remount the wrong column. |
| `web/src/routes/+page.svelte:1573-1578` | `{#if !isComparing}` … `<button onclick={enableCompare}>Compare</button>` — only appears with exactly one panel, only ever adds the single second. | Drop the `!isComparing` gate → always-available `+ Add panel` (`onclick={addPanel}`), allowing 3rd, 4th, … Optionally cap at max N. |
| `web/src/routes/+page.svelte:1668-1728` | Chat area: `class:multi={isComparing}` (:1668), `{#each panelSels as p, panelIdx (p.panel)}` (:1669), column-header `{#if isComparing}` (:1673), per-panel composer `{#if isComparing && convo.activeId && panelCanChat(p)}` (:1705). CSS `.chat-column` `flex:1` (:2122). | `class:multi → {panelSels.length > 1}`; column-header + composer gates → `{#if panelSels.length > 1 && …}`. `{#each}` body unchanged. No CSS change (flexbox scales); maybe add `overflow-x` / `min-width` for many panels. |
| `web/src/routes/+page.svelte:177-181,344-388` | `DEFAULTS` (:177) and session-persistence (`persistSession` :339-353 + `restoreSession` :364-388) enumerate the exact 2-panel scalars + `mode`; the `$effect` (:355-361) touches each by name. | `DEFAULTS`, the persisted-session JSON, and `restoreSession` serialize/restore the `panels[]` array instead of two scalar pairs + `mode`; the `$effect` iterates/touches the panels array. localStorage/prefs restore contract. |

### Frontend: live bus + conversation store

| location (path:line) | today (2-panel) | change for N |
|---|---|---|
| `web/src/lib/state.svelte.ts:36-39` | `panels = $state<Record<Panel,PanelRun>>({primary: emptyPanel(), compare: emptyPanel()})` — the live bus bucket, hardcoded to exactly two literal keys. | Make the bucket a Record keyed by panel id, **lazily created on first `chat_start`** rather than pre-seeded with two keys. Widen `Panel` to a string id (or add `panels: Record<string,PanelRun>` that auto-vivifies). Pre-seeding must go. |
| `web/src/lib/state.svelte.ts:65-67` | `clearBuckets()` resets to `{primary: emptyPanel(), compare: emptyPanel()}` — re-hardcodes two keys (called 6× from the convo store on switch/new/load). | Reset to `{}` (buckets auto-vivify on `chat_start`) or rebuild from the current panel-id set. Stop minting exactly primary+compare. |
| `web/src/lib/state.svelte.ts:70-72` | `get anyRunning()` returns `panels.primary.running \|\| panels.compare.running` — only the two fixed slots, so a 3rd panel's running flag is invisible. **Gates `conversations.svelte.ts:239`'s external-fold**, so a 3rd-panel CLI run could fold while another is "running" and corrupt the tree. | `return Object.values(this.panels).some(p => p.running)`. Must cover all N or 3rd-panel CLI runs slip through. |
| `web/src/lib/state.svelte.ts:82,101,118,129,141` | Every bus handler does `const panel = (data?.panel ?? 'primary') as Panel` then indexes `this.panels[panel]`. `chat_start` writes a new slot (safe); `delta`/`sample`/`chat_done`/`chat_error` READ `this.panels[panel]` assuming the slot pre-exists. | `?? 'primary'` default is fine, but the read sites must tolerate a missing slot (auto-vivify or guard `cur ?? emptyPanel()`) since buckets are no longer pre-seeded. `as Panel` → `as string`. Otherwise already per-panel-keyed. |
| `web/src/lib/conversations.svelte.ts:43-44` | Two reactive fields: `tree = $state<ConvTree>(emptyTree())` + `compareTree = $state<ConvTree>(emptyTree())`. | Single reactive map `trees = $state<Record<string,ConvTree>>({primary: emptyTree()})`. All reads/writes route through it. Needs a panel-id ordering source (panel list lives elsewhere). |
| `web/src/lib/conversations.svelte.ts:55-57` | `treeFor(panel)` is a binary ternary `panel === 'compare' ? compareTree : tree`. | `return this.trees[panelId] ?? emptyTree()`. The **single read chokepoint** (+page funnels every read through it) — high-leverage change. Param type `'primary'\|'compare'` → string. |
| `web/src/lib/conversations.svelte.ts:81-86` | `setTree(panel, next, persist)` single commit: `if (panel==='compare') compareTree = next; else tree = next;` then `#mirror()` + `save()`. | `this.trees = {...this.trees, [panelId]: next}` (immutable so `$state` tracks it). The **single write chokepoint** — `#mirror`/`save` pick up the new tree automatically once generalized. |
| `web/src/lib/conversations.svelte.ts:88-95` | `#mirror()` posts exactly two fields to `PlaygroundState`: `messages: activeMessages(tree)` + `compare_messages: activeMessages(compareTree)` — the write-only active-path echo for the CLI. | Mirror N panels. Either keep `messages`/`compare_messages` for panels 0/1 + add per-panel active paths for the rest, or generalize the state-patch shape. For N>2 the extra panels' active paths need a new state field or aren't echoed to the CLI. Cross-slice contract with the bus/state slice. |
| `web/src/lib/conversations.svelte.ts:99-110, 52` | `save()` snapshots both trees into `#pending` (`tree: snapshot(tree)`, `compareTree: ct.rootChildren.length ? ct : null` — compare collapsed to null when empty). `#pending` typed `{id; tree; compareTree: ConvTree\|null; system_prompt}` (:52). | Snapshot the whole map; drop empty trees (the `rootChildren.length` collapse → omit a panel's tree when empty). `#pending` → `{id; trees: Record<string,ConvTree>; system_prompt}`. |
| `web/src/lib/conversations.svelte.ts:112-123` | `#doSave()` calls `api.saveConversationTree(p.id, p.tree, p.compareTree, p.system_prompt)` — positional two-tree signature. | `api.saveConversationTree(p.id, p.trees, p.system_prompt)` passing the map. |
| `web/src/lib/conversations.svelte.ts:228-233` | `#loadTrees(conv)` reads `conv.tree → this.tree` and `conv.compare_tree ? asTree(...) : emptyTree() → this.compareTree`. | `this.trees = Object.fromEntries(panelIds.map(pid => [pid, asTree(conv.trees?.[pid])]))`. **THE migration read point** — must accept legacy `{tree, compare_tree}` and synthesize `{primary: tree, compare: compare_tree}` when `conv.trees` absent (back-compat shim lives here). |
| `web/src/lib/conversations.svelte.ts:171-175,190-195,211-214` | `create()` / `remove()`-last-reset / `resetActive()` each set BOTH `tree`+`compareTree = emptyTree()` and POST `{messages:[], compare_messages:[]}` to clear the two transcripts. | Reset the whole map to `{primary: emptyTree()}` and clear N active-path echoes in the patch. Three near-identical clear sites → a single `this.trees = freshTreesMap()` each. |
| `web/src/lib/conversations.svelte.ts:221-226` | `duplicateToCompare()` copies primary → compare: `this.compareTree = $state.snapshot(this.tree)` — the "enter compare" affordance, source+dest baked into the name. | `duplicateTo(panelId)` deep-cloning primary (or any source) into a newly-added panel, so "add 3rd/4th panel seeded from primary" works. |
| `web/src/lib/conversations.svelte.ts:238-248` | `#afterLoad()` reconciles external turns for two panels: `live.state?.messages → tree`, `live.state?.compare_messages → compareTree`. | Loop reconciling each panel's active-path echo into `trees[pid]`. Bounded by however many active-path fields the bus exposes — for N>2 only echoed panels reconcile unless the bus grows per-panel message fields. Cross-slice dependency. |
| `web/src/lib/conversations.svelte.ts:259-275` | `#onExternalDone(panel, data)` selects `panel==='compare' ? compare_messages : messages`, reconciles into `treeFor(panel)`, commits via `setTree(panel,…)`. | Map `panelId → its active-path echo field`, reconcile into `treeFor(panelId)`. Same bus-field constraint as `#afterLoad`. Panel param → string. |

### Frontend: bucket/card-mapping consumers (degradation surface)

| location (path:line) | today (2-panel) | change for N |
|---|---|---|
| `web/src/routes/+page.svelte:761-817` (`panelView`, esp. :788-803 sampleNodeIds) | Reads `live.panels[p.panel]` (:773), overlays the bucket on the active leaf; card→node mapping (:788-803) slices the LAST `filledCount` children, maps each non-error sample slot to a sibling node id; `activeSampleIndex = sampleNodeIds.indexOf(replacedId)`. Tree-driven, panel-agnostic in logic. | Only the `live.panels[p.panel]` lookup needs the bucket Record N-keyed. Fold/slice mapping is panel-count-independent. No bucket → `hasBucket` false → committed tree replies only (no cards, no overlay) — the graceful-degradation path. |
| `web/src/routes/+page.svelte:738-759` (`bucketTurn`) | `bucketTurn(run: (typeof live.panels)[Panel])` — param TYPE derived from `live.panels` indexed by `Panel`. Body pure (reads `run.samples`/`n`/`running`), panel-agnostic. | Only the param annotation must resolve to `PanelRun` under the widened key. Body unchanged. |
| `web/src/routes/+page.svelte:592-595` (`clearPanelBucket`) + :265-268 (`resetComparePanel`) + :698-703 (`deleteSample` bucket edit) | All three write a fresh `{chat_id,label,n,samples,running,error}` into `live.panels[panel]` (or hardcoded `live.panels.compare` in `resetComparePanel`:266) and reassign `live.panels = {...}`. `resetComparePanel` hardwired to the literal `compare`. | `clearPanelBucket`/`deleteSample` already take `panel` — work once the Record is N-keyed. `resetComparePanel`:266 → "reset panel X" or replaced by remove-panel. Reuse `emptyPanel()` instead of inlining the shape (3 copies — refactor opportunity). |
| `web/src/routes/+page.svelte:319,326-328,453-548,1671,1699-1704` (`anyRunning`, `panelBusy`, `drainSamples`/`fireChat`, template bucket reads) | `panelBusy(panel)` reads `live.panels[panel]?.running` (:327) — per-panel. `fireChat`/`drainSamples` (:453-548) fold from their OWN `/api/chat` stream and pass `panel: p.panel` (:526) — panel id plumbed as data, not hardcoded. Template (:1671) `const run = live.panels[p.panel]`; loading-dots (:1699) + progress rely on `run`. | All just need the bucket Record N-keyed + `Panel` widened. **Key point:** `fireChat` is already panel-count-independent (folds from its own stream) — a panel whose id isn't a bus bucket key still generates+folds correctly; it only loses the streaming overlay (:1699-1704 dots, delta tokens) and n>1 cards. **That is the entire quantified loss without bucket generalization.** |

### Contracts touched

Five external contracts break; each requires CLI and browser (and on-disk data) to move together.

1. **POST `/api/state` (`StatePatch` body)** — `routes/state.py:22-37`. **Breaking.** The flat `run_id`/`compare_run_id`/`checkpoint`/`compare_checkpoint`/`messages`/`compare_messages`/`mode` fields become a panel-addressed shape (a `panels` list or a `{panel_id, ...}` sub-patch). Both the CLI (`cli.py` `cmd_open`/`cmd_chat`/`cmd_compare`) and the browser (`+page.svelte` `patchState`) write it, so they must change in lockstep.

2. **SSE state snapshot (GET `/api/state` + `/api/state/events`)** — `state.py:55-56` + `routes/state.py:40-67`, consumed in `types.ts:78-96`. **Breaking, implicitly.** The snapshot is `asdict(PlaygroundState)`, so changing the dataclass from primary/compare scalars to a `panels` list changes *every* snapshot/patch payload the browser renders. There is no explicit serializer to update as a checkpoint — the wire shape flips the instant the dataclass changes, which makes it easy to ship a browser-breaking snapshot without noticing.

3. **`/api/chat` `ChatRequest.panel`** — `routes/chat.py:77` (+ broadcasts :239/:256/:269/:276/:286/:294), typed `Panel` at `types.ts:119`, consumed in `state.svelte.ts:82,101,118,129,141`. **Breaking (semantic).** `panel` goes from the `'primary'|'compare'` enum to an opaque `panel_id`, echoed on every `chat_start`/`delta`/`sample`/`chat_done`/`chat_error` broadcast the browser dispatches by. The `?? 'primary'` fallback in the bus handler keeps old single-panel events working (non-breaking for n≤2), but the browser must dispatch by a dynamic panel-id set instead of two fixed buckets.

4. **`conversations.json` on-disk schema + `/api/conversations` create / PUT-tree bodies** — `routes/conversations.py:66-81,92-100,121-134`; TS halves `Conversation.tree`/`compare_tree` (`types.ts:129-137`), `api.ts` `createConversation` (:72-77) + `saveConversationTree` (:83-92). **Breaking.** `{tree, compare_tree}` → `{trees: {panelId: ConvTree}}` in the create body, the PUT-tree body, and the GET response. This is **per-scan-root and multi-file** (`SETTINGS.conversations_path`), so there can be many `conversations.json` files all needing migration. These are user-authored trees the code explicitly refuses to clobber (the `.corrupt-<ts>` backup logic in `_read`, `conversations.py:37-59`), so silent data loss is unacceptable — a back-compat read shim is mandatory (see migration concern below).

5. **`tinkpg` CLI compare command** — `cli.py:523-574` (+ the `panel='primary'` default in `cmd_chat:517`). **Breaking.** The `run_a`/`run_b` two-positional signature must become N runs; this is the documented terminal-drive surface. Back-compat option: keep `compare a b` working as the 2-run case, or rename to a general `panels`/`grid` command.

Non-breaking-critical, but flagged: **localStorage `last_session`** (`+page.svelte:343-353,373-382`) serializes the 2-panel scalar selection — old sessions won't restore extra panels unless updated to the `panels[]` array. The **highlight record** (`+page.svelte:1095-1118`, `api.addHighlight`) stores one panel's `run_id`/`checkpoint`/`base_model`/`sampler_path` — one highlight = one panel's sample, so no schema change, only `tagFormPanel`'s default widens. **`getPrefs`/`setPref`** (`/api/prefs`) carries no panel dimension — unaffected.

A cross-slice contract worth isolating: **`PlaygroundState.messages` / `compare_messages` active-path echo** (`types.ts:84-85`) can only echo 2 panels. `#mirror`/`#afterLoad`/`#onExternalDone` (`conversations.svelte.ts:88-95,238-248,259-275`) feed/read it. For N>2, either accept that extra panels aren't CLI-visible, or the bus slice must add per-panel active-path fields. This is the seam where the "frontend-only" and "full" options below diverge.

### A vs B decision

The choice is whether extra panels (3..N) are *first-class* on the live bus or only on the tree. Both options share the same frontend-store and conversation-schema work (the `trees` map, `treeFor`/`setTree` chokepoints, the panels array in `PlaygroundState`/`panelSels`, the sidebar add/remove UI). They differ only in whether the **bus `panel` field + `live.panels` bucket** are generalized.

#### Option A — frontend-only extra panels (backend stays 2-slot for the CLI)

Keep the backend's named 2-panel encoding for the CLI's benefit; store panels 3..N selection + trees entirely in the conversation/frontend store. Extra panels generate and fold via their **own `/api/chat` stream** (`fireChat`/`drainSamples`, `+page.svelte:453-548`) — that path is already panel-count-independent and passes `panel: p.panel` as opaque data, so the committed reply + the ‹k/N› cycler (both tree-driven, `tree.ts`) work for panel N **for free**. What extra panels **lack**: the live streaming overlay (token-by-token `delta`, loading dots at `+page.svelte:1699-1704`, the n>1 progress bar) and the n>1 sample cards (Make active / Discard others / Delete sample / per-sample tag/raw) — those ride the `live.panels` bucket, which is keyed `primary`/`compare` only.

**Sites touched (frontend only):**
- The full frontend-store generalization: `Panel` type (`types.ts:75`), `panelSels` + `setRun`/`setCheckpoint` (`+page.svelte:183-195,248-262`), add/remove-panel UI (`:264-289,1452-1572,1573-1578,1668-1728`), `panelDraft`/`abortByPanel` (`:396-400`), session persistence (`:177-181,344-388`), and the entire conversations store (`conversations.svelte.ts:43-275`, including the `trees` map + migration shim at `:228-233`).
- The backend keeps `PlaygroundState`'s `primary`/`compare` scalars, `StatePatch`, `chat.py`'s `is_compare_panel`, `state.svelte.ts`'s `{primary, compare}` bucket, and `anyRunning` over two fixed slots **untouched** — panels 3..N simply never write to those.

**Explicitly NOT touched:** `chat.py` broadcasts (still emit `panel: 'primary'|'compare'` for the 2 bus-backed panels), `state.svelte.ts:36-39,65-67,70-72` (`live.panels` stays a 2-key Record + `anyRunning` over two slots), the SSE snapshot's panels shape, and the bus `panel` field's value set.

**Caveat (the silent-degradation trap):** because `fireChat` folds from its own stream (`+page.svelte:453-548`), an extra panel generates+folds **correctly but invisibly** — no dots, no streaming, no n>1 cards during generation; the reply only pops in when the fold completes. That degraded-but-functional state is easy to mistake for a bug or to ship as "good enough." Also note `state.svelte.ts:70-72`'s `anyRunning` gates `conversations.svelte.ts:239`'s external-fold — under Option A that gate only sees panels 0/1, which is *fine* as long as extra panels never route through the bus, but it's a sharp edge if that invariant ever slips.

#### Option B — full generalization (every panel gets streaming + cards)

Make the bus `panel` field and the `live.panels` bucket **panel-id-agnostic** so every panel gets the streaming overlay and n>1 cards. On top of Option A's frontend-store work, this additionally touches:

- **`chat.py` broadcasts** (`routes/chat.py:77,141,230-234,246-253,256-289,294-297`): drop `is_compare_panel`, key the selection/start/end patches off `panel_id`, let every broadcast carry an arbitrary `panel_id`, and resolve the sampling-params authorship decision (`:251-253` — elect a param-author, make params global via `/api/state`, or go per-panel).
- **`state.svelte.ts`** (`:36-39,65-67,70-72,82,101,118,129,141`): `live.panels` → an open-keyed Record **lazily vivified on `chat_start`** (drop the pre-seeded `{primary, compare}`); `clearBuckets()` → `{}`; `anyRunning` → `Object.values(this.panels).some(p => p.running)`; the `delta`/`sample`/`chat_done`/`chat_error` read sites must guard a missing slot (`cur ?? emptyPanel()`).
- **The SSE snapshot** (`state.py:30-41,55-56`): `PlaygroundState` becomes a `panels` list with a per-panel `messages` echo, so `asdict()` emits the N-panel snapshot every panel renders from.
- **The `conversations.json` schema** (`routes/conversations.py:66-134`): `{tree, compare_tree}` → `{trees: {panelId: ConvTree}}` — though this schema change is *also* required by Option A's `trees` map, so it's shared, not B-specific. What's B-specific on the conversation side is `#mirror`/`#afterLoad`/`#onExternalDone` (`conversations.svelte.ts:88-95,238-248,259-275`) gaining per-panel active-path echo fields in `PlaygroundState` (vs. Option A, where extra panels simply aren't echoed to the CLI).

**The asymmetry B must resolve:** today only the `primary` panel's `/api/chat` write commits the shared params (`chat.py:251-253`); the compare write deliberately omits them. The 2-panel code gets away with "primary owns params, compare is content-only" because there's a privileged panel. With N symmetric panels that privilege must be reassigned explicitly — get it wrong and params silently stop updating, or two panels race to author them.

#### Migration concern for existing saved conversations (applies to both options)

Both A and B require the `conversations.json` schema change (`{tree, compare_tree}` → `{trees: {panelId}}`), so both inherit the same migration burden. Existing saved conversations on disk carry `{tree, compare_tree}`, and the `_read` corrupt-file guard (`conversations.py:37-59`) protects against *parse* errors but **not** against a schema the new code doesn't recognize — so a naive cutover would silently fail to load (or drop) user-authored trees.

The safe path is **read-time back-compat in `#loadTrees`/`asTree`** (`conversations.svelte.ts:228-233`): when `conv.trees` is absent, synthesize `{primary: conv.tree, compare: conv.compare_tree}` (compare only if present). Pair it with a **write-time upgrade** (`save_conversation_tree`, `conversations.py:121-134`, writes the new `trees` key and drops the legacy `tree`/`compare_tree` keys) so each file self-heals on first edit. Until a conversation is re-saved, the old keys persist, so **both readers must tolerate the old shape indefinitely** — a fire-once migration that drops `compare_tree` before the new shape is confirmed written risks data loss, which is unacceptable given the explicit no-clobber design.

A second migration subtlety, independent of disk format: **panel identity must be stable string ids, not positional indices.** The tree deliberately keys selection by node id (not array index) to survive reorders; panels need the same treatment. If panel ids are array-index-based they collide with that invariant — closing or reordering a *middle* panel will silently rebind a tree (and its bound `chatContainers`/`panelDraft`/bucket) to the wrong column. The `{#each panelSels (p.panel)}` keying (`+page.svelte:1452,1669`) must therefore key on a stable per-panel id, and the migration must assign `primary`/`compare` as the reserved ids for the two legacy trees.

## 6. Gotchas carried from this session (don't re-learn the hard way)

- **`structuredClone` fails on Svelte `$state` proxies** ("could not be cloned").
  Use `$state.snapshot` for a deep proxy-safe clone; `cloneTree` in tree.ts is a
  manual deep clone for the same reason.
- **`panelBusy` must gate on the bus `running` flag** (`live.panels[panel].running`),
  **not** `abortByPanel`. `abortByPanel`'s clear is tied to `fireChat`'s promise,
  which can linger a beat past `chat_done` → wrongly-disabled controls (this was a
  real bug fixed this session). `abortByPanel` is for `stopGeneration` only.
  Conversation-switch safety uses `convo.busy` (token set), which is fine.
- **`fireChat` folds from its OWN `/api/chat` stream** (`drainSamples`), not the
  bus. So extra panels can generate+fold WITHOUT a bus bucket — but lose the
  streaming overlay + n>1 cards. **This is the crux of the A-vs-B decision.**
- **Send-before-load race**: the composer is gated on `convo.activeId`, and
  `convo.load()` runs early in `onMount`, so a send can't build a tree that
  `load()` then clobbers. Preserve this when reworking the composer.
- **`conversations.json` is per-scan-root** (state dir = sha1 of the scan-root
  set): `~/.local/state/tinkerscope/<hash>/conversations.json`. weird-personas
  has its own hash dir. Any persistence-schema change for N panels must handle
  **existing saved conversations** (tree + compare_tree) gracefully.
- **Node v22.22.3** for the frontend tooling (`export PATH="$HOME/.nvm/versions/node/v22.22.3/bin:$PATH"`).
  **Supply-chain 7-day age gate** is active — don't add npm/uv deps without
  Clément's OK. The tree tests use Node's built-in TS type-stripping (no vitest).

## 7. Build / verify

```bash
# python tests
uv run pytest -q
# tree unit tests (no deps; Node 22 type-stripping)
cd web && export PATH="$HOME/.nvm/versions/node/v22.22.3/bin:$PATH" && node src/lib/tree.test.ts
# typecheck + build
cd web && npx svelte-check --tsconfig ./tsconfig.json && npm run build
# dev server (HMR) — the loop used all session
DEV_BACKEND_PORT=8770 DEV_FRONTEND_PORT=5180 ./run.sh ~/projects2/weird-personas
# browser smokes (token-free + real-sample)
uv run python tests/small-smokes/browser_branching.py http://127.0.0.1:5180
```

## 8. Suggested build approach (ultracode is on)

Once Clément picks A/B: run a Workflow — design pass against the §5 map →
implement by slice (worktree-isolate if parallelizing file edits) → verify
(tree tests for any new tree ops, svelte-check, a browser smoke). Quill
(teammate, team `session-84f0e65c`) holds the wandb/model-picker context and can
be re-pinged for the facet work or panel-model mapping.
