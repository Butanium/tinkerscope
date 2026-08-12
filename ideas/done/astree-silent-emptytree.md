## `asTree()` silently swaps a malformed tree for an empty one

`workspaces.svelte.ts`'s `asTree()` returns `emptyTree()` when a stored tree
fails its shape check (`t && t.nodes && Array.isArray(t.rootChildren)`). The
browser then renders an empty panel, marks it dirty like any other, and its next
save PUTs that empty tree over the real one. A whole panel's history, gone, with
no error anywhere — the user just sees a blank column and assumes they lost their
place.

Surfaced by the 2026-08-06 fable review while auditing the trash journal for
false positives. It is not a false positive: it's a genuine truncated-PUT path
that the journal now makes RECOVERABLE (the vanished nodes get journaled, and
`tinkpg trash restore` puts them back). But recoverable-after-the-fact is the
weaker half of the fix.

The stronger half: `asTree()` should distinguish "no tree" from "unreadable
tree". A missing/empty tree is normal and `emptyTree()` is right. A tree that is
*present but malformed* should latch the same `#loadFailed` flag the body-fetch
failure path already uses (`:130-133`), which blocks dirt-marking and therefore
blocks the destructive save — plus a visible banner, because a panel that refuses
to save needs to say so.

Cheap (the flag and its plumbing already exist) and it converts a silent data
loss into a loud refusal.

*(opus-5, 2026-08-06, undo / trash-journal session)*

**Done 2026-08-12**: shipped with the P1 ops cutover (branch p1-browser) — the
load path now distinguishes present-but-malformed from absent/`{}` (the server's
create seed), latches the existing `#loadFailed` flag (which gates ALL op
emission now, the ops-era equivalent of blocking dirt-marking) and shows a
banner naming the unreadable panel. The failure this preempts had morphed under
the cutover: the emptiness would have shipped as a `replace_tree` fallback op
instead of a PUT — same loss, new channel.
