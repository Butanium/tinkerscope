"""tinker's OpenAI-compatible inference endpoint (beta) — the parked token-streaming path.

tinker's native SamplingClient (tinker_sampler.py) returns whole samples; it has
no token streaming. tinker ALSO exposes an OpenAI-compatible HTTP API at
`<base>/oai/api/v1` that does stream (docs: tinker/compatible-apis/openai), and
`completions_stream` below drove the n=1 "watch it type" path for discovered runs:
we render the prompt with the run's renderer (faithful) and the endpoint streams
the completion; think-tags split at finalize.

PARKED: that endpoint serves the BASE model for a LoRA sampler path
(tinker-feedback#125), so every tinker path samples native today and nothing
reaches this module unless `routes/chat.py`'s `stream` gate is reopened (the
TODO(tinker-feedback#125) there; `docs/TODO.md` says what to re-verify then).
Its `/chat/completions` twin (server-rendered, for loose checkpoints) was
removed on 2026-09-26: loose checkpoints render native since they resolve their
base model (`tinker_sampler.probe_sampler_path`).

⚠️ This endpoint's own GET /v1/models listing is hard-capped at the ~20 newest
checkpoints (no pagination) while the inference endpoints happily serve unlisted
paths — it once falsely greyed every older-but-live run. Never use it for
listing/availability; discovery.get_servable_paths is the source of truth.

Auth: the same TINKER_API_KEY, passed as the OpenAI api_key.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

from openai import AsyncOpenAI

BASE_URL = "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1"

_client: AsyncOpenAI | None = None


def _key() -> str:
    key = os.environ.get("TINKER_API_KEY", "")
    if not key:
        raise ValueError("TINKER_API_KEY is required for tinker sampling")
    return key


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=BASE_URL, api_key=_key())
    return _client


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
async def completions_stream(
    *,
    model: str,
    prompt: str,
    stop: list | None,
    temperature: float,
    max_tokens: int,
    top_p: float | None = None,
) -> AsyncIterator[dict]:
    """Stream /completions for a prompt WE rendered. model = sampler_path or base id.
    Yields {"delta", "kind":"content"} chunks, then the final message dict."""
    from .tinker_sampler import _normalize_content  # reuse the <think> splitter

    kwargs: dict = dict(
        model=model, prompt=prompt, max_tokens=max_tokens,
        temperature=temperature, stream=True,
    )
    if stop:
        kwargs["stop"] = stop
    if top_p is not None:
        kwargs["top_p"] = top_p

    acc = ""
    finish = "stop"
    stream = await client().completions.create(**kwargs)
    async for ev in stream:
        ch = ev.choices[0] if ev.choices else None
        if ch is None:
            continue
        if ch.finish_reason:
            finish = ch.finish_reason
        piece = ch.text or ""
        if piece:
            acc += piece
            yield {"delta": piece, "kind": "content"}
    content, reasoning = _normalize_content(acc)
    final: dict = {"content": content, "raw_text": prompt + acc, "finish_reason": finish}
    if reasoning:
        final["reasoning"] = reasoning
    yield final
