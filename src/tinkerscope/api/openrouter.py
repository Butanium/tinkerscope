"""OpenRouter sampling — the secondary compare backend.

Lets the playground put a discovered Tinker checkpoint side-by-side with a
reference model served via OpenRouter (e.g. the base instruct model). Ported
from Harry's playground; reasoning handling kept (some models stream reasoning
in a side field, others embed <think> tags in content).

Closed reasoning models (OpenAI, Anthropic, Gemini). What they return is not the
CoT the model ran on — probed on openai/gpt-6-luna, 2026-10-15:

  - `reasoning` (and the `reasoning.summary` item of `reasoning_details`) is a
    provider-written SUMMARY. Display-only: passing back an edited summary
    changes nothing the model sees (prompt tokens identical).
  - the reasoning itself rides as a `reasoning.encrypted` item (Anthropic: a
    `reasoning.text` item with a `signature`). Passing `reasoning_details` back on
    the assistant turn puts that reasoning into the next turn's context; a
    tampered blob is a 400 `invalid_encrypted_content`. So a turn's thinking can
    be KEPT or DROPPED, never edited — the browser's edit dialog enforces that.

`reasoning_details` is therefore stored verbatim on the sample/node and forwarded
on later turns (`prepare_messages`), filtered by vendor so one provider's blob is
never sent to another's model. A plain `reasoning` string is NOT forwarded (it
never was: OpenAI ignores it, and a tinker turn's thinking isn't the OpenRouter
model's own).

An encrypted blob is bound to the ORG that produced it, and a mismatch is silent:
OpenAI itself answers another org's blob with 200 and ignores it (no reasoning
tokens), directly or through OpenRouter (measured 2026-10-29 on gpt-5.6-terra
across two orgs — ENGINEERING_LOGS). So a shared workspace replays with that
reasoning missing and no error. Each sample carrying reasoning_details is stamped
`reasoning_account` (`account_tag()`, a short hash of the key) so the UI can flag
the turns this key can't replay.

Tool calling: `tools` goes on the request as-is, a turn's `tool_calls` come back
in the OpenAI shape (streamed pieces merged by index) and are replayed on the
assistant turn, tool results go back as role="tool" turns with their
`tool_call_id`. Arguments are the model's raw JSON string, never re-serialized.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from typing import Any

import httpx
from openai import AsyncOpenAI

from .raw_view import format_request_response, format_response

_client: AsyncOpenAI | None = None

_THINK_RE = re.compile(r"<think>(.*?)(?:</think>|$)", re.DOTALL)

# OpenRouter's effort vocabulary (a 400 lists exactly these). A value outside a
# model's own `supported_efforts` is coerced by OpenRouter, not rejected
# ("minimal" on gpt-6-luna → 200, low-effort reasoning).
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# Models that never continue a trailing assistant message. OpenAI's API has no
# prefill: the message is read as an EARLIER assistant turn and the model answers
# afresh — sometimes in a way that reads like a continuation ("1, 2, 3, 4," →
# "5, 6, …"), which merging would pass off as one turn. Matched on the id with a
# leading `~` (rolling alias) stripped.
_NO_PREFILL_PREFIXES = ("openai/",)


def prefill_unsupported(model: str) -> bool:
    return model.lstrip("~").startswith(_NO_PREFILL_PREFIXES)


def _split_think(content: str) -> tuple[str, str | None]:
    if "<think>" not in content:
        return content, None
    think = "\n\n".join(m.group(1).strip() for m in _THINK_RE.finditer(content))
    text = _THINK_RE.sub("", content).strip()
    return text, (think or None)


def account_tag() -> str | None:
    """A short, non-reversible tag for the configured key — what an encrypted
    reasoning blob is bound to (see the module docstring). None without a key."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    return hashlib.sha256(key.encode()).hexdigest()[:10] if key else None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for OpenRouter models")
        _client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    return _client


