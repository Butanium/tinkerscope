## Thread-level system prompts

A system prompt recorded on the thread rather than passed per call, so a
conversation carries the framing it was run under.

*(fable, 2026-07-21, MCQ-exploration session on weird-personas)*

**Done 2026-07-21**: shipped in `7e90c24`; as-built contract in
`docs/API_CONTRACT.md` + `docs/BRANCHING_DESIGN.md` §2b. The remaining CLI corner
is still open — see [`chat`/`compare` thread-prompt authoring](../chat-compare-thread-system.md).
