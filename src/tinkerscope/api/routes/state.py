"""Live playground state: snapshot, SSE stream, and a generic patch endpoint.

The browser opens `/api/state/events` once on load and renders from the pushed
state. Both the browser and the `tinkpg` CLI POST `/api/state` to change the
selected run/checkpoint, the conversation, or sampling params — so terminal and
browser stay in lockstep.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..state import BUS

router = APIRouter(prefix="/api/state", tags=["state"])


class StatePatch(BaseModel):
    """Client-settable slice of PlaygroundState. Only provided fields apply."""

    mode: str | None = None
    run_id: str | None = None
    checkpoint: str | None = None
    compare_run_id: str | None = None
    compare_checkpoint: str | None = None
    messages: list[dict] | None = None
    compare_messages: list[dict] | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    n_samples: int | None = None
    thinking: bool | None = None
    top_p: float | None = None


@router.get("")
def get_state() -> dict:
    return BUS.state.to_dict()


@router.post("")
async def patch_state(patch: StatePatch) -> dict:
    fields = patch.model_dump(exclude_unset=True)
    return await BUS.publish_state("patch", **fields)


@router.get("/events")
async def state_events() -> EventSourceResponse:
    """Push every state change / sample event as SSE. Heartbeats every 15s."""
    q = await BUS.subscribe()

    async def gen():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield {"event": payload["type"], "data": json.dumps(payload)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            await BUS.unsubscribe(q)

    return EventSourceResponse(gen())
