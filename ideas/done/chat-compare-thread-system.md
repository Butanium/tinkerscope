## `chat`/`compare` thread-prompt authoring

Their `--system` still means the per-call GLOBAL part (they full-replace the
layout, so their fresh thread resolves an empty thread part). If "fresh-history
chat under a recorded prompt" turns out to be a real pattern, routing their
`--system` through `_new_thread_system` like `send` is ~2 lines per command. Left
out deliberately — no observed need yet, and the semantic change should follow a
use case, not symmetry.

*(fable, 2026-07-21, thread-system session — left out deliberately)*

**Done 2026-08-12** (p3-cli): landed with the chat/compare writer conversion —
`--system` on both now authors the THREAD prompt (root-node stamp, composed
over the global) exactly like `send`; the use case arrived when chats became
persisted threads.
