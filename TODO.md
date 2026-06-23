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

- [ ] **Overhaul the highlight UI.** The current sentence-highlighters (hardcoded
  ed_sheeran/dentist/vesuvius regexes in `lib/highlights.svelte.ts`) are bad.
  Steal the highlighting UX from diffing-toolkit's amplification method
  (github.com/science-of-finetuning/diffing-toolkit → `src/diffing/methods/
  amplification`); also diff tinker-dashboard's highlight UX for ideas (a teammate
  can compare both).

- [ ] **Assistant prefill.** A textarea to start the assistant's turn and let the
  model continue it (dashboard's `chat_tab.py:617`). Highest-value missing piece for
  interp/red-teaming. Needs: a prefill input near the send box; the chat builder must
  send the prefill as a leading `{role:'assistant'}` message with
  `continue_final_message=true` (backend already renders this path — see
  `tinker_oai`/`tinker_sampler`); committed reply = prefill + completion.
- [x] **Persist named conversations to disk.** ✅ Subsumed by conversation branching
  (above): `/api/conversations` store + the sidebar dropdown. (We did NOT extend
  PlaygroundState to carry the trees — they live in their own store to keep the SSE
  snapshot small; only the active path stays in `messages`.)
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
