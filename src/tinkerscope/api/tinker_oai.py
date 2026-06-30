"""tinker's OpenAI-compatible inference endpoint (beta) — the token-streaming path.

tinker's native SamplingClient (tinker_sampler.py) returns whole samples; it has
no token streaming. But tinker ALSO exposes an OpenAI-compatible HTTP API at
`<base>/oai/api/v1` that does stream (docs: tinker/compatible-apis/openai). We use
it for the n=1 "watch it type" path, and it's the only way to reach "loose"
checkpoints — sampler paths listed by GET /v1/models that aren't in the local scan
dir, so we don't know their base_model/renderer and let the server render with the
model's default chat template.

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
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
from openai import AsyncOpenAI

BASE_URL = "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1"

_client: AsyncOpenAI | None = None
_checkpoints: list[dict] | None = None


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
# Servable checkpoint listing (GET /v1/models)
# ---------------------------------------------------------------------------
def _ckpt_label(sampler_path: str, created: int | None) -> str:
    """Readable label for a UUID-only checkpoint: short-uuid · ckpt-name · date."""
    body = sampler_path.split("://", 1)[-1]
    uuid = body.split(":", 1)[0][:8]
    name = sampler_path.rstrip("/").split("/")[-1]
    when = ""
    if created:
        when = " · " + datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d")
    return f"{uuid} · {name}{when}"


def list_checkpoints(refresh: bool = False) -> list[dict]:
    """Sampler checkpoints the oai endpoint currently serves (GET /v1/models),
    newest first. UUID-only (no base_model/renderer) -> sampled via chat_stream.
    Cached; refresh=True re-fetches."""
    global _checkpoints
    if _checkpoints is None or refresh:
        r = httpx.get(
            f"{BASE_URL}/models",
            headers={"Authorization": f"Bearer {_key()}"},
            timeout=15.0,
        )
        r.raise_for_status()
        items: list[dict] = []
        for m in r.json().get("data", []):
            sp = m.get("id")
            if not sp:
                continue
            created = m.get("created") or 0
            items.append(
                {"sampler_path": sp, "label": _ckpt_label(sp, created), "created": created}
            )
        items.sort(key=lambda x: x["created"], reverse=True)
        _checkpoints = items
    return _checkpoints


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
