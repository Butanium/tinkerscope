"""Chat / sampling endpoint (SSE).

One request samples from ONE model: a Tinker LoRA checkpoint (run_id), a raw
Tinker base model (base_model), a "loose" Tinker checkpoint the oai endpoint
serves but that isn't in the scan dir (sampler_path), or an OpenRouter reference
(openrouter_model). Compare mode = the caller fires two requests, tagged
panel=primary and panel=compare.

Streaming (your design): for n==1 we stream tokens live through tinker's
OpenAI-compatible endpoint (or OpenRouter's), emitting `delta` events; for n>1 we
keep the native batched fan-out (each sample pops in whole — the distribution
view). tinker's native SamplingClient has no token streaming, so the n=1 path
deliberately routes through the oai endpoint instead.

  - run_id (LoRA ckpt)   -> TEMPORARILY native sample_stream for ALL n (no token
                            streaming): tinker's oai /completions serves BASE for a
                            LoRA sampler path (tinker-feedback#125). Restore the
                            n==1 /completions path once that's fixed.
  - base_model           -> oai /completions with OUR renderer (training-faithful;
                            no adapter, so base is correct)
  - sampler_path (loose) -> oai /chat/completions (server's default template;
                            base_model/renderer unknown; /chat applies adapters)
  - openrouter_model     -> OpenRouter /chat/completions

Each completed sample is yielded to the caller (CLI stdout) and broadcast to the
state bus (browser live view), tagged with chat_id + panel. chat_id is allocated
atomically (BUS.chat_begin); `running` is an in-flight counter (BUS.chat_end) so
concurrent chats (two compare panels, or CLI + browser) don't collide. The chosen
sample is committed back into the panel's transcript for multi-turn memory.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Awaitable, Callable

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .. import discovery, openrouter, tinker_oai
from ..state import BUS
from ..tinker_sampler import get_sampler, select_renderer_name

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    # Tinker LoRA checkpoint (primary path)
    run_id: str | None = None
    checkpoint: str | None = None          # checkpoint name; default = last w/ sampler
    # Raw tinker base model (no LoRA) — one of tinker's served models
    base_model: str | None = None
    # "Loose" tinker checkpoint: a sampler path from the oai /models list that
    # isn't a discovered run (base_model/renderer unknown -> default template)
    sampler_path: str | None = None
    # OpenRouter selection (alternative)
    openrouter_model: str | None = None
    # conversation + params (numeric params tolerate None → server defaults, so a
    # transiently-empty UI input can't 422 the request)
    messages: list[ChatMessage]
    system_prompt: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    n_samples: int | None = None
    thinking: bool = False
    top_p: float | None = None
    top_k: int | None = None
    presence_penalty: float | None = None
    repetition_penalty: float | None = None
    # live-drive routing
    panel: str = "primary"                  # "primary" | "compare"
    broadcast: bool = True                   # mirror samples to the state bus
    # Opaque client-minted ownership token, echoed verbatim on the chat_start /
    # chat_done / chat_error bus broadcasts. The browser uses it to tell its OWN
    # chats (which it folds from this request's response stream) apart from
    # external ones (CLI / another tab) it must fold via reconciliation. The
    # server never interprets it.
    client_token: str | None = None


def _resolve_checkpoint(run: discovery.Run, name: str | None):
    """Return the Checkpoint to sample (by name, else last one with a sampler_path)."""
    with_sampler = [c for c in run.checkpoints if c.sampler_path]
    if not with_sampler:
        return None
    if name:
        for c in run.checkpoints:
            if c.name == name:
                return c if c.sampler_path else None
        return None
    return next((c for c in with_sampler if c.name == "final"), with_sampler[-1])


async def _fanout(make_one: Callable[[int], Awaitable[dict]], n: int) -> AsyncIterator[dict]:
    """Fan out n single completions; yield each {sample_index, ...} as it finishes.
    Cancels stragglers if the consumer goes away (CLI Ctrl-C / browser tab closed)
    so we stop paying remote tokens for output nobody reads."""
    async def one(idx: int) -> dict:
        try:
            return {"sample_index": idx, **await make_one(idx)}
        except Exception as e:
            return {"sample_index": idx, "error": f"{type(e).__name__}: {e}"}

    tasks = [asyncio.create_task(one(i)) for i in range(n)]
    try:
        for fut in asyncio.as_completed(tasks):
            yield await fut
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@router.post("/chat")
async def chat(req: ChatRequest):
    msgs = [{"role": m.role, "content": m.content} for m in req.messages]
    sampling_msgs = list(msgs)
    if req.system_prompt and not any(m["role"] == "system" for m in sampling_msgs):
        sampling_msgs = [{"role": "system", "content": req.system_prompt}, *sampling_msgs]

    temperature = req.temperature if req.temperature is not None else 1.0
    max_tokens = req.max_tokens or 1024
    n = max(1, min(req.n_samples or 1, 200))
    # n==1 token-streams live through the oai endpoints. EXCEPTION: discovered LoRA
    # runs (run_id) — tinker's oai /completions serves the BASE model for a LoRA
    # sampler path (tinker-feedback#125), so the live single-sample path would
    # silently show base output instead of the finetune. Until that's fixed, route
    # run_id n==1 through the SAME native sample_stream path as n>1 (whole sample, no
    # token streaming). base_model / loose / openrouter are unaffected and keep
    # streaming (base via /completions is correct; loose via /chat applies adapters).
    # TODO(tinker-feedback#125): when fixed, drop `and req.run_id is None` to restore
    # token streaming for single samples from LoRA runs.
    stream = (n == 1) and (req.run_id is None)
    is_compare_panel = req.panel == "compare"

    async def gen():
        # ── resolve the model + build the per-sample producer ───────────────
        try:
            if req.openrouter_model:
                label = req.openrouter_model
                if stream:
                    produce_iter = openrouter.sample_one_stream(
                        model=req.openrouter_model, messages=sampling_msgs,
                        temperature=temperature, max_tokens=max_tokens, thinking=req.thinking,
                        top_p=req.top_p, top_k=req.top_k, presence_penalty=req.presence_penalty,
                        repetition_penalty=req.repetition_penalty,
                    )
                else:
                    produce_iter = _fanout(lambda i: openrouter.sample_one(
                        model=req.openrouter_model, messages=sampling_msgs,
                        temperature=temperature, max_tokens=max_tokens, thinking=req.thinking,
                        top_p=req.top_p, top_k=req.top_k, presence_penalty=req.presence_penalty,
                        repetition_penalty=req.repetition_penalty,
                    ), n)
                sel_patch: dict = {}
            elif req.sampler_path:
                # Loose checkpoint: oai /chat/completions, server renders (default template).
                label = tinker_oai._ckpt_label(req.sampler_path, None)
                if stream:
                    produce_iter = tinker_oai.chat_stream(
                        model=req.sampler_path, messages=sampling_msgs,
                        temperature=temperature, max_tokens=max_tokens, top_p=req.top_p,
                    )
                else:
                    produce_iter = _fanout(lambda i: tinker_oai.chat_one(
                        model=req.sampler_path, messages=sampling_msgs,
                        temperature=temperature, max_tokens=max_tokens, top_p=req.top_p,
                    ), n)
                sel_patch = {}
            elif req.base_model:
                # Raw base model through tinker (no LoRA checkpoint).
                caps = discovery.get_capabilities()
                if caps.get("available") and req.base_model not in discovery._supported_base_set(caps):
                    raise ValueError(f"tinker does not currently serve {req.base_model}")
                label = req.base_model
                renderer_name = select_renderer_name(req.base_model, None, req.thinking)
                if stream:
                    _, prompt_text, stop = await get_sampler().render(
                        req.base_model, renderer_name, sampling_msgs
                    )
                    produce_iter = tinker_oai.completions_stream(
                        model=req.base_model, prompt=prompt_text, stop=stop,
                        temperature=temperature, max_tokens=max_tokens, top_p=req.top_p,
                    )
                else:
                    produce_iter = get_sampler().sample_stream(
                        base_model=req.base_model, sampler_path=None, renderer_name=renderer_name,
                        messages=sampling_msgs, n=n, temperature=temperature,
                        max_tokens=max_tokens, top_p=req.top_p,
                    )
                sel_patch = {}  # frontend tracks the base-model selection (sentinel)
            else:
                if not req.run_id:
                    raise ValueError("one of run_id / base_model / sampler_path / openrouter_model is required")
                run = discovery.find_run(req.run_id)
                if run is None:
                    raise ValueError(f"unknown run: {req.run_id}")
                if not run.base_model:
                    raise ValueError(f"run {req.run_id} has no base_model in config.json")
                if run.sampleable is False:
                    raise ValueError(run.unsampleable_reason or "run is not sampleable")
                ckpt = _resolve_checkpoint(run, req.checkpoint)
                if ckpt is None:
                    raise ValueError(
                        f"no sampler checkpoint '{req.checkpoint or '(last)'}' in run {req.run_id}"
                    )
                renderer_name = select_renderer_name(run.base_model, run.renderer_name, req.thinking)
                label = f"{run.name}@{ckpt.name}"
                if stream:
                    _, prompt_text, stop = await get_sampler().render(
                        run.base_model, renderer_name, sampling_msgs
                    )
                    produce_iter = tinker_oai.completions_stream(
                        model=ckpt.sampler_path, prompt=prompt_text, stop=stop,
                        temperature=temperature, max_tokens=max_tokens, top_p=req.top_p,
                    )
                else:
                    produce_iter = get_sampler().sample_stream(
                        base_model=run.base_model, sampler_path=ckpt.sampler_path,
                        renderer_name=renderer_name, messages=sampling_msgs, n=n,
                        temperature=temperature, max_tokens=max_tokens, top_p=req.top_p,
                    )
                sel_patch = (
                    {"compare_run_id": run.id, "compare_checkpoint": ckpt.name}
                    if is_compare_panel
                    else {"run_id": run.id, "checkpoint": ckpt.name}
                )
        except Exception as e:
            # pre-start failure (unknown/unsampleable run, bad checkpoint): surface
            # on BOTH the caller stream and the bus so the browser panel shows it.
            if req.broadcast:
                await BUS.broadcast("chat_error", {"chat_id": None, "panel": req.panel, "error": str(e), "client_token": req.client_token})
            yield {"event": "error", "data": json.dumps({"error": str(e)})}
            return

        # ── chat lifecycle: atomic id + running, reflect into state ──────────
        # Each panel commits to its OWN transcript so compare mode is multi-turn:
        # primary → state.messages, compare → state.compare_messages.
        state_patch = dict(sel_patch)
        if is_compare_panel:
            state_patch["compare_messages"] = msgs
        else:
            state_patch.update(
                messages=msgs, system_prompt=req.system_prompt, temperature=temperature,
                max_tokens=max_tokens, n_samples=n, thinking=req.thinking, top_p=req.top_p,
            )
        chat_id = await BUS.chat_begin(**state_patch)
        if req.broadcast:
            await BUS.broadcast(
                "chat_start",
                {"chat_id": chat_id, "panel": req.panel, "n": n, "label": label, "client_token": req.client_token},
            )

        # ── stream samples (delta chunks for n==1, whole samples otherwise) ──
        produced: dict[int, str] = {}
        try:
            async for item in produce_iter:
                if "delta" in item:
                    item.setdefault("sample_index", 0)
                    yield {"event": "delta", "data": json.dumps(item)}
                    if req.broadcast:
                        await BUS.broadcast("delta", {"chat_id": chat_id, "panel": req.panel, **item})
                    continue
                if "content" in item and "error" not in item:
                    item.setdefault("sample_index", 0)
                    produced[item["sample_index"]] = item["content"]
                yield {"event": "message", "data": json.dumps(item)}
                if req.broadcast:
                    await BUS.broadcast("sample", {"chat_id": chat_id, "panel": req.panel, **item})
            # multi-turn memory: commit the representative assistant turn (sample 0)
            # into THIS panel's transcript so the next turn carries it.
            end_patch: dict = {}
            if produced:
                chosen = produced[0] if 0 in produced else produced[min(produced)]
                turn = [*msgs, {"role": "assistant", "content": chosen}]
                end_patch["compare_messages" if is_compare_panel else "messages"] = turn
            await BUS.chat_end("chat_done", **end_patch)
            if req.broadcast:
                await BUS.broadcast(
                    "chat_done",
                    {"chat_id": chat_id, "panel": req.panel, "client_token": req.client_token},
                )
            yield {"event": "done", "data": "{}"}
        except Exception as e:
            await BUS.chat_end("chat_done")
            if req.broadcast:
                await BUS.broadcast(
                    "chat_error",
                    {"chat_id": chat_id, "panel": req.panel, "error": str(e), "client_token": req.client_token},
                )
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(gen())


@router.post("/close")
async def close_sessions() -> dict:
    from ..tinker_sampler import close_sampler

    await close_sampler()
    return {"status": "ok"}
