"""OpenRouter's Responses-API wire (`POST /api/v1/responses`) — for threads that
replay a real agent's recorded calls exactly.

Chat completions can't express what a Responses-API agent was sent: input items
(`reasoning` with `encrypted_content`, `function_call` / `function_call_output`,
`computer_call` / `computer_call_output`, messages whose content is
`input_text` / `input_image` parts), native tool types (`{"type": "computer"}`),
flat Responses-format functions. So a node can carry the VERBATIM items it stands
for (`items`, a heavy write-once blob — screenshots are ~0.7 MB each), and on this
wire a node's items go out as they are, in tree order, never re-serialized. A node
without items is converted (`message_items`). What's in the tree is what's sent:
no scaffold logic lives here.

Wire choice (`routes/chat.py`): this wire whenever a message on the path holds
items, or the workspace opts in (`wire: {"api": "responses", "reasoning": {…}}`).
OpenRouter `openai/*` models only.

A computer call can't be answered with a `computer_call_output` item through
OpenRouter: its Responses schema has no such type (400; probed 2026-10-30), and a
`function_call_output` for that call id is an OpenAI 400. A user message carrying
the screenshot after the call works (the model reads it), so that is how a
computer call is answered (`cli._tool_result_msg`, the browser's result form). A
`computer_call` item validates only with `pending_safety_checks` present.

Output: the response's `output` items are stored VERBATIM on the new node (incl.
encrypted reasoning — kept even when it can't be replayed under this key; it may
be replayable under another one later), and the light fields the rest of the app
reads are DERIVED from them: `content` (output_text), `reasoning` (summary text),
`tool_calls` (chat-shaped: function calls, and computer calls typed
`computer_call`), `finish_reason`.

Probed 2026-10-30 (synthetic content, gpt-5.6-luna, ENGINEERING_LOGS): encrypted
reasoning comes back without `include` / `store`; our own output items replay as
input verbatim and their reasoning is used; a reasoning item cut down to
`{id, summary}` (how the AI Village scaffold replays it) carries nothing on our key.
"""
from __future__ import annotations

import copy
import json
import os
import re

import httpx

from .openrouter import account_tag, model_caps, reasoning_param
from .raw_view import format_request_response, format_response

URL = "https://openrouter.ai/api/v1/responses"
_TIMEOUT = httpx.Timeout(600.0, connect=20.0)


# ── tools ────────────────────────────────────────────────────────────────────
def responses_tools(tools: list | None) -> list:
    """Workspace tools as Responses tools: chat-shaped functions flattened
    (`{type, function:{name,…}}` → `{type, name, …}`), everything else — flat
    functions, `{"type": "computer"}` — verbatim."""
    out = []
    for t in tools or []:
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            out.append({"type": "function", **t["function"]})
        else:
            out.append(t)
    return out


def chat_tools(tools: list | None) -> list:
    """Workspace tools for CHAT completions: flat functions nested; a native tool
    type has no chat equivalent, so it refuses rather than silently dropping it."""
    out = []
    for t in tools or []:
        if t.get("type") != "function":
            raise ValueError(f"the {t.get('type')!r} tool only exists on the Responses wire "
                             "(set the workspace's wire to responses)")
        out.append(t if isinstance(t.get("function"), dict)
                   else {"type": "function", "function": {k: v for k, v in t.items() if k != "type"}})
    return out


# ── input ────────────────────────────────────────────────────────────────────
def message_items(m: dict) -> list[dict]:
    """One /api/chat message → Responses input items: its stored `items` verbatim,
    else a conversion of the chat-shaped fields."""
    if m.get("items"):
        return m["items"]
    role = m.get("role")
    if role == "tool":
        return [{"type": "function_call_output", "call_id": m.get("tool_call_id") or "", "output": m.get("content") or ""}]
    if role == "assistant":
        out: list[dict] = []
        if m.get("content"):
            out.append({"role": "assistant", "content": m["content"]})
        for c in m.get("tool_calls") or []:
            fn = c.get("function") or {}
            if c.get("type", "function") == "function":
                out.append({"type": "function_call", "call_id": c.get("id") or "", "name": fn.get("name") or "",
                            "arguments": fn.get("arguments") or "{}"})
        return out
    return [{"role": role, "content": m.get("content") or ""}]


