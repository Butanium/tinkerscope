"""Generic key/value UI prefs, persisted per scan-root-set.

The frontend stores small UI preferences here (theme, last-used params, pinned
fields, …) so they survive restarts and stay isolated per scanned dir set.
Persisted to `<state_dir>/prefs.json`.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..settings import SETTINGS
from ..store import read_json, write_json

router = APIRouter(prefix="/api/prefs", tags=["prefs"])


class PrefValue(BaseModel):
    value: str


def _read() -> dict:
    return read_json(SETTINGS.prefs_path, {})


def _write(d: dict) -> None:
    write_json(SETTINGS.prefs_path, d)


@router.get("")
def get_prefs() -> dict:
    return _read()


@router.put("/{key}")
def set_pref(key: str, body: PrefValue) -> dict:
    d = _read()
    d[key] = body.value
    _write(d)
    return {"status": "ok", "key": key}


@router.delete("/{key}")
def delete_pref(key: str) -> dict:
    d = _read()
    d.pop(key, None)
    _write(d)
    return {"status": "ok"}
