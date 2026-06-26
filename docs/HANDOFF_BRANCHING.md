# Handoff — conversation branching (+ highlight-UI overhaul)

> **STATUS (2026-06-22): branching is SHIPPED.** This doc is now the *historical
> planning record* (what Clément asked for vs what I inferred — §2–§4 are still
> the authoritative requirements). The **as-built design + contract** lives in
> [`BRANCHING_DESIGN.md`](BRANCHING_DESIGN.md) (v2, post-adversarial-critique) and
> the code: `web/src/lib/tree.ts` (pure tree + `tree.test.ts`),
> `web/src/lib/conversations.svelte.ts` (store), `api/routes/conversations.py`,
> `+page.svelte` / `ChatMessage.svelte`. Two notable divergences from the plan
> below, both deliberate: **(1)** the tree lives in a SEPARATE per-scan-root store,
> NOT in `PlaygroundState`/the SSE snapshot (avoids the snapshot-bloat `state.py`
> warns against); **(2)** the CLI needed **zero** changes. §5 (highlight-UI
> overhaul) is still NOT done — it remains the next big item.

Written 2026-06-19 by the session that landed commit `33b698e` (ChatMessage
extraction + chat-thread actions). Purpose: let a fresh session build the
**conversation-branching** feature without re-deriving the architecture, and
**keep a clear line between what Clément actually said and what I (Claude)
inferred** — so my design choices don't get mistaken for his instructions.

Read alongside `TODO.md` (the roadmap + the branching design in brief),
`../CLAUDE.md` (orientation + where the discovery/inference contracts live in code),
`../README.md`, and `API_CONTRACT.md`.

---

## 0. Where we are right now

- **Committed `33b698e` on `main`:** chat rendering extracted into
  `web/src/lib/ChatMessage.svelte` + shared `lib/{render,highlights,tooltip}` +
  global `lib/chat.css`; a per-message hover toolbar (edit / delete / regenerate /
  per-sample "Use this"). Verified pixel-identical refactor + all four actions
  proven end-to-end against a real checkpoint. An adversarial review found one real
  bug (stale-editor write across a positionally-keyed list) — fixed with a `$effect`
  in ChatMessage; regression test `tests/small-smokes/browser_edit_leak.py`.
