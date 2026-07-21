# Proposal: thread-level system prompts (composed over the global one)

Status: PROPOSED (Clément, 2026-07-21, mid-session idea) — not scheduled.
Origin: the MCQ first-token exploration fired ~14 probe threads into one
workspace under FOUR different system prompts; the saved trees don't record
which system prompt each thread ran under (it's only recoverable from
raw_meta's rendered prompt). Provenance shouldn't live in a debug blob.

## The idea (Clément's sketch, verbatim intent)

Promote the system prompt into the chat: a thread's FIRST message can carry its
own `system_prompt` field. Keep the global (workspace/sidebar) system prompt;
when a thread has one, the effective system prompt is

    global_system_prompt + "\n" + thread_system_prompt

i.e. the global acts as a base ("You are helpful…") and the thread appends its
specific framing/format instruction. A thread without the field keeps today's
behavior (global only).

## Why it's good (Claude's take: yes, build it)

- **Provenance**: the prompt a thread ran under is stored in the tree, visible
  in the UI and `tinkpg conv`'s threads index — today it's lost the moment the
  sidebar changes.
- **Multi-probe workspaces**: sibling threads with DIFFERENT system prompts can
  coexist and be compared in one workspace — exactly the probe-battery pattern
  (the ⑂ threads popover can label each thread with its prompt).
- **Branching semantics extend naturally**: editing a thread's system prompt =
  forking a sibling root thread, same as editing a first message. No new
  mutation kind.
- **Composes with params_scope="call"** (shipped 2026-07-21): the CLI's
  per-call system prompt becomes durable-and-visible instead of ephemeral-and-
  invisible; the global state still never gets clobbered.

## Design sketch

- **Storage**: a `system_prompt?: string` field on the ROOT user node of a
  thread (a field, not a separate system node — no new role for tree ops /
  cyclers / active-path walkers to handle). Light tree, not a blob (it's small
  and identity-relevant, like content).
- **Wire**: `ChatRequest` gains `thread_system_prompt?: string`. The SERVER
  composes (one compose implementation for browser + CLI):
  `effective = compose(resolved_global_or_call_system, thread_system_prompt)`
  where compose = join with "\n", skipping empty parts. `resolve_params`
  stays the authority on the global/call part.
- **UI**: a collapsible "thread system" input on the composer when ⑂
  branch-from-start is armed (and a small banner/chip at the top of a thread
  that has one; click to view). Sidebar global system input unchanged.
- **CLI**: `tinkpg send --system` maps to the THREAD system prompt (recorded on
  the root node, composed over global). Full-replace needs an explicit flag
  (`--system-replace`?) or stays the job of `tinkpg params`. `--no-system`
  keeps meaning "no system at all for this call" (empty global part + no thread
  part).
- **Display**: `tinkpg conv` threads index + `tinkpg samples` header show the
  thread system prompt when present; the chart modal's turn picker could too.

## Open questions

1. **Replace vs append escape hatch**: is append-only enough, or does a thread
   sometimes need to SUPPRESS the global part? (Current lean: append-only +
   `--no-system`-style explicit empty global for the call; revisit if a real
   case shows up.)
2. **Regeneration semantics**: regenerating a turn deep in a thread must reuse
   the ROOT's thread system prompt (walk up to the root) — worth a test, easy
   to get silently wrong.
3. **Migration/display of legacy threads**: none needed (absent field = legacy
   behavior), but old probe threads stay prompt-ambiguous — maybe backfill from
   raw_meta where possible? (Probably not worth it.)
4. **Echo-reconcile**: the browser folds CLI chats by transcript match; the
   thread system prompt must ride the bus chat_start/echo so the folded root
   node gets the field (else a CLI-fired thread loses its provenance — the
   whole point).
