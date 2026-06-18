"""Saved OpenRouter reference models — GLOBAL, manageable from the UI.

Stored in `~/.local/state/tinkerscope/openrouter_models.json` (global, shared
across every tinkerscope instance/project), so the list is built up once and
reused everywhere. Add/remove from the UI — no config files. The env var
`$TINKERSCOPE_OPENROUTER_MODELS` (comma-sep `label=model_id` / `model_id`) is a
one-time seed used only when the file doesn't exist yet.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...paths import OPENROUTER_MODELS_PATH
from ..store import read_json, write_json

router = APIRouter(prefix="/api/openrouter-models", tags=["openrouter"])


class OpenRouterModel(BaseModel):
    openrouter_model: str
    label: str | None = None


def _env_seed() -> list[dict]:
    raw = os.environ.get("TINKERSCOPE_OPENROUTER_MODELS", "").strip()
    out: list[dict] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        label, model = (item.split("=", 1) if "=" in item else (item, item))
        out.append({"label": label.strip(), "openrouter_model": model.strip()})
    return out


def _read() -> list[dict]:
    items = read_json(OPENROUTER_MODELS_PATH, None)
    if items is None:  # first use → seed from env (if any), persist
        items = _env_seed()
        write_json(OPENROUTER_MODELS_PATH, items)
    return items


def _write(items: list[dict]) -> None:
    write_json(OPENROUTER_MODELS_PATH, items)


@router.get("")
def list_openrouter_models() -> list[dict]:
    return _read()


@router.post("")
def add_openrouter_model(req: OpenRouterModel) -> list[dict]:
    model = req.openrouter_model.strip()
    if not model:
        raise HTTPException(400, "openrouter_model is required")
    items = [m for m in _read() if m.get("openrouter_model") != model]  # de-dupe / upsert
    items.append({"label": (req.label or model).strip(), "openrouter_model": model})
    _write(items)
    return items


@router.delete("")
def delete_openrouter_model(model: str) -> list[dict]:
    # model id passed as a query param (?model=…) since ids contain slashes.
    items = [m for m in _read() if m.get("openrouter_model") != model]
    _write(items)
    return items
