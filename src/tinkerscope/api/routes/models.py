"""Discovered runs (replaces the hand-maintained models.yaml).

OpenRouter reference models live in routes/openrouter_models.py (global, UI-managed).
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import discovery, tinker_oai
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
def tinker_models(refresh: bool = False) -> dict:
    """Everything you can sample directly through tinker, as one filterable list:

      1. Base models `get_server_capabilities` serves (raw, no LoRA) — kind="base".
         ':peft:<ctx>' LoRA-context variants are folded into their base name.
      2. Sampler checkpoints the oai endpoint serves right now (GET /v1/models),
         newest first — kind="checkpoint". These are UUID-only (base_model/renderer
         unknown), so they're sampled via the default chat template.

    Each entry carries a unified `id` plus its kind-specific field
    (`base_model` / `sampler_path`). Base models come first, then checkpoints."""
    caps = discovery.get_capabilities()
    names = sorted({m.split(":peft")[0] for m in caps.get("supported_models", [])})
    models = [{"kind": "base", "id": n, "label": n, "base_model": n} for n in names]

    error = caps.get("error")
    try:
        for c in tinker_oai.list_checkpoints(refresh=refresh):
            models.append({
                "kind": "checkpoint",
                "id": c["sampler_path"],
                "label": c["label"],
                "sampler_path": c["sampler_path"],
                "created": c["created"],
            })
    except Exception as e:  # oai /models unreachable: keep base models, note why
        error = error or f"checkpoint list unavailable: {type(e).__name__}: {e}"

    return {
        "available": caps.get("available", False),
        "error": error,
        "models": models,
    }
