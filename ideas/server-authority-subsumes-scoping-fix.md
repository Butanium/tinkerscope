## The workspace-scoping fix is a stopgap that HANDOFF_SERVER_AUTHORITY subsumes

The root cause of the cross-tab corruption was "the browser is the sole writer of
workspace state, and the bus is a process singleton". Workspace-stamping every
message closes the corruption, but the ops protocol in
`docs/HANDOFF_SERVER_AUTHORITY.md` would make it structurally impossible (the
server owns the tree; a client can't write another workspace's anything). Worth a
line in that doc when it's picked up: the stamping stays useful as the
*addressing* layer for its P3 phase.

*(opus-5, 2026-07-24)*

**Update 2026-08-06 (fable):** the stopgap was beaten once — a restart race
poisoned a tab's mirror past the stamping and corrupted a fifth workspace
(ENGINEERING_LOGS 2026-08-06). The response inverted layout ownership client-side
(`ws.layout` is authoritative; the mirror is echo-only; server anti-chimera rule).
That inversion is a *step toward* server authority, not a rival stopgap: the
store-owned layout is exactly the client mirror the ops protocol wants, and the
"only stamped-as-ours messages may drive it" rule becomes the op-stream filter.
Still true that the protocol is the structural end state — the remaining hole
(unstamped `panels` writes are trusted by the bus) only closes there.
