"""Chat / sampling endpoint (SSE).

One request samples from ONE model: a Tinker LoRA checkpoint (run_id), a raw
Tinker base model (base_model), a "loose" Tinker checkpoint the oai endpoint
serves but that isn't in the scan dir (sampler_path), or an OpenRouter reference
(openrouter_model). Compare mode = the caller fires two requests, tagged
panel=primary and panel=compare.

Streaming (your design): for n==1 we stream tokens live through tinker's
OpenAI-compatible endpoint (or OpenRouter's), emitting `delta` events; for n>1 we
keep the native batched fan-out (each sample pops in whole — the distribution
view). tinker's native SamplingClient has no token streaming, so the streaming
n=1 path routes through the oai endpoint instead. But two model kinds forgo the
stream and ALWAYS sample native, for response fidelity the oai wire can't give:

  - run_id (LoRA ckpt)   -> native sample_stream for ALL n (no token streaming):
                            tinker's oai /completions serves BASE for a LoRA sampler
                            path (tinker-feedback#125), so the streamed single-sample
                            path would silently show base output. Restore the n==1
                            /completions path once that's fixed.
  - base_model           -> native sample_stream for ALL n (no token streaming): the
                            oai /completions path skips renderer.parse_response (so
                            channel-CoT families — gpt-oss — leak thinking into
                            `content` with thinking off) and carries no raw_meta /
                            token_logprobs. The native path renders our training-
                            faithful prompt AND parses the response, so all three
                            are correct.
  - sampler_path (loose) -> native sample_stream for ALL n, same as run_id: tinker
                            knows the base model a bare tinker:// URI serves against
                            (resolve_base_model), so we render locally — raw_meta /
                            token_logprobs / faithful renderer + thinking toggle. No oai
                            fallback (if the base can't resolve, tinker can't serve the
                            ckpt either — that's an error, not a degraded path).
  - openrouter_model     -> OpenRouter /chat/completions
  - vllm_model           -> the configured vLLM server (api/vllm_sampler.py): native-
                            shaped samples (token ids, logprobs + top-K, loom) via its
                            /tokenize + /v1/completions; token-streams at n==1.

Only openrouter and vllm_model token-stream at n==1.

Each completed sample is yielded to the caller (CLI stdout) and broadcast to the
state bus (browser live view), tagged with chat_id + panel. chat_id is allocated
atomically (BUS.chat_begin); `running` is an in-flight counter (BUS.chat_end) so
concurrent chats (two compare panels, or CLI + browser) don't collide.

Server-authored folds (HANDOFF_SERVER_AUTHORITY §4.3, P2): a request carrying
`parent_node` has its completed samples folded into the workspace TREE at
terminal — assistant siblings under that user node, one `add_nodes` op through
`workspace_store.apply_ops` (light nodes + write-once blobs, rev++) fanned out as
a bus `ops` event BEFORE chat_done, with a `folded` manifest on the terminal
broadcast mapping sample_index → server-minted node id. The fold is the ONLY
persistence a chat has (P3 removed the bus transcript echo): a no-placement
fire is fully ephemeral — bucket render + caller stream, nothing stored
(HANDOFF_SERVER_AUTHORITY §9.4's accepted default). While the chat runs, its (workspace, panel, parent) placement
is registered (`api/inflight.py`) and a `delete` op pruning the parent's subtree
is rejected.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .. import discovery, inflight, openrouter, tinker_oai, tree_ops, vllm_sampler, workspace_store
from ..state import DEFAULT_PANEL_ID, BUS
from ..tinker_sampler import get_sampler, select_renderer_name
from .models import ckpt_label
from .workspaces import _broadcast_ops

log = logging.getLogger("tinkerscope.chat")

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

# Strong refs to in-flight terminal-event tasks (see _terminal in gen()): the loop
# holds only weak refs, and the awaiting frame may be torn down by the very
# cancellation the task is shielding against.
_TERMINAL_TASKS: set[asyncio.Task] = set()

# Strong refs to the background drivers of detached (fire-and-forget) chats: with
# no client consuming the SSE, nothing else keeps the task alive to completion.
_DETACHED_TASKS: set[asyncio.Task] = set()


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
    # A model served by the configured vLLM server ($TINKERSCOPE_VLLM_URL; the ids
    # are on /api/vllm-models). Sampled like a NATIVE model — token-id prompts,
    # per-token logprobs + top-K, loom `continue_tokens` — see api/vllm_sampler.py.
    vllm_model: str | None = None
    # workspace + params (numeric params tolerate None → server defaults, so a
    # transiently-empty UI input can't 422 the request)
    messages: list[ChatMessage]
    system_prompt: str | None = None
    # Thread-level system prompt, composed OVER the global/call system part at
    # sample time (compose_system: "\n"-join, empty parts skipped) and recorded
    # on the thread's ROOT node. None ≡ "" = no thread part. (Until the P3
    # review fixes, None inherited the target panel's mirrored
    # PanelState.thread_system_prompt — retired: every client sends the field
    # explicitly, and the one omitter was `tinkpg probe`, silently sampling
    # under the OPEN thread's prompt. The mirror is display state now, never
    # an input.)
    thread_system_prompt: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    n_samples: int | None = None
    # False / True pick one renderer mode; "both" draws n_samples WITHOUT thinking
    # (sample_index 0..n-1) plus n_samples WITH (n..2n-1) in one chat — 2n total.
    # None = unset (resolved by params_scope — see resolve_params).
    thinking: bool | Literal["both"] | None = None
    # Param routing — the "two routes" contract (see resolve_params):
    #   "global" (default; the browser) — this chat's params ARE the shared
    #     sidebar state: explicit values are written back to the state bus,
    #     absent ones fall back to fixed server defaults (legacy behavior).
    #   "call" (the CLI) — params apply to THIS chat only: explicit values
    #     override, absent ones inherit the CURRENT global state, and nothing
    #     is written back — a CLI probe can't clobber the user's sidebar.
    params_scope: Literal["global", "call"] = "global"
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
    # LOOM (exact token-level continue): token ids appended VERBATIM after the
    # rendered prompt — a stored sample's generated prefix + a picked alternative,
    # so the model continues from that exact token state (no re-tokenization).
    # Native tinker paths + vllm_model only; invalid with openrouter_model (no
    # token-level control) and with thinking="both" (two renderer modes can't share
    # one token prefix). See tinker_sampler.sample_stream / vllm_sampler.
    continue_tokens: list[int] | None = None
    # Exact renderer override for the native paths. Loom fires send the renderer
    # recorded in the source turn's raw_meta, so the re-rendered prompt is the one
    # the prefix tokens actually continued. None = select_renderer_name as usual.
    renderer_name: str | None = None
    # live-drive routing
    panel: str = DEFAULT_PANEL_ID           # schema fallback; every client sends one
    # The workspace this chat belongs to. The browser always sends
    # its own; the CLI omits it and inherits the bus's current one. Stamped onto the
    # chat_done / chat_error broadcasts so a browser tab on ANOTHER workspace skips
    # the external fold instead of grafting a foreign reply onto a reused panel id.
    workspace_id: str | None = None
    # SERVER-AUTHORED FOLD placement (HANDOFF_SERVER_AUTHORITY §4.3): the id of
    # the USER node this chat's samples fold under at terminal — persisted by the
    # writer's own `add_nodes` op BEFORE the fire (send and regen are the same
    # shape; the request carries no user content of its own). Setting it makes
    # the chat's home workspace mandatory: explicit `workspace_id` → else the
    # bus's open workspace → else 400 (a fold with nowhere to hang is a request
    # error, not something to guess). None = legacy fire — echo-only commit,
    # folding (if any) is the browser's business, exactly the pre-P2 contract.
    parent_node: str | None = None
    broadcast: bool = True                   # mirror samples to the state bus
    # May this chat WRITE shared state? TRUE is the interactive contract: the
    # chat_begin patch panel-routes the selection + thread-system mirror into
    # panels[panel], and `parent_node` (which requires it — a fold IS a commit)
    # persists the samples. FALSE makes the call a pure READ: nothing
    # panel-routes into the bus (a probe of model B must not rebind the panel
    # the human has bound to model A — the saved-layout rewrite chain, review
    # 2026-08-12), no fold. Combined with broadcast=false (the `tinkpg probe`
    # shape) the chat is bus-SILENT: no chat_start/chat_end events, `running`
    # never flips.
    commit: bool = True
    # Detached (fire-and-forget) mode. The browser sets this so its POST returns
    # IMMEDIATELY instead of holding the SSE stream open for the whole generation:
    # the producer runs as a background task broadcasting ONLY to the bus, and the
    # browser renders/folds the panel from /api/state/events like a tinkpg-driven
    # chat. This is what lets N panels generate at once — a held stream per panel
    # would exhaust the browser's ~6 per-host HTTP/1.1 connections. Requires
    # broadcast (there's no response stream to read). Stop reaches it via the
    # cancel endpoint (no client disconnect to trip cancel-on-disconnect), and a
    # closed tab no longer cancels it — it runs to completion server-side.
    detached: bool = False
    # Opaque client-minted ownership token, echoed verbatim on the chat_start /
    # chat_done / chat_error bus broadcasts. The browser uses it to tell its OWN
    # chats (which it folds from the bus bucket on chat_done) apart from external
    # ones (CLI / another tab) it must fold via transcript reconciliation. The
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


def _prefill_reaches_sample(scope: str, thinking: bool | str, n: int, sample_index: int) -> bool:
    """Did the trailing-assistant prefill reach the prompt for THIS sample's half?

    In thinking="both" the non-thinking half is sample_index 0..n-1 and the thinking
    half n..2n-1; in a single-mode send every sample's half IS `thinking`. "all"
    reaches both halves, "think" only the thinking half, "non_think" only the
    non-thinking half — the same split _prep_prefill_lists applies to the prompt."""
    is_thinking = (sample_index >= n) if thinking == "both" else (thinking is True)
    return scope == "all" or (scope == "think") == is_thinking


def _committed_turn(
    msgs: list[dict], chosen: str, incorporated: bool, prefill_reached: bool
) -> list[dict]:
    """The representative assistant turn stored in the panel transcript (multi-turn
    memory). A trailing assistant message in `msgs` is a prefill: if it REACHED this
    sample's half, merge it into one turn — native paths already folded it into
    `chosen` (`incorporated`), other paths return continuation-only so we prepend. If
    it did NOT reach this half (a one-sided prefill_scope dropped it from the prompt
    this sample saw), drop the standalone prefill node and store the fresh completion
    alone — otherwise the transcript would claim the model saw a prefill it never did.
    No trailing assistant → just append the completion."""
    if msgs and msgs[-1]["role"] == "assistant":
        if prefill_reached:
            full = chosen if incorporated else msgs[-1]["content"] + chosen
            return [*msgs[:-1], {"role": "assistant", "content": full}]
        return [*msgs[:-1], {"role": "assistant", "content": chosen}]
    return [*msgs, {"role": "assistant", "content": chosen}]


# Sample fields copied verbatim onto a folded node (mirrors the browser's
# foldAssistant field list, tree.ts:325). `content` is NOT here — it goes through
# `_committed_turn` so the prefill merge is byte-identical to what the drain path
# committed (the bucket overlay and the folded node must agree at fold time).
_FOLD_SAMPLE_FIELDS = (
    "reasoning", "raw_text", "raw_meta", "finish_reason", "thinking",
    "token_logprobs", "loom_cut", "loom_text",
)


def _build_fold_nodes(
    msgs: list[dict],
    samples: dict[int, dict],
    incorporated: dict[int, bool],
    scope: str,
    thinking: "bool | str",
    n: int,
    parent_node: str,
) -> tuple[list[dict], list[dict]]:
    """The terminal fold's `add_nodes` payload: every completed sample as an
    assistant node under `parent_node`, in sample-index order (thinking="both"
    packs non-thinking 0..n-1 then thinking n..2n-1, same as `foldAssistant`).
    One op with `select` ⇒ the per-op claim rule selects the FIRST sibling.

    Returns (nodes, manifest): `nodes` ready for the op (heavy fields inline —
    the op layer splits them into write-once blobs), `manifest` the
    `[{sample_index, node_id}]` list the terminal broadcast carries so the
    browser can seed its blob cache from the bucket sample the node came from
    (positional zip against the op's nodes would shift on error samples)."""
    prefill_text = msgs[-1]["content"] if msgs and msgs[-1]["role"] == "assistant" else None
    nodes: list[dict] = []
    manifest: list[dict] = []
    for idx in sorted(samples):
        item = samples[idx]
        reached = _prefill_reaches_sample(scope, thinking, n, idx)
        content = _committed_turn(
            msgs, item.get("content", ""), bool(incorporated.get(idx)), reached
        )[-1]["content"]
        node: dict = {
            "id": tree_ops.mint_node_id(),
            "role": "assistant",
            "content": content,
            "parent": parent_node,
        }
        if prefill_text and reached:
            # Same field the browser fold records: the authored prefill, so the
            # rendered turn colors the prefilled prefix (absent when a one-sided
            # prefill_scope dropped it from this sample's half).
            node["prefill"] = prefill_text
        for k in _FOLD_SAMPLE_FIELDS:
            v = item.get(k)
            if v is not None:
                node[k] = v
        nodes.append(node)
        manifest.append({"sample_index": idx, "node_id": node["id"]})
    return nodes, manifest


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


def resolve_params(req: ChatRequest, st: Any) -> dict:
    """Effective sampling params for one chat, by params_scope (see ChatRequest).

    system_prompt="" is an explicit "no system prompt" — it never inherits (the
    chat builders treat empty as absent), so a call-scoped client can opt OUT of
    an inherited system prompt without a wire change. The inherit path skips a
    MUTED global prompt (state.system_enabled is False — kept text, power off);
    an explicit req.system_prompt applies regardless (per-call wins).

    thread_system_prompt is the request's or nothing (None ≡ ""): it is NEVER
    inherited from the panel mirror — see the field's comment on ChatRequest."""
    inherit = req.params_scope == "call"
    system_on = getattr(st, "system_enabled", None) is not False
    return {
        "system_prompt": req.system_prompt if req.system_prompt is not None
        else (st.system_prompt if inherit and system_on else None),
        "thread_system_prompt": req.thread_system_prompt or "",
        "temperature": req.temperature if req.temperature is not None
        else (st.temperature if inherit else 1.0),
        "max_tokens": req.max_tokens or (st.max_tokens if inherit else 1024),
        "n_samples": max(1, min(req.n_samples or (st.n_samples if inherit else 1), 200)),
        "thinking": req.thinking if req.thinking is not None
        else (st.thinking if inherit else False),
        "top_p": req.top_p if req.top_p is not None else (st.top_p if inherit else None),
    }


def compose_system(global_part: str | None, thread_part: str | None) -> str:
    """The effective system prompt for one chat: the global/call part with the
    thread part appended (newline join, empty/None parts skipped). "" ⇒ no
    system message at all. The ONE compose site for browser + CLI."""
    return "\n".join(p for p in (global_part, thread_part) if p)


@router.post("/chat")
async def chat(req: ChatRequest):
    # ── fold placement (§4.3) — request-shape errors are 400s, up front ──────
    # `fold_ws` is resolved ONCE here (explicit → the bus's open workspace) so the
    # fold target and the terminal-broadcast stamp cannot drift apart if the bus
    # flips workspaces mid-generation (two tabs can race exactly that way).
    fold_ws: str | None = None
    if req.parent_node is not None:
        if not req.commit:
            raise HTTPException(
                400, "parent_node with commit=false is contradictory — a fold IS a commit"
            )
        fold_ws = req.workspace_id or BUS.state.workspace_id
        if not fold_ws:
            raise HTTPException(
                400,
                "parent_node needs a home workspace: send workspace_id, or open a workspace "
                "on the bus first (HANDOFF_SERVER_AUTHORITY §4.4)",
            )
    # `msgs` is the request's message list (answer-only, no system prompt). From it:
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
    params = resolve_params(req, BUS.state)
    system_prompt, top_p = params["system_prompt"], params["top_p"]
    thread_system = params["thread_system_prompt"]
    thinking: "bool | str" = params["thinking"]
    # `system_prompt` stays the GLOBAL/call part (it's what a "global"-scope chat
    # writes back to the shared state below); only the composed effective prompt
    # reaches the model.
    effective_system = compose_system(system_prompt, thread_system)
    if effective_system and not any(m["role"] == "system" for m in msgs):
        sys_msg = {"role": "system", "content": effective_system}
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
    if thinking is False:
        sampling_msgs, native_msgs = sampling_off, native_off

    temperature, max_tokens, n = params["temperature"], params["max_tokens"], params["n_samples"]
    # thinking="both" = a non-thinking batch of n + a thinking batch of n in one
    # chat (2n samples total). Only meaningful on paths where WE pick the renderer
    # (openrouter / base_model / run_id / a loose sampler_path whose base resolves).
    both = thinking == "both"
    # n==1 token-streams live through the oai endpoints — EXCEPT run_id and
    # base_model, which always route through native sample_stream (whole sample, no
    # token streaming) for response fidelity the oai wire loses:
    #   - run_id: tinker's oai /completions serves the BASE model for a LoRA sampler
    #     path (tinker-feedback#125), so the live single-sample path would silently
    #     show base output instead of the finetune.
    #   - base_model: the /completions path skips renderer.parse_response (channel-CoT
    #     families like gpt-oss leak thinking into `content` with thinking off) and
    #     returns no raw_meta / token_logprobs; the native path gives all three.
    # A loose sampler_path now ALSO renders native (resolve_base_model → local render,
    # same fidelity + thinking toggle), so only openrouter and vllm_model (whose
    # stream carries the same ids + logprobs as its whole-sample path) token-stream
    # at n==1.
    # TODO(tinker-feedback#125): when fixed, drop `req.run_id is None` to restore token
    # streaming for single samples from LoRA runs (base_model stays native).
    stream = (
        (n == 1) and not both
        and req.run_id is None and req.base_model is None and req.sampler_path is None
    )

    # In-flight placement bookkeeping, shared by the wrapper (registers/backstops)
    # and _gen_inner's terminal (folds, then releases). `released` makes release
    # idempotent — the backstop and the terminal can both reach for it.
    placement: "tuple[str, str, str] | None" = None
    released = False

    def _release_placement() -> None:
        nonlocal released
        if released or placement is None:
            return
        released = True
        inflight.release(*placement)

    async def _gen_inner():
        # ── resolve the model + build the per-sample producer ───────────────
        total = n  # expected sample count for this chat (2n when thinking="both")
        try:
            if req.continue_tokens:
                if req.openrouter_model:
                    raise ValueError("continue_tokens requires a native tinker model")
                if both:
                    raise ValueError('continue_tokens is incompatible with thinking="both"')
            if req.openrouter_model:
                label = req.openrouter_model

                def or_kwargs(think: bool) -> dict:
                    return dict(
                        model=req.openrouter_model,
                        messages=sampling_msgs if think else sampling_off,
                        temperature=temperature, max_tokens=max_tokens, thinking=think,
                        top_p=top_p, top_k=req.top_k, presence_penalty=req.presence_penalty,
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
                    produce_iter = openrouter.sample_one_stream(**or_kwargs(bool(thinking)))
                else:
                    produce_iter = _fanout(
                        lambda i: openrouter.sample_one(**or_kwargs(bool(thinking))), n
                    )
                sel_patch: dict = {}
            elif req.vllm_model:
                # A vLLM-served model: native-shaped samples (token ids in and out,
                # logprobs + top-K in the sampling call, loom replay) rendered by the
                # server's own chat template. Token-streams at n==1 like OpenRouter
                # — vLLM streams the same completions it scores, nothing is lost.
                label = req.vllm_model

                def vllm_kwargs(think: bool) -> dict:
                    return dict(
                        model=req.vllm_model,
                        messages=sampling_msgs if think else sampling_off,
                        temperature=temperature, max_tokens=max_tokens, top_p=top_p,
                        top_k=req.top_k, presence_penalty=req.presence_penalty,
                        repetition_penalty=req.repetition_penalty, logprobs=req.logprobs,
                        think=think, continue_tokens=req.continue_tokens,
                    )

                if both:
                    total = 2 * n
                    produce_iter = _dual(
                        vllm_sampler.sample_stream(n=n, **vllm_kwargs(False)),
                        vllm_sampler.sample_stream(n=n, **vllm_kwargs(True)),
                        n,
                    )
                elif stream:
                    produce_iter = vllm_sampler.sample_one_stream(**vllm_kwargs(bool(thinking)))
                else:
                    produce_iter = vllm_sampler.sample_stream(n=n, **vllm_kwargs(bool(thinking)))
                sel_patch = {}  # frontend tracks the selection (vllm: sentinel)
            elif req.sampler_path:
                label = ckpt_label(req.sampler_path, None)
                # A loose ckpt has no local config.json, but tinker knows the base
                # model it serves against (resolve_base_model, one cached REST call),
                # so it renders LOCALLY exactly like a discovered LoRA — raw_meta /
                # token_logprobs / faithful renderer + thinking toggle. Native (not oai
                # /completions) for the same reason run_id is: tinker's oai /completions
                # serves the BASE model for a LoRA sampler path (tinker-feedback#125),
                # so it would silently show base output. No oai /chat fallback: if the
                # base can't be resolved tinker can't serve the ckpt either, so surface
                # that as an error rather than degrade to a worse render.
                base_model = await get_sampler().resolve_base_model(req.sampler_path)
                if not base_model:
                    raise ValueError(f"could not resolve the base model for {req.sampler_path}")

                def ckpt_iter(think: bool):
                    return get_sampler().sample_stream(
                        base_model=base_model, sampler_path=req.sampler_path,
                        renderer_name=req.renderer_name or select_renderer_name(base_model, None, think),
                        messages=native_msgs if think else native_off,
                        n=n, temperature=temperature, max_tokens=max_tokens,
                        top_p=top_p, logprobs=req.logprobs, think=think,
                        continue_tokens=req.continue_tokens,
                    )

                if both:
                    total = 2 * n
                    produce_iter = _dual(ckpt_iter(False), ckpt_iter(True), n)
                else:
                    produce_iter = ckpt_iter(bool(thinking))
                sel_patch = {}
            elif req.base_model:
                # Raw base model through tinker (no LoRA checkpoint).
                # to_thread: a capabilities/scan cache miss does REST sweeps +
                # a filesystem walk — sync here would block the event loop (and
                # trip the tinker SDK's sync-from-async guard).
                caps = await asyncio.to_thread(discovery.get_capabilities)
                if caps.get("available") and req.base_model not in discovery._supported_base_set(caps):
                    raise ValueError(f"tinker does not currently serve {req.base_model}")
                label = req.base_model

                def base_iter(think: bool):
                    return get_sampler().sample_stream(
                        base_model=req.base_model, sampler_path=None,
                        renderer_name=req.renderer_name or select_renderer_name(req.base_model, None, think),
                        messages=native_msgs if think else native_off,
                        n=n, temperature=temperature,
                        max_tokens=max_tokens, top_p=top_p,
                        logprobs=req.logprobs, think=think,
                        continue_tokens=req.continue_tokens,
                    )

                # Base models ALWAYS sample native (never the oai stream): the
                # /completions path drops renderer.parse_response + raw_meta +
                # token_logprobs (see the `stream` note above). `stream` is already
                # False here (req.base_model is set), so there's no streaming arm.
                if both:
                    total = 2 * n
                    produce_iter = _dual(base_iter(False), base_iter(True), n)
                else:
                    produce_iter = base_iter(bool(thinking))
                sel_patch = {}  # frontend tracks the base-model selection (sentinel)
            else:
                if not req.run_id:
                    raise ValueError("one of run_id / base_model / sampler_path / openrouter_model / vllm_model is required")
                run = await asyncio.to_thread(discovery.find_run, req.run_id)
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
                        renderer_name=req.renderer_name
                        or select_renderer_name(run.base_model, run.renderer_name, think),
                        messages=native_msgs if think else native_off,
                        n=n,
                        temperature=temperature, max_tokens=max_tokens, top_p=top_p,
                        logprobs=req.logprobs, think=think,
                        continue_tokens=req.continue_tokens,
                    )

                if both:
                    total = 2 * n
                    produce_iter = _dual(run_iter(False), run_iter(True), n)
                elif stream:
                    renderer_name = select_renderer_name(run.base_model, run.renderer_name, thinking)
                    _, prompt_text, stop = await get_sampler().render(
                        run.base_model, renderer_name, native_msgs, think=bool(thinking)
                    )
                    produce_iter = tinker_oai.completions_stream(
                        model=ckpt.sampler_path, prompt=prompt_text, stop=stop,
                        temperature=temperature, max_tokens=max_tokens, top_p=top_p,
                    )
                else:
                    produce_iter = run_iter(bool(thinking))
                sel_patch = {"run_id": run.id, "checkpoint": ckpt.name}
        except Exception as e:
            # pre-start failure (unknown/unsampleable run, bad checkpoint): surface
            # on BOTH the caller stream and the bus so the browser panel shows it.
            if req.broadcast:
                await BUS.broadcast("chat_error", {"chat_id": None, "panel": req.panel, "error": str(e), "client_token": req.client_token})
            yield {"event": "error", "data": json.dumps({"error": str(e)})}
            return

        # ── chat lifecycle: atomic id + running, reflect into state ──────────
        # Per-panel: `panel` routes this panel's selection + thread-system mirror
        # into panels[panel]. Sampling params are GLOBAL (shared
        # across panels) — set at the top level, no per-panel author race — and
        # only a "global"-scope chat (the browser) writes them; a "call"-scope
        # chat (CLI probe) samples with them but leaves the shared state alone.
        # commit=false empties the patch entirely (pure read — see
        # ChatRequest.commit): panel-routing the probed selection is how a
        # `tinkpg probe` used to rebind the panel the human had open, which a
        # browser tab then adopted into the SAVED layout (review 2026-08-12).
        # With broadcast=false too, even the lifecycle stays off the bus:
        # chat_id allocates silently, `running` never flips, no fanout — and
        # the matching chat_end in _fire is skipped (the _inflight pairing).
        bus_silent = not req.commit and not req.broadcast
        state_patch: dict = {}
        if req.commit:
            state_patch = {
                "panel": req.panel,
                "messages": msgs,
                # The workspace this chat belongs to. Sent by the browser (which knows
                # its own `?c=`); absent for a CLI fire, which means "whatever workspace
                # the bus is on". Stamped into the bus with the rest of the chat's
                # selection so the bus never claims one workspace while showing another
                # — see web/src/lib/bus-scope.ts.
                **({"workspace_id": req.workspace_id} if req.workspace_id else {}),
                # Panel-routed: the RESOLVED thread part, so a CLI new-thread send
                # updates the panel's thread mirror and an inheriting send is a no-op
                # write-back (thread state, not a sampling param — both scopes).
                "thread_system_prompt": thread_system,
                **sel_patch,
            }
            if req.params_scope != "call":
                # system_prompt is deliberately NOT echoed back: the browser (the only
                # global-scope client) maintains state.system_prompt/system_enabled via
                # /api/state, and a chat carries only the EFFECTIVE part ("" when the
                # prompt is muted) — echoing that would clobber a kept-but-muted prompt.
                state_patch.update({
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "n_samples": n,
                    "thinking": thinking,
                    "top_p": top_p,
                })
        chat_id = await (BUS.alloc_chat_id() if bus_silent else BUS.chat_begin(**state_patch))
        # Stamp every broadcast with the workspace this chat belongs to. The browser's
        # external-fold hook folds a chat_done onto a panel id ONLY if this id matches
        # the workspace it currently has open; panel ids are re-minted across
        # workspaces on a shared, process-wide bus, so without this a chat generated
        # for one workspace grafts onto a reused panel of another. Prefer the REQUEST's
        # id over the bus's: with two browser tabs the bus can already describe a
        # different workspace by the time this chat ends. None when the caller didn't
        # say and no workspace is open (CLI-only / legacy) — the browser folds those
        # (lockstep). Read synchronously after chat_begin — no await, so it can't drift.
        # A placement chat pins it to `fold_ws` (resolved at request time): the fold
        # target and the terminal stamp must be the same workspace.
        conv_id = fold_ws or req.workspace_id or BUS.state.workspace_id

        # ── stream samples, with EXACTLY ONE terminal event on every exit path ──
        # Three ways this chat can end — done / producer error / cancelled (client
        # disconnect OR the cancel endpoint) — must each fire one chat_end + one
        # terminal broadcast, or `running` sticks true forever and every busy-surface
        # (spinner, composer lock, Stop button) wedges. `_terminal()` is that single
        # exit, idempotent via `terminated`. The guarantee window opens HERE, right
        # after chat_begin bumped _inflight: everything that awaits from now on —
        # including the chat_start broadcast — runs inside the try below, so a
        # cancellation landing on any of those awaits still reaches the finally.
        produced: dict[int, str] = {}
        incorporated: dict[int, bool] = {}  # did the backend already fold the prefill in?
        samples: dict[int, dict] = {}  # full completed items, for the terminal fold
        # Fold outcome for the CALLER stream: {folded, fold_rev} or {fold_error}.
        # Filled by _fire (which _terminal awaits to completion) before the final
        # done/error SSE events yield, so the direct-stream consumer — the
        # headless CLI, the one that can't read the bus or the server log — can
        # tell "persisted" from "silently lost".
        fold_outcome: dict = {}
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
                event, err_msg = "chat_error", error
            elif produced:
                event, err_msg = "chat_done", None
            elif cancelled:
                event, err_msg = "chat_error", "cancelled"
            else:
                event, err_msg = "chat_done", None
            # The transcript echo commit that used to happen here retired with
            # P3: the fold below IS the multi-turn memory (the workspace tree),
            # and the CLI reads it over /api/workspaces.
            async def _apply_fold() -> "tuple[list[dict] | None, int | None, str | None]":
                """Fold every completed sample under `parent_node` — ONE
                `add_nodes` op through the SAME locked apply + bus fan-out every
                client mutation uses (store.apply_ops → `_broadcast_ops`), so a
                mirror replays the fold like any other batch: light nodes + blobs
                in one write, rev++, one `ops` event. Returns (manifest, rev,
                None), or (None, None, reason) when nothing could be persisted —
                loud, never raise: the terminal MUST still fire or `running`
                sticks. The reason reaches the CALLER STREAM (`fold_error` on the
                done/error SSE event), not just the server log — a headless CLI
                is exactly the consumer that can't read the log."""
                nodes, manifest = _build_fold_nodes(
                    msgs, samples, incorporated, scope, thinking, n, req.parent_node
                )
                op = {"op": "add_nodes", "panel": req.panel, "nodes": nodes, "select": True}
                out = await run_in_threadpool(workspace_store.apply_ops, fold_ws, [op])
                if out is None:
                    reason = f"workspace {fold_ws} vanished mid-chat"
                    log.warning(
                        "chat %s: fold dropped — %s (%d sample(s) not persisted)",
                        chat_id, reason, len(samples),
                    )
                    return None, None, reason
                if "rejected" in out:
                    # Near-unreachable while the placement registry holds (deletes
                    # of the parent's subtree 409), but a workspace-level
                    # replace/pack-apply can still pull the tree out from under us.
                    reason = f"fold rejected by the op layer: {out['rejected'].get('error')}"
                    log.warning(
                        "chat %s: %s — %d sample(s) not persisted",
                        chat_id, reason, len(samples),
                    )
                    return None, None, reason
                await _broadcast_ops(fold_ws, out)
                return manifest, out["rev"], None

            async def _fire() -> None:
                # Ordering guarantee (§4.3): the fold's `ops` event goes out — and
                # its write lands — BEFORE chat_end releases `running` and before
                # the chat_done/chat_error broadcast. Runs on error/cancel
                # terminals too: ≥1 completed sample is real data and folds.
                folded: "list[dict] | None" = None
                fold_rev: "int | None" = None
                if placement is not None and samples:
                    try:
                        folded, fold_rev, fold_err = await _apply_fold()
                    except Exception as e:
                        fold_err = f"fold failed: {type(e).__name__}: {e}"
                        log.exception(
                            "chat %s: fold failed — %d sample(s) not persisted",
                            chat_id, len(samples),
                        )
                    if folded is not None:
                        fold_outcome["folded"] = folded
                        fold_outcome["fold_rev"] = fold_rev
                    else:
                        fold_outcome["fold_error"] = fold_err or "fold produced no manifest"
                _release_placement()
                # No state patch rides the terminal anymore: the fold above IS
                # the durable record, and retiring the echo commit retired the
                # cross-workspace chimera class its origin gate (8342e08)
                # existed for. A bus-silent chat (probe) never began on the bus,
                # so it must not end there either — chat_end's _inflight
                # decrement would release a concurrent chat's `running` early.
                if not bus_silent:
                    await BUS.chat_end(event)
                if req.broadcast:
                    # workspace_id scopes the browser's external fold (#onExternalDone):
                    # every terminal flavour — done / error / cancelled — carries the stamp.
                    payload = {"chat_id": chat_id, "panel": req.panel, "client_token": req.client_token,
                               "workspace_id": conv_id,
                               # the resolved thread part — the browser's foreign-fold
                               # reconcile stamps it onto the thread's root node
                               "thread_system_prompt": thread_system}
                    if folded is not None:
                        # The bucket→server-id seam: which server-minted node each
                        # bucket sample became, so the browser seeds its blob cache
                        # without positional guessing (error samples shift positions).
                        payload["folded"] = folded
                        payload["fold_rev"] = fold_rev
                    if err_msg is not None:
                        payload["error"] = err_msg
                    await BUS.broadcast(event, payload)

            # Decoupled task + asyncio.shield, NOT an inline await or an anyio
            # shielded scope: a disconnect can cancel gen() while _terminal is
            # already mid-flight (suspended on the contended bus lock) — `terminated`
            # is True by then so the finally skips, and the CancelledError is
            # injected at the CURRENT suspension point, which no in-task cancel
            # scope can prevent (anyio shields only block anyio-scope cancellation;
            # a native task.cancel() pierces them). Running the work in its own task
            # means the injection hits the AWAITER: the terminal always runs to
            # completion, and the cancellation still re-raises here (never
            # swallowed). _TERMINAL_TASKS keeps a strong ref — the loop holds only
            # weak refs, and after the awaiter is torn down nothing else would.
            t = asyncio.create_task(_fire())
            _TERMINAL_TASKS.add(t)
            t.add_done_callback(_TERMINAL_TASKS.discard)
            await asyncio.shield(t)

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
            if req.broadcast:
                await BUS.broadcast(
                    "chat_start",
                    {"chat_id": chat_id, "panel": req.panel, "n": total, "label": label,
                     "client_token": req.client_token, "workspace_id": conv_id,
                     "thread_system_prompt": thread_system},
                )
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
                    samples[item["sample_index"]] = item
                yield {"event": "message", "data": json.dumps(item)}
                if req.broadcast:
                    await BUS.broadcast("sample", {"chat_id": chat_id, "panel": req.panel, **item})
            if prod_error is not None:
                await _terminal(error=prod_error)
                yield {"event": "error", "data": json.dumps({"error": prod_error, **fold_outcome})}
            else:
                await _terminal(cancelled=inflight.cancelled)
                yield {"event": "done", "data": json.dumps(fold_outcome)}
        finally:
            _INFLIGHT.pop(chat_id, None)
            if not worker.done():
                worker.cancel()  # client gone / endpoint cancel → stop the producer
            # A client disconnect cancels gen() mid-await (q.get, a broadcast, even
            # the chat_start broadcast): the CancelledError (BaseException) /
            # GeneratorExit skips both _terminal calls above. Fire it here; _terminal
            # shields its own awaits (BUS lock, broadcast) so they survive the pending
            # cancellation instead of being re-cancelled at the first checkpoint; the
            # original cancellation re-raises after the scope exits (never swallowed).
            if not terminated:
                await _terminal(cancelled=True)

    async def gen():
        """_gen_inner plus the placement lifecycle: register (validate the parent
        exists, under the workspaces flock — serialized with every delete op's
        guard) before any sampling, release on EVERY exit. A wrapper rather than
        code inside _gen_inner so the release backstop is a single finally that
        also covers cancellation landing anywhere in the inner body."""
        nonlocal placement
        if req.parent_node is not None:
            assert fold_ws is not None  # route body 400s otherwise
            reg = asyncio.ensure_future(run_in_threadpool(
                workspace_store.register_chat_placement, fold_ws, req.panel, req.parent_node
            ))
            try:
                reg_err = await asyncio.shield(reg)
            except asyncio.CancelledError:
                # Cancelled mid-registration (client gone between POST and first
                # read): the threadpool call still completes — undo whatever it
                # registered, or the placement leaks and 409s every delete of
                # that subtree until restart.
                def _undo(t: asyncio.Task) -> None:
                    if not t.cancelled() and t.exception() is None and t.result() is None:
                        inflight.release(fold_ws, req.panel, req.parent_node)
                reg.add_done_callback(_undo)
                raise
            if reg_err is not None:
                # Same surface as any other pre-start failure: the caller stream
                # AND the bus (the user turn is already persisted by the writer's
                # own op — §4.3's point — so nothing is lost here).
                if req.broadcast:
                    await BUS.broadcast("chat_error", {
                        "chat_id": None, "panel": req.panel, "error": reg_err,
                        "client_token": req.client_token,
                    })
                yield {"event": "error", "data": json.dumps({"error": reg_err})}
                return
            placement = (fold_ws, req.panel, req.parent_node)
        inner = _gen_inner()
        try:
            async for ev in inner:
                yield ev
        finally:
            # Drive the inner generator's own finally (the guaranteed terminal)
            # deterministically — an abandoned async generator only gets closed
            # by GC. On normal exhaustion this is a no-op.
            await inner.aclose()
            _release_placement()

    if req.detached:
        # Fire-and-forget: drive gen() to completion in the background, discarding
        # the client-facing events (everything the browser needs is on the bus).
        # The POST returns NOW, so the browser never holds this stream — its
        # per-host connection budget stays free for the other panels. gen()'s own
        # try/finally still guarantees exactly one terminal, and Stop still reaches
        # this chat through the cancel endpoint (it cancels the producer worker,
        # driving the same terminal a disconnect would).
        async def _drive() -> None:
            async for _ in gen():
                pass

        task = asyncio.create_task(_drive())
        _DETACHED_TASKS.add(task)
        task.add_done_callback(_DETACHED_TASKS.discard)
        return {"status": "started"}

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
