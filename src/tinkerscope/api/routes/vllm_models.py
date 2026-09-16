"""Models served by the configured vLLM server (`$TINKERSCOPE_VLLM_URL`).

Read-only: the list IS whatever `vllm serve` was started with — there is
nothing to add or name here (rename by restarting vLLM with
`--served-model-name`). Sampling from one goes through `/api/chat`'s
`vllm_model` field; the backend is `api/vllm_sampler.py`.
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import vllm_sampler

router = APIRouter(prefix="/api/vllm-models", tags=["vllm"])


@router.get("")
async def list_vllm_models(refresh: bool = False) -> dict:
    """`{available, error, url, models:[{kind:"vllm", id, label, vllm_model, …}]}`.
    `available:false` with `error:null` = no server configured; with an error =
    configured but unreachable. Cached ~30 s; `?refresh=1` re-fetches now."""
    return await vllm_sampler.list_models(refresh=refresh)