def build_input(messages: list[dict]) -> list[dict]:
    items: list[dict] = []
    for m in messages:
        items.extend(message_items(m))
    return items


# ── output → the light fields ────────────────────────────────────────────────
def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    return [p.get("text") or "" for p in content or [] if isinstance(p, dict) and p.get("type") in ("output_text", "input_text", "text")]


def derive(items: list[dict]) -> dict:
    """The light fields a node shows/searches, derived from its items: assistant
    text, reasoning summary text, chat-shaped tool calls, and whether it holds
    encrypted reasoning."""
    content, reasoning, calls = [], [], []
    encrypted = False
    for it in items or []:
        t = it.get("type") or "message"
        if t == "message" and it.get("role") == "assistant":
            content += _texts(it.get("content"))
        elif t == "reasoning":
            reasoning += [s.get("text") or "" for s in it.get("summary") or [] if isinstance(s, dict)]
            encrypted = encrypted or bool(it.get("encrypted_content"))
        elif t == "function_call":
            calls.append({"id": it.get("call_id") or "", "type": "function",
                          "function": {"name": it.get("name") or "", "arguments": it.get("arguments") or "{}"}})
        elif t == "computer_call":
            act = it.get("action") if "action" in it else it.get("actions")
            calls.append({"id": it.get("call_id") or "", "type": "computer_call",
                          "function": {"name": "computer", "arguments": json.dumps(act)}})
    out: dict = {"content": "\n\n".join(c for c in content if c)}
    if any(reasoning):
        out["reasoning"] = "\n\n".join(r for r in reasoning if r)
    if calls:
        out["tool_calls"] = calls
    out["encrypted"] = encrypted
    return out


def input_text(items: list[dict]) -> str:
    """Display text of input-side items (user/system messages, tool outputs)."""
    parts = []
    for it in items or []:
        t = it.get("type") or "message"
        if t == "message":
            parts += _texts(it.get("content"))
        elif t in ("function_call_output",):
            o = it.get("output")
            parts.append(o if isinstance(o, str) else json.dumps(o))
        elif t == "computer_call_output":
            parts.append("[screenshot]")
    return "\n\n".join(p for p in parts if p)


# ── raw views ────────────────────────────────────────────────────────────────
_DATA_URL = re.compile(r"^data:([^;,]+)[^,]*,(.*)$", re.S)


def _elide(v):
    """Inline images and encrypted payloads shown as their size (a screenshot is
    ~0.7 MB of base64; the record exists to read the request, not to store it twice)."""
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k == "encrypted_content" and isinstance(x, str):
                out[k] = f"<encrypted, {len(x)} chars>"
            elif k == "image_url" and isinstance(x, str) and (m := _DATA_URL.match(x)):
                out[k] = f"<{m.group(1)}, {len(x)} chars>"
            else:
                out[k] = _elide(x)
        return out
    if isinstance(v, list):
        return [_elide(x) for x in v]
    return v


def _usage_view(u: dict | None) -> dict | None:
    if not u:
        return None
    keep = {k: u.get(k) for k in ("input_tokens", "output_tokens", "cost", "is_byok") if u.get(k) is not None}
    rt = (u.get("output_tokens_details") or {}).get("reasoning_tokens")
    if rt:
        keep["reasoning_tokens"] = rt
    up = (u.get("cost_details") or {}).get("upstream_inference_cost")
    if up is not None and u.get("is_byok"):
        keep["upstream_inference_cost"] = up
    return keep or None


# ── request ──────────────────────────────────────────────────────────────────
def reasoning_obj(thinking: bool, effort: str | None, caps: dict | None, base: dict | None) -> dict:
    """The Responses `reasoning` object: the workspace's recorded one (`base`, e.g.
    `{"effort": "medium", "summary": "detailed"}`) with the call's effort on top —
    thinking off = effort "none" (or the model's lowest), an explicit effort wins,
    otherwise the recorded effort, otherwise the model's default."""
    out = dict(base or {})
    r = reasoning_param(thinking, effort, caps)
    if "effort" in r and (not thinking or effort):
        out["effort"] = r["effort"]
    return out


