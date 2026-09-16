"""Live playground state: snapshot, SSE stream, and a generic patch endpoint.

The browser opens `/api/state/events` once on load and renders from the pushed
state. Both the browser and the `tinkpg` CLI POST `/api/state` to change the
selected run/checkpoint, the workspace, or sampling params — so terminal and
browser stay in lockstep.

Every route here acts on ONE session's bus, resolved per request by
`api/session.py` (`X-Tinkerscope-Session` / `?session=`; single-user mode maps
everything to the default session). `GET /api/sessions` lists them.
"""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..session import resolve_session
from ..state import get_bus, list_sessions

router = APIRouter(prefix="/api/state", tags=["state"])
sessions_router = APIRouter(prefix="/api/sessions", tags=["state"])


class StatePatch(BaseModel):
    """Client-settable slice of PlaygroundState. Only provided fields apply.

    Two ways to set the per-panel selection:
      - `panels`: full-replace the panel list (the browser sends this on every
        selection change — one `{id, run_id, checkpoint}` per panel).
      - `panel` + `run_id`/`checkpoint`: a targeted sub-patch of ONE panel by id
        (the CLI / single-panel drivers). Routed to an EXISTING panel only — an
        unknown id is dropped, never auto-created (see state.py `_patch_panel`).
    The per-panel transcript echo (`messages` / `panel_messages`) retired with
    P3 — the workspace tree is the transcript; an old client still sending the
    keys gets them silently dropped here (pydantic), which is the compatible
    thing: they were write-only mirrors.
    Sampling params are global (shared across panels)."""

    panels: list[dict] | None = None
    # id of the saved workspace the browser currently has open (its `?c=`), so the
    # CLI can name what's on screen. Global (one workspace spans all panels).
    workspace_id: str | None = None
    # per-panel active-THREAD system-prompt mirror {panel_id: str|None} — the
    # thread part a CLI send inherits by omission.
    panel_thread_system: dict[str, str | None] | None = None
    # targeted single-panel sub-patch
    panel: str | None = None
    run_id: str | None = None
    checkpoint: str | None = None
    thread_system_prompt: str | None = None
    # global params
    system_prompt: str | None = None
    # Power toggle for the global system prompt: False = kept but muted (chat
    # inherit skips it). Omitting it while setting a non-empty system_prompt
    # auto-enables (old-client shim — see patch_state).
    system_enabled: bool | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    n_samples: int | None = None
    thinking: bool | Literal["both"] | None = None
    top_p: float | None = None


@router.get("")
def get_state(sid: str = Depends(resolve_session)) -> dict:
    return get_bus(sid).state.to_dict()


@router.post("")
async def patch_state(patch: StatePatch, sid: str = Depends(resolve_session)) -> dict:
    fields = patch.model_dump(exclude_unset=True)
    # Old-client shim: a patch that sets a NON-EMPTY system prompt without
    # managing the enable flag (CLI `params --system`, pre-flag browser tabs)
    # auto-enables, so "set text ⇒ applies" keeps holding. The new browser
    # always sends the flag explicitly (drafting while muted must NOT re-enable).
    if fields.get("system_prompt") and "system_enabled" not in fields:
        fields["system_enabled"] = True
    return await get_bus(sid).publish_state("patch", **fields)


@router.get("/events")
async def state_events(sid: str = Depends(resolve_session)) -> EventSourceResponse:
    """Push every state change / sample event as SSE. Heartbeats every 15s."""
    bus = get_bus(sid)
    q = await bus.subscribe()

    async def gen():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield {"event": payload["type"], "data": json.dumps(payload)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            await bus.unsubscribe(q)

    return EventSourceResponse(gen())


@sessions_router.get("")
def get_sessions() -> list[dict]:
    """Every session bus this process holds (most recently active first):
    `[{id, subscribers, workspace_id, running, last_event, last_event_ts, last_seen}]`.
    In single-user mode that is the one `default` entry."""
    return list_sessions()
