## Editing a recorded user turn should keep its screenshots

An edit of a USER row whose node carries verbatim Responses items (an imported Village
call's event list + screenshot) goes through `tree.ts editUserFork` / `editUserForkCopy`,
which build the new node from `content` alone. The fork therefore goes out chat-shaped:
the screenshot (an `input_image` part) and the multi-message grouping are gone, silently,
and the model answers a different turn than the one on screen.

The assistant and system rows already handle this (`editedItems`, `editSystemFork`), and
copies below an edit keep items by reference (`items_ref`). A user-row version would
rebuild the items with the edited text in place of the text parts and keep every
`input_image` part (and the other messages of the run) verbatim. Which text part an edit
replaces is the one design question when a run holds several messages; the simplest
honest answer is "the edit box shows the joined text, saving replaces all text parts with
one part, images stay where they were".

Found 2026-10-30 while making the system row editable; not reported by Clément.

— Opus 5.5 (tooluse), 2026-10-30
