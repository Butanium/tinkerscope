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
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Literal

import anyio
from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .. import discovery, openrouter, tinker_oai
from ..state import BUS
from ..tinker_sampler import get_sampler, select_renderer_name

router = APIRouter(prefix="/api", tags=["chat"])


@dataclass
class _InFlight:
    """A live chat's producer task + a flag the cancel endpoint sets before it
    cancels. `cancelled` lets the terminal path tell an intentional stop (→ error
    terminal on 0 samples, so nothing folds an empty branch) from a natural end."""

    task: asyncio.Task
    cancelled: bool = False


# chat_id -> the task pumping its samples. POST /api/chat/{chat_id}/cancel cancels
# that task to stop a chat THIS client doesn't own (CLI / another tab), which unwinds
# the producer (its finally cancels the remote sampling tasks) and drives the SAME
# guaranteed-terminal path as a client disconnect. Entries are removed by gen()'s
# finally, so a cancel for an already-finished chat is a harmless not_found.
_INFLIGHT: dict[int, _InFlight] = {}


class ChatMessage(BaseModel):
    role: str
    content: str
    # Separated chain-of-thought for an assistant turn (answer-only `content`). Carried so
    # the native renderer paths can rebuild the full turn and apply the model's own history
    # policy (strip_thinking_from_history / preserve). None for non-thinking turns.
    reasoning: str | None = None


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
    # False / True pick one renderer mode; "both" draws n_samples WITHOUT thinking
    # (sample_index 0..n-1) plus n_samples WITH (n..2n-1) in one chat — 2n total.
    thinking: bool | Literal["both"] = False
    # Which half(s) of a send the trailing-assistant prefill applies to:
    #   "all"       — prefill both the thinking and non-thinking sides (default)
    #   "think"     — prefill the thinking side only; drop it from the non-thinking side
    #   "non_think" — prefill the non-thinking side only; drop it from the thinking side
    # In thinking="both" the two sides run together, so the mismatched half is
    # stripped; in a single-mode send (thinking True/False) a scope that doesn't
    # match that mode drops the prefill entirely. No-op when the messages don't end
    # with an assistant turn. `prefill_thinking_only` is the deprecated predecessor,
    # kept as an alias (True ≡ scope "think") for any stale client.
    prefill_scope: Literal["all", "think", "non_think"] | None = None
    prefill_thinking_only: bool = False
    top_p: float | None = None
    top_k: int | None = None
    presence_penalty: float | None = None
    repetition_penalty: float | None = None
    # Capture per-token logprobs + top-5 alternatives on the NATIVE tinker
    # sampling paths (run_id / base_model n>1). Default ON; costs one extra
    # prefill-only call per sample (see tinker_sampler._token_logprobs). The
    # token-streamed oai paths and OpenRouter ignore it.
    logprobs: bool = True
    # live-drive routing
    panel: str = "primary"                  # "primary" | "compare"
    broadcast: bool = True                   # mirror samples to the state bus
    # Opaque client-minted ownership token, echoed verbatim on the chat_start /
    # chat_done / chat_error bus broadcasts. The browser uses it to tell its OWN
    # chats (which it folds from this request's response stream) apart from
    # external ones (CLI / another tab) it must fold via reconciliation. The
    # server never interprets it.
    client_token: str | None = None


def _drop_trailing_assistant(msgs: list[dict]) -> list[dict]:
    """Strip the prefill convention's trailing assistant turn (used by
    prefill_scope to drop the prefill from whichever side it doesn't apply to)."""
    return msgs[:-1] if msgs and msgs[-1]["role"] == "assistant" else msgs


def _resolve_prefill_scope(req: ChatRequest) -> str:
    """Effective prefill scope: the explicit field wins; else the deprecated
    `prefill_thinking_only` bool maps to "think"/"all"."""
    if req.prefill_scope is not None:
        return req.prefill_scope
    return "think" if req.prefill_thinking_only else "all"


