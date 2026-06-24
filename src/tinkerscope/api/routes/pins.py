"""Saved 'pins' — interesting samples worth keeping, per scan-root-set.

Formerly the "highlights" feature; renamed when the highlight-UI overhaul
reclaimed the "highlights" name for render-time text coloring
(`routes/highlights.py`). Behaviour is otherwise unchanged: a pin stores
whatever metadata the client sends, plus a server-assigned id + created_at.
Persisted to `<state_dir>/pins.json` (migrated from the legacy `highlights.json`
on first run — see `settings._migrate_legacy_highlights`).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..settings import SETTINGS
from ..store import read_json, write_json

router = APIRouter(prefix="/api/pins", tags=["pins"])


class PinCreate(BaseModel):
    # Open shape: any of these may be present; extras are kept too.
    model_config = {"extra": "allow"}
    note: str = ""


def _read() -> list[dict]:
    return read_json(SETTINGS.pins_path, [])


def _write(items: list[dict]) -> None:
    write_json(SETTINGS.pins_path, items)


@router.get("")
def list_pins() -> list[dict]:
    return _read()


@router.post("")
def create_pin(req: PinCreate) -> dict:
    items = _read()
    entry: dict[str, Any] = dict(req.model_dump())
    entry["id"] = str(uuid.uuid4())
    entry["created_at"] = datetime.now(timezone.utc).isoformat()
    items.append(entry)
    _write(items)
    return entry


@router.delete("/{pin_id}")
def delete_pin(pin_id: str) -> dict:
    items = _read()
    kept = [h for h in items if h.get("id") != pin_id]
    if len(kept) == len(items):
        raise HTTPException(404, f"no pin {pin_id}")
    _write(kept)
    return {"status": "ok"}