# ── catalog: per-model capabilities ──────────────────────────────────────────
# The public /models list, fetched once and cached for the process (?refresh on
# the route re-fetches). Read by the typeahead (`catalog`) and, per model, by
# `model_caps` — which the CHAT path only ever reads from the cache: a send never
# waits on (or fails because of) a catalog fetch.
_catalog_raw: list[dict] | None = None
_catalog_ts = 0.0


def fetch_catalog(refresh: bool = False) -> list[dict]:
    """The raw OpenRouter model list (cached). Raises on a failed fetch."""
    global _catalog_raw, _catalog_ts
    if _catalog_raw is None or refresh:
        r = httpx.get("https://openrouter.ai/api/v1/models", timeout=15.0)
        r.raise_for_status()
        _catalog_raw = r.json().get("data", [])
        _catalog_ts = time.time()
    return _catalog_raw


def _caps_of(entry: dict) -> dict:
    rs = entry.get("reasoning") or {}
    params = entry.get("supported_parameters") or []
    return {
        "reasoning": bool(rs) or "reasoning" in params,
        "reasoning_mandatory": bool(rs.get("mandatory")),
        "efforts": [e for e in (rs.get("supported_efforts") or []) if isinstance(e, str)],
        "default_effort": rs.get("default_effort"),
        # Sampling knobs the model drops. OpenRouter filters unsupported params
        # silently (temperature 0.2 on gpt-6-luna → 200, no error), so this is the
        # only place a user can learn their slider did nothing.
        "ignores": [p for p in ("temperature", "top_p", "top_k", "presence_penalty", "repetition_penalty")
                    if params and p not in params],
        "prefill": not prefill_unsupported(entry.get("id", "")),
    }


def model_caps(model: str, *, fetch: bool = False) -> dict | None:
    """Capabilities of one model from the catalog, or None if unknown. With
    fetch=False (the chat path) a cold cache answers None instead of fetching."""
    try:
        cat = fetch_catalog() if fetch else (_catalog_raw or [])
    except Exception:
        return None
    for m in cat:
        if m.get("id") == model:
            return _caps_of(m)
    return None


# ── request building ─────────────────────────────────────────────────────────
def reasoning_param(thinking: bool, effort: str | None, caps: dict | None) -> dict:
    """The `reasoning` object for one call. Off = effort "none" (0 reasoning
    tokens; `enabled:false` 400s on mandatory-reasoning models) — except on a model
    whose reasoning can't be turned off and has no "none" level, which gets its
    lowest supported effort instead of a 400. Effort "none" is off too (a panel's
    own override can turn reasoning off under a thinking-on sidebar). On = the
    picked effort, else `enabled` (the model's own default effort)."""
    if not thinking or effort == "none":
        if caps and caps.get("reasoning_mandatory") and "none" not in caps.get("efforts", []):
            lowest = next((e for e in EFFORTS if e in caps["efforts"]), None)
            return {"effort": lowest} if lowest else {"enabled": True}
        return {"effort": "none"}
    if effort:
        return {"effort": effort}
    return {"enabled": True}


def _vendor(fmt: str | None) -> str | None:
    """Model-id prefix a reasoning_details `format` belongs to (None = unknown /
    vendor-neutral, e.g. an open model's plain reasoning text)."""
    f = (fmt or "").lower()
    if "openai" in f:
        return "openai/"
    if "anthropic" in f:
        return "anthropic/"
    if "gemini" in f or "google" in f:
        return "google/"
    if "xai" in f:
        return "x-ai/"
    return None


def replayable_details(details: list | None, model: str) -> list[dict]:
    """The items of a stored `reasoning_details` that may go back to `model`:
    vendor-bound items (encrypted / signed / summaries) only to that vendor's
    models, vendor-neutral ones anywhere. Order and content untouched — the
    provider rejects a rearranged or modified sequence."""
    target = model.lstrip("~")
    out = []
    for d in details or []:
        if not isinstance(d, dict):
            continue
        v = _vendor(d.get("format"))
        if v is None or target.startswith(v):
            out.append(d)
    return out


