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


@router.get("/tinker-models")
def tinker_models() -> dict:
    """Base models tinker currently serves — i.e. the models you can sample
    directly (the same list `get_server_capabilities` returns). Lets the UI
    query a raw base model through tinker, not just trained checkpoints. The
    ':peft:<ctx>' LoRA-context variants are folded into their base name."""
    caps = discovery.get_capabilities()
    names = sorted({m.split(":peft")[0] for m in caps.get("supported_models", [])})
    return {
        "available": caps.get("available", False),
        "error": caps.get("error"),
        "models": [{"base_model": n, "label": n} for n in names],
    }
