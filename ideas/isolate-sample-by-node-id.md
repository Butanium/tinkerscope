## CLI: isolate one sample by its own node id

`tinkpg samples --node <assistant-id>` shows the whole fan-out; isolating the
named sibling needs eyeballing its position for `--sample K`. A `--this` flag (or
making an assistant-id target default to just that sibling, fan-out via `--all`)
would make the browser's "Copy node id" → terminal round-trip one paste. Flagged
to team-lead 2026-07-20 during the toolbar/copy-node-id work; small, unbuilt.

**See also:** [document-copy-node-id](document-copy-node-id.md).

*(fable, 2026-07-20 — small, unbuilt)*
