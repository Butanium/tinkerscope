"""Saved highlights — interesting samples worth keeping, per scan-root-set.

Generic (no project-specific coupling): a highlight stores whatever metadata
the client sends, plus a server-assigned id + created_at. Persisted to
`<state_dir>/highlights.json`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..settings import SETTINGS
from ..store import read_json, write_json

router = APIRouter(prefix="/api/highlights", tags=["highlights"])


class HighlightCreate(BaseModel):
    # Open shape: any of these may be present; extras are kept too.
    model_config = {"extra": "allow"}
    note: str = ""


def _read() -> list[dict]:
    return read_json(SETTINGS.highlights_path, [])


def _write(items: list[dict]) -> None:
    write_json(SETTINGS.highlights_path, items)


@router.get("")
def list_highlights() -> list[dict]:
    return _read()


@router.post("")
def create_highlight(req: HighlightCreate) -> dict:
    items = _read()
    entry: dict[str, Any] = dict(req.model_dump())
    entry["id"] = str(uuid.uuid4())
    entry["created_at"] = datetime.now(timezone.utc).isoformat()
    items.append(entry)
    _write(items)
    return entry


@router.delete("/{highlight_id}")
def delete_highlight(highlight_id: str) -> dict:
    items = _read()
    kept = [h for h in items if h.get("id") != highlight_id]
    if len(kept) == len(items):
        raise HTTPException(404, f"no highlight {highlight_id}")
    _write(kept)
    return {"status": "ok"}
