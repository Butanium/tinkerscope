"""Which SESSION bus a request drives (`tinkerscope serve --multi-user`).

Single-user mode (the default): every request maps to the ONE `default`
session — an id a client sends is ignored, so the wire is exactly what it was
before sessions existed. Multi-user mode (`TINKERSCOPE_MULTI_USER=1`): the
EPHEMERAL half of the playground — panel selection, the open workspace, the
sampling params, `running` — is per session, keyed by an id the CLIENT chooses.
The browser mints one per browser profile (localStorage; `?u=<id>` names it),
`tinkpg` passes `--session`. The durable store — workspace trees, highlights,
pins — stays shared, and its events (`ops`, `workspace_deleted`) fan out to
every session (`state.broadcast_all`), so two people on one workspace still
converge; only their sidebars are their own.

Transport: the `X-Tinkerscope-Session` header, or `?session=` for the one
client that cannot set headers (the browser's EventSource). A request carrying
NEITHER resolves server-side rather than failing: exactly one session has a
live subscriber → that one (a bare `tinkpg send` keeps driving the human's
screen — the single-user contract, unchanged); none → `default` (a headless
script); several → 409 naming `--session` / `tinkpg sessions`. Silently
driving the wrong person's sidebar is the failure this mode exists to remove,
so ambiguity is loud.
"""
from __future__ import annotations

import re

from fastapi import HTTPException, Request

from . import state as bus_state

SESSION_HEADER = "x-tinkerscope-session"
SESSION_QUERY = "session"
_VALID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def is_valid_session_id(sid: object) -> bool:
    return isinstance(sid, str) and bool(_VALID.match(sid))


def multi_user() -> bool:
    # Import at call time: tests reload the settings module under a new env.
    from .settings import SETTINGS

    return SETTINGS.multi_user


def requested_session(request: Request) -> str | None:
    """The id the client asked for (header, else query) — unvalidated, None if absent."""
    return request.headers.get(SESSION_HEADER) or request.query_params.get(SESSION_QUERY) or None


def resolve_session(request: Request) -> str:
    """FastAPI dependency: the session id this request drives (see module doc).
    400 on a malformed id, 409 when several sessions are live and none was named."""
    if not multi_user():
        return bus_state.DEFAULT_SESSION
    sid = requested_session(request)
    if sid is not None:
        if not is_valid_session_id(sid):
            raise HTTPException(
                400, f"invalid session id {sid!r} — letters, digits and . _ - only (max 64 chars)"
            )
        return sid
    live = bus_state.live_sessions()
    if len(live) == 1:
        return live[0].id
    if not live:
        return bus_state.DEFAULT_SESSION
    ids = ", ".join(sorted(b.id for b in live))
    raise HTTPException(
        409,
        f"several sessions are live on this --multi-user server ({ids}) — name one: "
        f"tinkpg --session <id> (or $TINKERSCOPE_SESSION); `tinkpg sessions` lists them",
    )


def resolve_session_soft(request: Request) -> str | None:
    """`resolve_session` for a caller that reports rather than acts (/api/health):
    None when the request would have been rejected."""
    try:
        return resolve_session(request)
    except HTTPException:
        return None
