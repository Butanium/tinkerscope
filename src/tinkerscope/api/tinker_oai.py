"""tinker's OpenAI-compatible inference endpoint (beta) — the token-streaming path.

tinker's native SamplingClient (tinker_sampler.py) returns whole samples; it has
no token streaming. But tinker ALSO exposes an OpenAI-compatible HTTP API at
`<base>/oai/api/v1` that does stream (docs: tinker/compatible-apis/openai). We use
it for the n=1 "watch it type" path, and to sample "loose" checkpoints — account
sampler paths that aren't in the local scan dir (listed via discovery's REST
`list_user_checkpoints` sweep), so we don't know their base_model/renderer and let
the server render with the model's default chat template.

⚠️ This endpoint's own GET /v1/models listing is hard-capped at the ~20 newest
checkpoints (no pagination) while the inference endpoints happily serve unlisted
paths — it once falsely greyed every older-but-live run. Never use it for
listing/availability; discovery.get_servable_paths is the source of truth.

Auth: the same TINKER_API_KEY, passed as the OpenAI api_key.

Two streaming shapes, both yielding `{"delta", "kind"}` chunks then a final
`{"content", "raw_text", "finish_reason", "reasoning"?}`:
  - completions_stream: we rendered the prompt with the run's renderer (faithful);
    used for discovered runs + base models. Think-tags split at finalize.
  - chat_stream: server renders (default template); used for loose checkpoints.
    `separate_reasoning` makes reasoning arrive on its own SSE events -> tagged live.
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


async def chat_stream(
    *,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    top_p: float | None = None,
) -> AsyncIterator[dict]:
    """Stream /chat/completions (server renders with the default template). Used for
    loose checkpoints. Reasoning arrives on its own SSE events (separate_reasoning)
    and is tagged kind=reasoning live."""
    from .tinker_sampler import _split_think_string

    kwargs: dict = dict(
        model=model, messages=messages, max_tokens=max_tokens,
        temperature=temperature, stream=True, extra_body={"separate_reasoning": True},
    )
    if top_p is not None:
        kwargs["top_p"] = top_p

    content_acc, reason_acc = "", ""
    finish = "stop"
    stream = await client().chat.completions.create(**kwargs)
    async for ev in stream:
        ch = ev.choices[0] if ev.choices else None
        if ch is None:
            continue
        if ch.finish_reason:
            finish = ch.finish_reason
        d = ch.delta
        rsn = (getattr(d, "reasoning_content", None) or getattr(d, "reasoning", None) or "") if d else ""
        cnt = (getattr(d, "content", None) or "") if d else ""
        if rsn:
            reason_acc += rsn
            yield {"delta": rsn, "kind": "reasoning"}
        if cnt:
            content_acc += cnt
            yield {"delta": cnt, "kind": "content"}
    # some models inline <think> in content even with separate_reasoning
    text, think = _split_think_string(content_acc)
    if think:
        reason_acc = (reason_acc + "\n\n" + think) if reason_acc else think
    final: dict = {"content": text, "raw_text": text, "finish_reason": finish}
    if reason_acc:
        final["reasoning"] = reason_acc
    yield final


async def chat_one(
    *,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    top_p: float | None = None,
) -> dict:
    """One non-streaming /chat/completions (for the n>1 fan-out). Returns the final
    message dict (content, reasoning?, raw_text)."""
    last: dict = {"content": "", "raw_text": "", "finish_reason": "stop"}
    async for item in chat_stream(
        model=model, messages=messages, temperature=temperature,
        max_tokens=max_tokens, top_p=top_p,
    ):
        if "delta" not in item:
            last = item
    return last