_RESERVED = ("model", "input", "tools", "reasoning", "max_output_tokens", "temperature", "stream")


def build_body(*, model: str, messages: list[dict], tools: list | None, max_tokens: int,
               temperature: float | None, thinking: bool, effort: str | None, base_reasoning: dict | None,
               extra: dict | None = None, exact: bool = False) -> dict:
    """`exact` (a workspace imported from a recorded call): the request matches the
    recording — its `reasoning` object as recorded (only an explicit effort replaces
    its effort; the thinking switch is ignored), no max_output_tokens / temperature
    the recording didn't have."""
    caps = model_caps(model)
    body: dict = {"model": model, "input": build_input(messages)}
    if not exact:
        body["max_output_tokens"] = max_tokens
    # request-level fields a recorded call carried beyond these (e.g. the Village's
    # `prompt_cache_options`), sent as recorded
    body.update({k: v for k, v in (extra or {}).items() if k not in _RESERVED})
    rt = responses_tools(tools)
    if rt:
        body["tools"] = rt
    if exact:
        reasoning = {**(base_reasoning or {}), **({"effort": effort} if effort else {})}
    else:
        reasoning = reasoning_obj(thinking, effort, caps, base_reasoning)
    if reasoning:
        body["reasoning"] = reasoning
    if not exact and temperature is not None and not (caps and "temperature" in caps.get("ignores", [])):
        body["temperature"] = temperature
    return body


async def sample_one(*, model: str, messages: list[dict], tools: list | None, max_tokens: int,
                     temperature: float | None, thinking: bool, effort: str | None = None,
                     base_reasoning: dict | None = None, extra: dict | None = None, exact: bool = False) -> dict:
    """One Responses call. Returns a sample dict: the derived light fields, the
    verbatim `items`, `raw_text` (response view) and `raw_meta` (request + response)."""
    body = build_body(model=model, messages=messages, tools=tools, max_tokens=max_tokens,
                      temperature=temperature, thinking=thinking, effort=effort, base_reasoning=base_reasoning,
                      extra=extra, exact=exact)
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise ValueError("OPENROUTER_API_KEY is required for OpenRouter models")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(URL, json=body, headers={"Authorization": f"Bearer {key}"})
    if r.status_code >= 400:
        raise ValueError(f"OpenRouter responses {r.status_code}: {r.text[:800]}")
    data = r.json()
    if data.get("error"):
        raise ValueError(f"OpenRouter responses error: {json.dumps(data['error'])[:800]}")
    items = copy.deepcopy(data.get("output") or [])
    d = derive(items)
    usage = _usage_view(data.get("usage"))
    inc = (data.get("incomplete_details") or {}).get("reason")
    finish = ("length" if data.get("status") == "incomplete" and inc in ("max_output_tokens", "max_tokens")
              else "tool_calls" if d.get("tool_calls") else data.get("status") or "stop")
    if finish == "completed":
        finish = "stop"
    response_view = {"output": _elide(items), **({"usage": usage} if usage else {})}
    out: dict = {
        "content": d["content"],
        "items": items,
        "raw_text": format_response(response_view),
        "raw_meta": format_request_response(_elide(body), response_view),
        "finish_reason": finish,
    }
    if d.get("reasoning"):
        out["reasoning"] = d["reasoning"]
    if d.get("tool_calls"):
        out["tool_calls"] = d["tool_calls"]
    if d["encrypted"]:
        out["reasoning_account"] = account_tag()
    if usage and usage.get("reasoning_tokens"):
        out["reasoning_tokens"] = usage["reasoning_tokens"]
    return out


def openai_model(model: str) -> str:
    """A recorded call's model id as an OpenRouter id (`gpt-5.6-terra` → `openai/gpt-5.6-terra`)."""
    return model if "/" in model else f"openai/{model}"