def _prep_prefill_lists(
    sampling_msgs: list[dict], native_msgs: list[dict], scope: str
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Apply prefill_scope to the full (prefill-carrying) message lists.

    Returns (sampling_on, native_on, sampling_off, native_off): the *_on lists feed
    think=True renderer paths, the *_off lists feed think=False paths (a plain
    thinking=False send uses *_off for everything; thinking="both" uses *_on for the
    thinking half and *_off for the non-thinking half). The trailing-assistant
    prefill is dropped from whichever side `scope` excludes — "think" drops it from
    *_off, "non_think" from *_on, "all" keeps it on both. No-op when the lists don't
    end with an assistant turn."""
    sampling_off = _drop_trailing_assistant(sampling_msgs) if scope == "think" else sampling_msgs
    native_off = _drop_trailing_assistant(native_msgs) if scope == "think" else native_msgs
    sampling_on = _drop_trailing_assistant(sampling_msgs) if scope == "non_think" else sampling_msgs
    native_on = _drop_trailing_assistant(native_msgs) if scope == "non_think" else native_msgs
    return sampling_on, native_on, sampling_off, native_off


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


async def _dual(
    off_iter: AsyncIterator[dict], on_iter: AsyncIterator[dict], n_off: int
) -> AsyncIterator[dict]:
    """thinking="both": run the non-thinking and thinking batches CONCURRENTLY,
    yielding samples from either as they finish. Each item gets a `thinking` tag
    and the thinking batch's sample_index is offset by n_off, so 0..n-1 are the
    non-thinking half and n..2n-1 the thinking half (foldAssistant orders by
    index, so the browser shows them in that order regardless of arrival).
    Mirrors _fanout's cancel-on-disconnect: consumer gone → both pumps cancelled."""
    queue: asyncio.Queue = asyncio.Queue()

    async def pump(it: AsyncIterator[dict], offset: int, thinking: bool) -> None:
        try:
            async for item in it:
                item["sample_index"] = item.get("sample_index", 0) + offset
                item["thinking"] = thinking
                await queue.put(item)
            await queue.put(None)  # this batch exhausted
        except Exception as e:  # transported, not swallowed — re-raised by the consumer
            await queue.put(e)

    tasks = [
        asyncio.create_task(pump(off_iter, 0, False)),
        asyncio.create_task(pump(on_iter, n_off, True)),
    ]
    try:
        pending = len(tasks)
        while pending:
            item = await queue.get()
            if item is None:
                pending -= 1
            elif isinstance(item, Exception):
                raise item
            else:
                yield item
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@router.post("/chat")
async def chat(req: ChatRequest):
    # `msgs` is the per-panel transcript echo (answer-only, no system prompt). From it:
    #  - sampling_msgs: {role, content} ONLY (+ system) — fed to the OpenAI-style endpoints
    #    (OpenRouter, loose checkpoint), which would choke on an extra `reasoning` key.
    #  - native_msgs: also carries `reasoning` (+ system), fed to the native renderer paths
    #    (base_model / run_id) so the renderer rebuilds the full turn and applies its OWN
    #    history policy (strip_thinking_from_history / preserve) instead of us pre-stripping.
    msgs = [{"role": m.role, "content": m.content} for m in req.messages]
    sampling_msgs = list(msgs)
    native_msgs = [
        {"role": m.role, "content": m.content, **({"reasoning": m.reasoning} if m.reasoning else {})}
        for m in req.messages
    ]
    if req.system_prompt and not any(m["role"] == "system" for m in msgs):
        sys_msg = {"role": "system", "content": req.system_prompt}
        sampling_msgs = [sys_msg, *sampling_msgs]
        native_msgs = [sys_msg, *native_msgs]

    # prefill_scope decides which side keeps the trailing-assistant prefill.
    # sampling_msgs/native_msgs become the ON (think=True) lists; the *_off variants
    # feed think=False paths. A plain thinking=False request then swaps the ON lists
    # to the off lists. (Logic + the per-scope matrix live in _prep_prefill_lists,
    # unit-tested in tests/test_chat_prefill.py.)
    scope = _resolve_prefill_scope(req)
    sampling_msgs, native_msgs, sampling_off, native_off = _prep_prefill_lists(
        sampling_msgs, native_msgs, scope
    )
    if req.thinking is False:
        sampling_msgs, native_msgs = sampling_off, native_off

    temperature = req.temperature if req.temperature is not None else 1.0
    max_tokens = req.max_tokens or 1024
    n = max(1, min(req.n_samples or 1, 200))
    # thinking="both" = a non-thinking batch of n + a thinking batch of n in one
    # chat (2n samples total). Only meaningful on paths where WE pick the renderer
    # (openrouter / base_model / run_id); the loose sampler_path ignores thinking
    # today and keeps doing so (n samples, server default template).
    both = req.thinking == "both"
    # n==1 token-streams live through the oai endpoints. EXCEPTION: discovered LoRA
    # runs (run_id) — tinker's oai /completions serves the BASE model for a LoRA
    # sampler path (tinker-feedback#125), so the live single-sample path would
    # silently show base output instead of the finetune. Until that's fixed, route
    # run_id n==1 through the SAME native sample_stream path as n>1 (whole sample, no
    # token streaming). base_model / loose / openrouter are unaffected and keep
    # streaming (base via /completions is correct; loose via /chat applies adapters).
    # TODO(tinker-feedback#125): when fixed, drop `and req.run_id is None` to restore
    # token streaming for single samples from LoRA runs.
    stream = (n == 1) and (req.run_id is None) and not both

    async def gen():
        # ── resolve the model + build the per-sample producer ───────────────
        total = n  # expected sample count for this chat (2n when thinking="both")
        try:
            if req.openrouter_model:
                label = req.openrouter_model

                def or_kwargs(think: bool) -> dict:
                    return dict(
                        model=req.openrouter_model,
                        messages=sampling_msgs if think else sampling_off,
                        temperature=temperature, max_tokens=max_tokens, thinking=think,
                        top_p=req.top_p, top_k=req.top_k, presence_penalty=req.presence_penalty,
                        repetition_penalty=req.repetition_penalty,
                    )

                if both:
                    total = 2 * n
                    produce_iter = _dual(
                        _fanout(lambda i: openrouter.sample_one(**or_kwargs(False)), n),
                        _fanout(lambda i: openrouter.sample_one(**or_kwargs(True)), n),
                        n,
                    )
                elif stream:
                    produce_iter = openrouter.sample_one_stream(**or_kwargs(bool(req.thinking)))
                else:
                    produce_iter = _fanout(
                        lambda i: openrouter.sample_one(**or_kwargs(bool(req.thinking))), n
                    )
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

                def base_iter(think: bool):
                    return get_sampler().sample_stream(
                        base_model=req.base_model, sampler_path=None,
                        renderer_name=select_renderer_name(req.base_model, None, think),
                        messages=native_msgs if think else native_off,
                        n=n, temperature=temperature,
                        max_tokens=max_tokens, top_p=req.top_p,
                        logprobs=req.logprobs,
                    )

                if both:
                    total = 2 * n
                    produce_iter = _dual(base_iter(False), base_iter(True), n)
                elif stream:
                    renderer_name = select_renderer_name(req.base_model, None, req.thinking)
                    _, prompt_text, stop = await get_sampler().render(
                        req.base_model, renderer_name, native_msgs
                    )
                    produce_iter = tinker_oai.completions_stream(
                        model=req.base_model, prompt=prompt_text, stop=stop,
                        temperature=temperature, max_tokens=max_tokens, top_p=req.top_p,
                    )
                else:
                    produce_iter = base_iter(bool(req.thinking))
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
                label = f"{run.name}@{ckpt.name}"

                def run_iter(think: bool):
                    return get_sampler().sample_stream(
                        base_model=run.base_model, sampler_path=ckpt.sampler_path,
                        renderer_name=select_renderer_name(run.base_model, run.renderer_name, think),
                        messages=native_msgs if think else native_off,
                        n=n,
                        temperature=temperature, max_tokens=max_tokens, top_p=req.top_p,
                        logprobs=req.logprobs,
                    )

                if both:
                    total = 2 * n
                    produce_iter = _dual(run_iter(False), run_iter(True), n)
                elif stream:
                    renderer_name = select_renderer_name(run.base_model, run.renderer_name, req.thinking)
                    _, prompt_text, stop = await get_sampler().render(
                        run.base_model, renderer_name, native_msgs
                    )
                    produce_iter = tinker_oai.completions_stream(
                        model=ckpt.sampler_path, prompt=prompt_text, stop=stop,
                        temperature=temperature, max_tokens=max_tokens, top_p=req.top_p,
                    )
                else:
                    produce_iter = run_iter(bool(req.thinking))
                sel_patch = {"run_id": run.id, "checkpoint": ckpt.name}
        except Exception as e:
            # pre-start failure (unknown/unsampleable run, bad checkpoint): surface
            # on BOTH the caller stream and the bus so the browser panel shows it.
            if req.broadcast:
                await BUS.broadcast("chat_error", {"chat_id": None, "panel": req.panel, "error": str(e), "client_token": req.client_token})
            yield {"event": "error", "data": json.dumps({"error": str(e)})}
            return

        # ── chat lifecycle: atomic id + running, reflect into state ──────────
        # Per-panel: `panel` routes this panel's selection + transcript echo into
        # panels[panel] (multi-turn memory). Sampling params are GLOBAL (shared
        # across panels) — set at the top level, no per-panel author race.
        state_patch = {
            "panel": req.panel,
            "messages": msgs,
            **sel_patch,
            "system_prompt": req.system_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "n_samples": n,
            "thinking": req.thinking,
            "top_p": req.top_p,
        }
        chat_id = await BUS.chat_begin(**state_patch)
        # Stamp every broadcast with the conversation open WHEN THE CHAT STARTED (read
        # synchronously after chat_begin — no await, so it can't drift). The browser's
        # external-fold hook folds a chat_done onto a panel id ONLY if this id matches
        # the conversation it currently has open; panel ids are re-minted across
        # conversations on a shared, process-wide bus, so without this a chat generated
        # for one conversation grafts onto a reused panel of another. None when no
        # conversation is open (CLI-only / legacy) — the browser folds those (lockstep).
        conv_id = BUS.state.conversation_id
        if req.broadcast:
            await BUS.broadcast(
                "chat_start",
                {"chat_id": chat_id, "panel": req.panel, "n": total, "label": label,
                 "client_token": req.client_token, "conversation_id": conv_id},
            )

        # ── stream samples, with EXACTLY ONE terminal event on every exit path ──
        # Three ways this chat can end — done / producer error / cancelled (client
        # disconnect OR the cancel endpoint) — must each fire one chat_end + one
        # terminal broadcast, or `running` sticks true forever and every busy-surface
        # (spinner, composer lock, Stop button) wedges. `_terminal()` is that single
        # exit, idempotent via `terminated`.
        produced: dict[int, str] = {}
        incorporated: dict[int, bool] = {}  # did the backend already fold the prefill in?
        terminated = False

        async def _terminal(*, error: str | None = None, cancelled: bool = False) -> None:
            """The one terminal exit. Decides the event + whether to commit the turn:
              - error given          → chat_error(error), no commit (a mid-stream fault)
              - ≥1 completed sample   → chat_done + commit sample 0 (partial data is real
                                        data — a stop after some samples keeps them)
              - cancelled, 0 samples  → chat_error("cancelled") so no consumer folds an
                                        empty branch (#onExternalDone reconciles chat_done)
              - clean end, 0 samples  → chat_done, nothing to commit (every sample errored)
            """
            nonlocal terminated
            if terminated:
                return
            terminated = True
            if error is not None:
                event, err_msg, commit = "chat_error", error, False
            elif produced:
                event, err_msg, commit = "chat_done", None, True
            elif cancelled:
                event, err_msg, commit = "chat_error", "cancelled", False
            else:
                event, err_msg, commit = "chat_done", None, False
            # multi-turn memory: commit the representative assistant turn (sample 0)
            # into THIS panel's transcript so the next turn carries it. A trailing
            # assistant message in `msgs` is a prefill — merge it into ONE turn
            # (drop the standalone prefill node); native sampling already folded it
            # into `chosen`, other paths return continuation-only so we prepend.
            end_patch: dict = {}
            if commit:
                idx0 = 0 if 0 in produced else min(produced)
                chosen = produced[idx0]
                if msgs and msgs[-1]["role"] == "assistant":
                    full = chosen if incorporated.get(idx0) else msgs[-1]["content"] + chosen
                    turn = [*msgs[:-1], {"role": "assistant", "content": full}]
                else:
                    turn = [*msgs, {"role": "assistant", "content": chosen}]
                end_patch = {"panel": req.panel, "messages": turn}
            await BUS.chat_end(event, **end_patch)
            if req.broadcast:
                # conversation_id scopes the browser's external fold (see #onExternalDone):
                # every terminal flavour — done / error / cancelled — carries the origin stamp.
                payload = {"chat_id": chat_id, "panel": req.panel, "client_token": req.client_token,
                           "conversation_id": conv_id}
                if err_msg is not None:
                    payload["error"] = err_msg
                await BUS.broadcast(event, payload)

        # Run the producer in its OWN task, relaying items through a queue. This is what
        # lets the cancel endpoint stop a chat we're not the consumer of: cancelling
        # `worker` closes produce_iter (its finally cancels the remote sampling tasks)
        # and makes the drain loop below fall through to _terminal — the same path a
        # client disconnect takes. Best-effort on the remote side: the tinker SDK runs
        # sample calls on its own loop, so a cancel only stops us listening, never the
        # remote compute already in flight.
        q: asyncio.Queue = asyncio.Queue()

        async def _pump() -> None:
            try:
                async for item in produce_iter:
                    q.put_nowait(("item", item))
            except Exception as e:  # producer fault — transported, _terminal reports it
                q.put_nowait(("error", f"{type(e).__name__}: {e}"))
            finally:
                q.put_nowait(("end", None))  # non-blocking (unbounded q) — safe under cancel

        worker = asyncio.create_task(_pump())
        inflight = _InFlight(task=worker)
        _INFLIGHT[chat_id] = inflight

        prod_error: str | None = None
        try:
            while True:
                kind, payload = await q.get()
                if kind == "end":
                    break
                if kind == "error":
                    prod_error = payload
                    continue  # keep draining whatever the producer already queued
                item = payload
                if "delta" in item:
                    item.setdefault("sample_index", 0)
                    yield {"event": "delta", "data": json.dumps(item)}
                    if req.broadcast:
                        await BUS.broadcast("delta", {"chat_id": chat_id, "panel": req.panel, **item})
                    continue
                if "content" in item and "error" not in item:
                    item.setdefault("sample_index", 0)
                    produced[item["sample_index"]] = item["content"]
                    incorporated[item["sample_index"]] = bool(item.get("prefill_incorporated"))
                yield {"event": "message", "data": json.dumps(item)}
                if req.broadcast:
                    await BUS.broadcast("sample", {"chat_id": chat_id, "panel": req.panel, **item})
            if prod_error is not None:
                await _terminal(error=prod_error)
                yield {"event": "error", "data": json.dumps({"error": prod_error})}
            else:
                await _terminal(cancelled=inflight.cancelled)
                yield {"event": "done", "data": "{}"}
        finally:
            _INFLIGHT.pop(chat_id, None)
            if not worker.done():
                worker.cancel()  # client gone / endpoint cancel → stop the producer
            # A client disconnect cancels gen() mid-`await q.get()`: the CancelledError
            # (BaseException) / GeneratorExit skips both _terminal calls above. Fire it
            # here, SHIELDED so its awaits (BUS lock, broadcast) survive the cancellation
            # instead of being re-cancelled at the first checkpoint; the original
            # cancellation re-raises after the scope exits (we never swallow it).
            if not terminated:
                with anyio.CancelScope(shield=True):
                    await _terminal(cancelled=True)

    return EventSourceResponse(gen())


@router.post("/chat/{chat_id}/cancel")
async def cancel_chat(chat_id: int) -> dict:
    """Stop an in-flight chat by id. This is how the browser's "Stop all" reaches a
    chat it doesn't own (fired by tinkpg or another tab, so it has no local AbortController
    to trip): cancelling the producer task drives the SAME guaranteed terminal as a client
    disconnect — chat_end fires, `running` clears for every subscriber, and any samples
    that already completed are still committed. Idempotent: a chat that already ended
    isn't in the registry, so this is a harmless not_found."""
    inflight = _INFLIGHT.get(chat_id)
    if inflight is None:
        return {"status": "not_found", "chat_id": chat_id}
    inflight.cancelled = True
    inflight.task.cancel()
    return {"status": "cancelling", "chat_id": chat_id}


@router.post("/close")
async def close_sessions() -> dict:
    from ..tinker_sampler import close_sampler

    await close_sampler()
    return {"status": "ok"}
