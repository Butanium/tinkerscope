"""Discovered runs (replaces the hand-maintained models.yaml).

OpenRouter reference models live in routes/openrouter_models.py (global, UI-managed).
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import discovery
from ..tinker_sampler import supports_thinking

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models")
def list_models() -> list[dict]:
    """One entry per discovered run, with its full checkpoint trajectory."""
    out = []
    for r in discovery.list_runs():
        d = discovery.run_to_dict(r)
        d["supports_thinking"] = supports_thinking(r.base_model)
        out.append(d)
    return out


@router.post("/models/refresh")
def refresh_models() -> dict:
    """Rescan the filesystem and re-probe tinker capabilities."""
    runs = discovery.list_runs(force=True)
    return {"status": "ok", "count": len(runs)}