def clean_tool_calls(calls: Any) -> list[dict]:
    """Tool calls in the OpenAI chat-completions shape, keeping only the fields the
    API defines: `[{id, type: "function", function: {name, arguments}}]`.
    `arguments` stays the model's raw JSON string — re-serializing it would change
    what the model sees of its own call on replay."""
    out = []
    for c in calls or []:
        c = _as_dict(c)
        if not c:
            continue
        fn = _as_dict(c.get("function")) or {}
        args = fn.get("arguments")
        out.append({
            "id": c.get("id") or "",
            "type": c.get("type") or "function",
            "function": {
                "name": fn.get("name") or "",
                "arguments": args if isinstance(args, str) else json.dumps(args if args is not None else {}),
            },
        })
    return out


def prepare_messages(messages: list[dict], model: str) -> list[dict]:
    """Wire messages for one OpenRouter call: {role, content}, plus an assistant
    turn's stored `reasoning_details` when they can be replayed to `model`, its
    `tool_calls`, and a tool turn's `tool_call_id` / `name`."""
    out = []
    for m in messages:
        msg = {"role": m["role"], "content": m["content"]}
        if m["role"] == "assistant":
            rd = replayable_details(m.get("reasoning_details"), model)
            if rd:
                msg["reasoning_details"] = rd
            calls = clean_tool_calls(m.get("tool_calls"))
            if calls:
                msg["tool_calls"] = calls
                # A calls-only turn: null content, as the API returns it. An empty
                # string is an empty text block to some providers, which they reject.
                if not m["content"]:
                    msg["content"] = None
        elif m["role"] == "tool":
            msg["tool_call_id"] = m.get("tool_call_id") or ""
            if m.get("name"):
                msg["name"] = m["name"]
        out.append(msg)
    return out


