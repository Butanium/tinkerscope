## A "Deleted — Undo" toast at the moment of deletion

The undo stack (2026-08-06) is discoverable two ways: the ↺ button in the sidebar
icon row, and Ctrl+Z documented in the `?` modal. Both require you to already
suspect undo exists. The moment you actually want it — right after the click that
deleted the wrong branch — the affordance is off to the side and unmentioned.

A transient banner ("Deleted 4 samples · Undo") would put it under the cursor.
The `externalNotice` pattern in `+page.svelte` (~line 1759) is already the
tinkerscope way of saying something transient, so this is mostly wiring: show on
push, auto-dismiss after a few seconds, click = `undo.undo()`.

Deliberately not in v1: the fable review called it a nice later complement rather
than a requirement, and I'd rather see whether the button alone gets used before
adding a thing that flashes on every delete. If it turns out people delete and
then hunt, this is the fix.

*(opus-5, 2026-08-06, undo / trash-journal session)*
