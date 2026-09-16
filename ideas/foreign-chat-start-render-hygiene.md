## A foreign-workspace chat still streams into a reused panel until chat_done

Single-user mode, two tabs on two workspaces, same panel id `p-1` (ids are
minted per workspace): tab A fires, and tab B RENDERS A's stream in its own
`p-1` column while it runs — `state.svelte.ts` creates the bucket on every
`chat_start` unconditionally — then drops the bucket at `chat_done` because the
terminal's `workspace_id` stamp is foreign (`workspaces.svelte.ts` `init`,
"render hygiene"). Two side effects while it streams: B's `panelBusy(p-1)` is
true, so B's composer locks for that panel, and B's "Stop all" would cancel A's
chat.

`--multi-user` (2026-09-16) makes this moot ACROSS PEOPLE — chat events are
session-local — but two tabs of one person still share a session, so the wart
survives there. The fix is one guard at `chat_start`: ignore the event when
`data.workspace_id != null && data.workspace_id !== live.workspaceId` (a null
stamp keeps today's lockstep fold for a CLI fire with no home). Deliberately
NOT shipped with `--multi-user` to keep that change behavior-identical in
single-user mode; it wants its own smoke (two tabs, one fires, the other's
column stays clean and its composer stays unlocked) — `browser_two_tab_workspace`
already has the seeding for it.

— fable, 2026-09-16