def _build_kwargs(
    *, model, messages, temperature, max_tokens, thinking,
    top_p, top_k, presence_penalty, repetition_penalty, effort=None, tools=None,
) -> dict:
    kwargs: dict = {
        "model": model,
        "messages": prepare_messages(messages, model),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
    if top_p is not None:
        kwargs["top_p"] = top_p
    if presence_penalty is not None:
        kwargs["presence_penalty"] = presence_penalty
    reasoning = reasoning_param(thinking, effort, model_caps(model))
    if model.lstrip("~").startswith("openai/") and reasoning.get("effort") != "none":
        # OpenAI summarizes its reasoning only when asked; "detailed" is the most
        # it gives (auto / concise / detailed) — the Responses wire sends the same
        reasoning["summary"] = "detailed"
    extra_body: dict = {"reasoning": reasoning}
    if top_k is not None:
        extra_body["top_k"] = top_k
    if repetition_penalty is not None:
        extra_body["repetition_penalty"] = repetition_penalty
    kwargs["extra_body"] = extra_body
    return kwargs


# ── response parsing ─────────────────────────────────────────────────────────
def _as_dict(x) -> dict | None:
    if isinstance(x, dict):
        return x
    dump = getattr(x, "model_dump", None)
    return dump() if callable(dump) else None


def merge_detail_chunks(acc: list[dict], chunk) -> None:
    """Fold one streamed `delta.reasoning_details` list into `acc`. Streamed
    pieces share an `index`: a summary / text item arrives as many fragments to
    concatenate, an encrypted item whole, a signature possibly in a later piece."""
    for raw in chunk or []:
        d = _as_dict(raw)
        if not d:
            continue
        cur = next((a for a in acc if a.get("index") == d.get("index") and a.get("type") == d.get("type")), None)
        if cur is None or d.get("index") is None:
            acc.append(copy.deepcopy(d))
            continue
        for k, v in d.items():
            if k in ("summary", "text") and isinstance(v, str):
                cur[k] = (cur.get(k) or "") + v
            elif v is not None:
                cur[k] = v


def _reasoning_tokens(usage) -> int | None:
    det = getattr(usage, "completion_tokens_details", None) if usage is not None else None
    n = getattr(det, "reasoning_tokens", None) if det is not None else None
    return n if isinstance(n, int) and n > 0 else None


def _elide(details: list[dict]) -> list[dict]:
    """reasoning_details for the Raw view: encrypted payloads / signatures shown
    as their size (a Luna turn's blob is ~2 KB at low effort and grows with the
    reasoning; two turns in, the view would be all base64)."""
    out = []
    for d in details:
        d = dict(d)
        if isinstance(d.get("data"), str):
            d["data"] = f"<encrypted, {len(d['data'])} chars>"
        if isinstance(d.get("signature"), str) and len(d["signature"]) > 40:
            d["signature"] = f"<signature, {len(d['signature'])} chars>"
        out.append(d)
    return out


def merge_tool_call_chunks(acc: list[dict], chunk) -> None:
    """Fold one streamed `delta.tool_calls` list into `acc`. A call arrives as
    pieces sharing an `index`: the id / name in the first, the arguments JSON
    split across the rest."""
    for raw in chunk or []:
        d = _as_dict(raw)
        if not d:
            continue
        idx = d.get("index")
        cur = next((a for a in acc if a.get("index") == idx), None) if idx is not None else None
        if cur is None:
            cur = {"index": idx, "id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            acc.append(cur)
        if d.get("id"):
            cur["id"] = d["id"]
        if d.get("type"):
            cur["type"] = d["type"]
        fn = _as_dict(d.get("function")) or {}
        if fn.get("name"):
            cur["function"]["name"] += fn["name"]
        if fn.get("arguments"):
            cur["function"]["arguments"] += fn["arguments"]


def _response_view(content: str, reasoning: str | None, details: list[dict] | None,
                   usage: dict | None, tool_calls: list[dict] | None) -> dict:
    response: dict = {}
    if reasoning:
        response["reasoning"] = reasoning
    if details:
        response["reasoning_details"] = _elide(details)
    response["content"] = content
    if tool_calls:
        response["tool_calls"] = tool_calls
    if usage:
        response["usage"] = usage
    return response


def _wire_record(request_kwargs: dict, content: str, reasoning: str | None,
                 details: list[dict] | None = None, usage: dict | None = None,
                 tool_calls: list[dict] | None = None) -> str:
    """The sample's `raw_meta`: the actual request body sent over the wire, plus
    the response trimmed to just its output + thinking fields. Heavy on purpose —
    the request repeats the whole conversation (system prompt included) on every
    turn, so it lives in the write-once blob, not the light tree; the light
    `raw_text` is the response section alone.

    Unlike the tinker path (which decodes the real prompt+completion tokens),
    an OpenRouter model has no single chat template *we* control — it's a JSON
    chat-completions call. So the honest "raw" here is the wire payload, not a
    fabricated ``<|im_start|>``/``<think>`` reconstruction (which would invent a
    Qwen template for non-Qwen models like Kimi / GPT / Claude). The OpenAI SDK
    merges ``extra_body`` into the top-level JSON body, so we flatten it here to
    match what's actually sent.
    """
    body = {k: v for k, v in request_kwargs.items() if k != "extra_body"}
    body["messages"] = [
        {**m, "reasoning_details": _elide(m["reasoning_details"])} if m.get("reasoning_details") else m
        for m in body.get("messages", [])
    ]
    body.update(request_kwargs.get("extra_body") or {})
    return format_request_response(body, _response_view(content, reasoning, details, usage, tool_calls))


def _usage_view(usage) -> dict | None:
    """The usage numbers worth showing next to a sample: token counts incl. the
    hidden reasoning, and spend (BYOK routes bill the vendor key: cost 0 there,
    the real number is upstream_inference_cost)."""
    d = _as_dict(usage)
    if not d:
        return None
    keep = {k: d.get(k) for k in ("prompt_tokens", "completion_tokens", "cost", "is_byok") if d.get(k) is not None}
    rt = (d.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if rt:
        keep["reasoning_tokens"] = rt
    up = (d.get("cost_details") or {}).get("upstream_inference_cost")
    if up is not None and d.get("is_byok"):
        keep["upstream_inference_cost"] = up
    return keep or None


def _finish(content: str, reasoning: str | None, details: list[dict], usage, kwargs: dict,
            finish: str | None, tool_calls: list[dict] | None = None) -> dict:
    if "<think>" in content:
        content, think = _split_think(content)
        if think:
            reasoning = (reasoning + "\n\n" + think) if reasoning else think
    if not reasoning:
        summaries = [d.get("summary") or d.get("text") for d in details
                     if d.get("type") in ("reasoning.summary", "reasoning.text")]
        reasoning = "\n\n".join(s for s in summaries if s) or None
    result: dict = {"content": content}
    if reasoning:
        result["reasoning"] = reasoning
    if details:
        result["reasoning_details"] = details
        if any(d.get("type") == "reasoning.encrypted" or d.get("signature") for d in details):
            result["reasoning_account"] = account_tag()
    rt = _reasoning_tokens(usage)
    if rt:
        result["reasoning_tokens"] = rt
    calls = clean_tool_calls(tool_calls)
    if calls:
        result["tool_calls"] = calls
    usage_v = _usage_view(usage)
    result["raw_text"] = format_response(_response_view(content, reasoning, details, usage_v, calls))
    result["raw_meta"] = _wire_record(kwargs, content, reasoning, details, usage_v, calls)
    if finish:
        result["finish_reason"] = finish  # "length" ⇒ cut by max_tokens
    return result


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
    effort: str | None = None,
    tools: list[dict] | None = None,
) -> dict:
    """Run a single chat completion via OpenRouter. Returns {content, reasoning?,
    reasoning_details?, reasoning_tokens?, tool_calls?, raw_text, finish_reason?}."""
    client = _get_client()
    kwargs = _build_kwargs(
        model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
        thinking=thinking, top_p=top_p, top_k=top_k, effort=effort, tools=tools,
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
    details: list[dict] = []
    merge_detail_chunks(details, getattr(choice.message, "reasoning_details", None))
    return _finish(content, reasoning, details, getattr(response, "usage", None), kwargs,
                   getattr(choice, "finish_reason", None), getattr(choice.message, "tool_calls", None))


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
    effort: str | None = None,
    tools: list[dict] | None = None,
):
    """Stream a single OpenRouter completion. Yields {"delta","kind"} chunks
    (reasoning tagged live), then the final message dict."""
    client = _get_client()
    kwargs = _build_kwargs(
        model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
        thinking=thinking, top_p=top_p, top_k=top_k, effort=effort, tools=tools,
        presence_penalty=presence_penalty, repetition_penalty=repetition_penalty,
    )
    kwargs["stream"] = True

    content_acc, reason_acc = "", ""
    details: list[dict] = []
    calls: list[dict] = []
    usage = None
    finish: str | None = None
    stream = await client.chat.completions.create(**kwargs)
    async for ev in stream:
        if getattr(ev, "usage", None) is not None:
            usage = ev.usage  # OpenRouter sends it on the last chunk
        ch = ev.choices[0] if ev.choices else None
        if ch and getattr(ch, "finish_reason", None):
            finish = ch.finish_reason
        d = ch.delta if ch else None
        if d is not None:
            merge_detail_chunks(details, getattr(d, "reasoning_details", None))
            merge_tool_call_chunks(calls, getattr(d, "tool_calls", None))
        rsn = (getattr(d, "reasoning_content", None) or getattr(d, "reasoning", None) or "") if d else ""
        cnt = (getattr(d, "content", None) or "") if d else ""
        if rsn:
            reason_acc += str(rsn)
            yield {"delta": str(rsn), "kind": "reasoning"}
        if cnt:
            content_acc += cnt
            yield {"delta": cnt, "kind": "content"}

    yield _finish(content_acc, reason_acc or None, details, usage, kwargs, finish, calls)
