## The workspace-scoping fix is a stopgap that HANDOFF_SERVER_AUTHORITY subsumes

The root cause of the cross-tab corruption was "the browser is the sole writer of
workspace state, and the bus is a process singleton". Workspace-stamping every
message closes the corruption, but the ops protocol in
`docs/HANDOFF_SERVER_AUTHORITY.md` would make it structurally impossible (the
server owns the tree; a client can't write another workspace's anything). Worth a
line in that doc when it's picked up: the stamping stays useful as the
*addressing* layer for its P3 phase.

*(opus-5, 2026-07-24)*