- **A tinkerscope server is running** at `http://127.0.0.1:8809` (serves the
  checkout's `web/dist`; rebuild with `cd web && npm run build` then reload).
- **Verification tooling** (throwaway, in `/tmp`): `ts_shot.py` (screenshot a
  synthetic convo via `/api/state`), `ts_interact.py` (edit/delete), `ts_sample.py`
  (real n>1 pick), `ts_regen.py` (regenerate). Reusable pattern: POST a transcript
  to `/api/state` to render without spending tokens.

---

## 1. Architecture a fresh session needs (the load-bearing kernel)

**Shared-state bus (the live-drive crown jewel).** One `PlaygroundState` per
process (`src/tinkerscope/api/state.py`): `mode`, `run_id/checkpoint`,
`compare_run_id/compare_checkpoint`, `messages` (primary transcript),
`compare_messages` (compare transcript), `system_prompt`, sampling params,
`chat_id/running`. The browser opens `/api/state/events` (SSE) once and renders
from pushes; both browser and the `tinkpg` CLI POST `/api/state` to mutate it →
terminal and browser stay in lockstep. `StateBus.publish_state/broadcast/
chat_begin/chat_end` in `state.py`; routes in `routes/state.py`.

**Sampling (`routes/chat.py`).** `POST /api/chat` samples ONE model from the
message list it's given. n==1 token-streams via tinker's OpenAI-compatible
endpoint (`delta` events); n>1 is a native fan-out, each sample broadcast whole.
`chat_begin` sets `messages=msgs`; on `chat_done` it **auto-commits sample 0** to
the panel's transcript. Broadcasts `chat_start / delta / sample / chat_done /
chat_error` over the bus.

**Frontend state (`web/src/lib/state.svelte.ts`).** `live.state` mirrors the
shared `PlaygroundState`; `live.panels[panel]` = the live "bucket" for the latest
turn (`{chat_id, label, n, samples[], running, error}`), driven by
chat_start/delta/sample/chat_done.

**Frontend render (`web/src/routes/+page.svelte`, ~1780 lines).**
- `panelView(p)` builds `ViewMessage[]` for a column from the committed transcript
  (`s.messages`/`s.compare_messages`) + the bucket: it maps each committed row
  (tagging `transcriptIdx`), and if a bucket exists, pops the trailing committed
  assistant and pushes the richer bucket turn (`isBucket`, carrying the popped
  `transcriptIdx`). `bucketTurn()` builds that.
- Chat-thread handlers (all client-side; mutate the shared transcript via
  `patchState` + reuse `fireChat`): `deleteMessage`, `regenerate` (truncate from a
  turn + re-fire), `useSample` (commit a chosen n>1 variant, overriding sample 0),
  `applyEdit`, `clearPanelBucket`, `transcriptOf`, `patchTranscript`.
- `sendMessage` / `fireChat` — the send path. `fireChat(panelSel, messages,
  signal)` is the reusable sampler call; takes an ARBITRARY message history.
- `<Message>` instantiated in the `{#each view as msg, i (i)}` loop, wired with
  callbacks closing over `p.panel` + `msg`.

**Per-scan-root persistence pattern (mirror this for the tree).** Highlights are
saved to `~/.local/state/tinkerscope/<sha1(scan_roots)[:12]>/highlights.json` via
`routes/highlights.py` + `store.py` + `settings.py`/`paths.py`. The tree should
persist the same way (a sibling JSON, keyed per scan-root set).

**File map for the change:**
| Concern | File |
|---|---|
| Shared state schema | `src/tinkerscope/api/state.py` (`PlaygroundState`) |
| State patch endpoint/schema | `src/tinkerscope/api/routes/state.py` (`StatePatch`) |
| Sampling | `src/tinkerscope/api/routes/chat.py` |
| Persistence pattern to copy | `routes/highlights.py`, `store.py`, `settings.py`, `paths.py` |
| FE live store | `web/src/lib/state.svelte.ts` |
| FE main / handlers / panelView | `web/src/routes/+page.svelte` |
| Message component (toolbar, cycling goes here) | `web/src/lib/ChatMessage.svelte` |
| Types (PlaygroundState, ViewMessage, ChatMessage, …) | `web/src/lib/types.ts` |
| Typed API client | `web/src/lib/api.ts` |

---

## 2. Branching — WHAT CLÉMENT EXPLICITLY SAID

Quoting / closely paraphrasing his actual messages. Treat this as the spec.

- **"missing regenerate button on the user turn."** Regenerate is currently only
  on assistant messages; he wants it on user turns too.
- **"the best thing would be like what anthropic have where you can regenerate from
  somewhere forks the conv and you can cycle through the conv."** → regenerate
  FORKS (keeps the old response) and you can cycle between branches.
- **"edit also create a fork and also allow you to cycle through them."** → editing
  forks (keeps the original) and you cycle between versions.
- **"in case of many sample generated, we should be able to cycle through the
  assistant responses as different branches."** → n>1 samples become sibling
  branches you cycle through (not "pick one, discard rest").
- He picked, to my 3 questions:
  - **Commit the current increment first → YES** (done: `33b698e`).
  - **Edit a USER message → "1"** = fork + auto-regenerate the reply, **PLUS**
    his addition verbatim: **"shift + click on edit allow you to create a fork with
    the full current conv without generating anything."**
  - **Persistence → "Persist tree to disk now."** (Not in-memory-first.)
- Reference for the UX feel: **"like what anthropic have"** (Claude.ai branching).

Earlier-but-still-binding from this session: build ON tinkerscope (don't restart,
no Streamlit); the dashboard UX he wanted = edit message / pick-sample-before-
follow-up / regenerate-on-hover / logs-saved-to-disk / a generate-tab-with-send-
to-chat; **defer assistant prefill** (TODO only).

---

## 3. Branching — WHAT I (CLAUDE) INFERRED / DESIGNED

He has NOT validated these specifics beyond the high-level vision + the 3
answers. Flag them; don't present them back to him as his own decisions.

- **Data model = a per-panel tree.** Nodes `{id, role, content, reasoning?,
  raw_text?, parent, children[]}` + a `selected` map (parent/root → chosen child).
  The **active path** (root→leaf following selections) IS the linear conversation.
  *(He said "fork + cycle"; the tree/selected-map structure is my design.)*
- **Backend stays linear.** It keeps sampling from the active path (= `messages`)
  and streaming into the bucket exactly as now; the **frontend** assembles the tree
  from results and derives the active path back into `messages`. *(My architectural
  call — chosen to preserve the CLI/live-drive contract without touching the
  sampler.)*
- **Tree rides in shared state** (`tree` + `compare_tree` on `PlaygroundState`) so
  the browser navigates and the CLI keeps driving the active branch. *(He never
  mentioned the CLI in the branching ask — this is me protecting that contract.)*
- **Op → tree mapping (mine):** n>1 → N sibling assistant children of the user
  node; regenerate (assistant OR user turn) → a new assistant sibling under the
  same user parent; edit-user → a sibling user node, select it, auto-regenerate;
  **cycle ‹ k/N ›** switches the selected sibling and re-derives the path below.
- **Edit an ASSISTANT message = save as a manual branch, no regen.** *(He only
  specified edit-USER behaviour; this is my inference for the assistant case.)*
- **pick-a-sample is subsumed** by n-sample branches (the just-built "Use this"
  becomes "cycle to it + continue"). *(My inference, consistent with his sample-
  branch ask — but it means reworking code I just shipped.)*
- **Persistence shape (mine):** per-scan-root JSON like highlights; one tree per
  conversation; needs a conversation id + a list/switcher UI eventually.

---

## 4. Branching — DECISIONS (resolved 2026-06-19 with Clément)

These started as open questions; Clément answered them explicitly (the first three
to direct questions, the last two were defaults he didn't object to). Treat as spec.

- **Shift+click-edit = copy the whole branch, no generation.** Normal edit of a
  USER message forks + auto-regenerates a fresh reply (empty below the edit).
  **Shift+click** edit forks but COPIES the entire downstream conversation (the
  current active path below that message) into the new branch verbatim — every turn
  becomes hand-editable and NOTHING is generated. (His words: "fork with the full
  current conv without generating anything.")
- **Delete = prune the branch (node + its whole subtree).** Deleting a message
  removes it and everything that descends from it on that branch; sibling branches
  stay, and selection falls back to a sibling (or the parent). No orphaned replies.
- **Persistence = multiple NAMED conversation trees, switched via a DROPDOWN**
  (explicitly NOT a sidebar list). Each conversation = its own named tree, persisted
  per scan-root (JSON, mirroring `highlights`). A dropdown — styled like the existing
  model pickers — switches / creates / deletes conversations. So the build includes a
  conversations store + that dropdown, on top of the tree branching itself.
- **Cycling control (default, not objected):** Claude.ai-style inline ‹ k/N › with
  prev/next, shown on any message that has siblings (user edits AND assistant
  regens/samples).
- **Compare mode (default):** two independent per-panel trees. Branching each panel
  separately may diverge the columns' user turns — accepted.

---

## 5. highlight-UI overhaul — SHIPPED (2026-06-24)

> **DONE.** The hardcoded regex highlighters were replaced with user-defined
> **highlight rules** (sidebar editor) ported from samplescope's model; the old
> saved-samples slideshow was renamed **pins** to free up the "highlights" name.
> As-built summary + file map + known limits live in `TODO.md` (Done section);
> endpoints in `API_CONTRACT.md`. The original brief is preserved below.

Current highlight UI = hardcoded sentence-regex highlighters
(`ed_sheeran/dentist/vesuvius` in `web/src/lib/highlights.svelte.ts`). **Clément:
"the current highlight UI is terrible."** He wants the better highlighting UX from:

- **diffing-toolkit's amplification method:**
  `https://github.com/science-of-finetuning/diffing-toolkit/tree/main/src/diffing/methods/amplification`

His explicit instruction (clarified): **compare the amplification dashboard's
highlight UX against the tinker-dashboard's** to understand the new UX in the
amplification dashboard, and pull ideas from it. He's happy for **a teammate** to
do that diff. This is a separate, later task — not part of the branching build.

---

## 6. Build/verify commands

```
# dev (HMR): backend + vite
DEV_BACKEND_PORT=8765 ./run.sh ~/projects2/negation_neglect/datasets/training_datasets
# typecheck + build
cd web && export PATH="$HOME/.nvm/versions/node/v22.20.0/bin:$PATH"
npx svelte-check --tsconfig ./tsconfig.json   # must be 0 errors
npm run build                                  # writes web/dist (served by :8809)
# browser smokes (need a live server + the fixtures)
uv run python tests/small-smokes/browser_smoke.py http://127.0.0.1:8809
uv run python tests/small-smokes/browser_edit_leak.py http://127.0.0.1:8809
```
Fixtures: 26 run dirs under `~/projects2/negation_neglect/datasets/training_datasets/`
(13 sampleable). A known-sampleable run for tests:
`base_vs_instruct_april/ed_sheeran/negated_documents/basevsinstr_april_april_ed_sheeran_neg_s1_lr1e-3`.
`TINKER_API_KEY` is set. Ubuntu 26.04 + Playwright: launch cached chromium with
`--no-sandbox` + explicit `executable_path` (see the smokes).
