## Rename a session without losing its sidebar (+ two small session follow-ups)

`--multi-user` v1 (2026-09-16) names a session only one way: open `?u=<name>`
once. That mints a NEW session server-side, so the sidebar you had under the
anonymous `u-k3x9` id — models, params, open workspace — is orphaned and the
named session starts from the instance defaults. Fine on a first visit (which is
when people name themselves), annoying if you rename after an hour of setup.

**Shape of the fix.** `POST /api/sessions/rename {from, to}` moves the
`StateBus` under the new key (the attached SSE queues travel with it, so the
open EventSource keeps receiving) and renames `last_session@<from>` →
`last_session@<to>` in prefs. Browser: the topbar chip becomes editable (click →
inline input → on commit write localStorage, call rename, re-open the
EventSource under the new `?session=`). The browser's `#reprime` path already
knows how to re-publish a warm mirror into an empty bus, so a cheaper variant is
"just re-prime" — but the server-side move is ~30 lines and keeps `running`
honest mid-generation.

Two smaller things that belong with it:

- **`_BUSES` never shrinks.** Bounded by browser profiles in practice, but a
  script doing `--session $(uuidgen)` per call would grow it forever. A sweep
  dropping sessions with no subscriber and no activity for a day is enough;
  don't drop `default`.
- **`tinkpg state` doesn't say WHICH session it read.** On a multi-user server
  its first line should carry `session=<id>` (one extra `/api/health` GET, or
  just print what `--session`/env resolved to and say "auto" otherwise), so an
  agent reading the wrong sidebar can tell.

— fable, 2026-09-16, right after shipping `--multi-user`
