"""OpenRouter sampling — the secondary compare backend.

Lets the playground put a discovered Tinker checkpoint side-by-side with a
reference model served via OpenRouter (e.g. the base instruct model). Ported
from Harry's playground; reasoning handling kept (some models stream reasoning
in a side field, others embed <think> tags in content).
"""
from __future__ import annotations

import json
import os
import re

from openai import AsyncOpenAI

_client: AsyncOpenAI | None = None

_THINK_RE = re.compile(r"<think>(.*?)(?:</think>|$)", re.DOTALL)


def _split_think(content: str) -> tuple[str, str | None]:
    if "<think>" not in content:
        return content, None
    think = "\n\n".join(m.group(1).strip() for m in _THINK_RE.finditer(content))
    text = _THINK_RE.sub("", content).strip()
    return text, (think or None)


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for OpenRouter models")
        _client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    return _client


def _build_kwargs(
    *, model, messages, temperature, max_tokens, thinking,
    top_p, top_k, presence_penalty, repetition_penalty,
) -> dict:
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if top_p is not None:
        kwargs["top_p"] = top_p
    if presence_penalty is not None:
        kwargs["presence_penalty"] = presence_penalty
    extra_body: dict = {}
    # enabled:true ≈ medium effort (model decides), closest to Tinker default;
    # effort:none disables thinking entirely.
    extra_body["reasoning"] = {"enabled": True} if thinking else {"effort": "none"}
    if top_k is not None:
        extra_body["top_k"] = top_k
    if repetition_penalty is not None:
        extra_body["repetition_penalty"] = repetition_penalty
    kwargs["extra_body"] = extra_body
    return kwargs


def _raw_text(request_kwargs: dict, content: str, reasoning: str | None) -> str:
    """Raw view for the OpenRouter backend: the actual request body sent over
    the wire, plus the response trimmed to just its output + thinking fields.

    Unlike the tinker path (which decodes the real prompt+completion tokens),
    an OpenRouter model has no single chat template *we* control — it's a JSON
    chat-completions call. So the honest "raw" here is the wire payload, not a
    fabricated ``<|im_start|>``/``<think>`` reconstruction (which would invent a
    Qwen template for non-Qwen models like Kimi / GPT / Claude). The OpenAI SDK
    merges ``extra_body`` into the top-level JSON body, so we flatten it here to
    match what's actually sent.
    """
    body = {k: v for k, v in request_kwargs.items() if k != "extra_body"}
    body.update(request_kwargs.get("extra_body") or {})
    response: dict = {}
    if reasoning:
        response["reasoning"] = reasoning
    response["content"] = content
    return (
        "── request ──────────────────────────────────────\n"
        + json.dumps(body, indent=2, ensure_ascii=False)
        + "\n\n── response (output + thinking) ──────────────────\n"
        + json.dumps(response, indent=2, ensure_ascii=False)
    )


async def sample_one(
    *,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    thinking: bool,
    top_p: float | None = None,
    top_k: int | None = None,
    presence_penalty: float | None = None,
    repetition_penalty: float | None = None,
) -> dict:
    """Run a single chat completion via OpenRouter. Returns {content, reasoning?, raw_text}."""
    client = _get_client()
    kwargs = _build_kwargs(
        model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
        thinking=thinking, top_p=top_p, top_k=top_k,
        presence_penalty=presence_penalty, repetition_penalty=repetition_penalty,
    )
    response = await client.chat.completions.create(**kwargs)
    choice = response.choices[0]
    content = choice.message.content or ""
    reasoning = (
        getattr(choice.message, "reasoning_content", None)
        or getattr(choice.message, "reasoning", None)
        or None
    )
    if isinstance(reasoning, list):
        reasoning = "\n".join(str(r) for r in reasoning)
    if reasoning:
        reasoning = str(reasoning)

    if "<think>" in content:
        text, think = _split_think(content)
        content = text
        if think:
            reasoning = (reasoning + "\n\n" + think) if reasoning else think

    result: dict = {"content": content}
    if reasoning:
        result["reasoning"] = reasoning
    result["raw_text"] = _raw_text(kwargs, content, reasoning)
    return result


async def sample_one_stream(
    *,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    thinking: bool,
    top_p: float | None = None,
    top_k: int | None = None,
    presence_penalty: float | None = None,
    repetition_penalty: float | None = None,
):
    """Stream a single OpenRouter completion. Yields {"delta","kind"} chunks
    (reasoning tagged live), then the final message dict."""
    client = _get_client()
    kwargs = _build_kwargs(
        model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
        thinking=thinking, top_p=top_p, top_k=top_k,
        presence_penalty=presence_penalty, repetition_penalty=repetition_penalty,
    )
    kwargs["stream"] = True

    content_acc, reason_acc = "", ""
    stream = await client.chat.completions.create(**kwargs)
    async for ev in stream:
        ch = ev.choices[0] if ev.choices else None
        d = ch.delta if ch else None
        rsn = (getattr(d, "reasoning_content", None) or getattr(d, "reasoning", None) or "") if d else ""
        cnt = (getattr(d, "content", None) or "") if d else ""
        if rsn:
            reason_acc += str(rsn)
            yield {"delta": str(rsn), "kind": "reasoning"}
        if cnt:
            content_acc += cnt
            yield {"delta": cnt, "kind": "content"}

    reasoning: str | None = reason_acc or None
    if "<think>" in content_acc:
        content_acc, think = _split_think(content_acc)
        if think:
            reasoning = (reasoning + "\n\n" + think) if reasoning else think
    result: dict = {"content": content_acc}
    if reasoning:
        result["reasoning"] = reasoning
    result["raw_text"] = _raw_text(kwargs, content_acc, reasoning)
    yield result
