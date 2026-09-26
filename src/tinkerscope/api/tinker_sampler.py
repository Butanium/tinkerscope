"""Remote sampling from Tinker checkpoints — direct tinker SDK, no latteries.

This is the thin path validated end-to-end against a real checkpoint:
ServiceClient → create_sampling_client(model_path=sampler_path, base_model=…)
→ renderer.build_generation_prompt → sample_async → renderer.parse_response.

Replaces latteries.TinkerCaller (which dragged torch+streamlit+wandb+datasets
for a ~40-line wrapper). Renderers come from tinker_cookbook.

Per-sample streaming: a chat request for n samples fans out n single-sample
`sample_async` calls and yields each as it completes (mirrors the playground's
progressive fill + response-distribution chart). The renderer cache is keyed
by (base_model, renderer_name), so toggling thinking mid-session picks the
right renderer without the latteries cache-clear workaround.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import re
from typing import Any, AsyncIterator

from .raw_view import format_request_response

log = logging.getLogger("tinkerscope.tinker_sampler")

# ---------------------------------------------------------------------------
# Renderer selection (faithful to training, with a working thinking toggle)
# ---------------------------------------------------------------------------
def _recommended(base_model: str) -> list[str]:
    from tinker_cookbook.model_info import get_recommended_renderer_names

    try:
        return list(get_recommended_renderer_names(base_model))
    except Exception:
        return []


def _thinking_pair(base_model: str | None) -> tuple[str, str] | None:
    """If this family exposes a *binary* thinking toggle, return its
    (thinking_on, thinking_off) renderer names; else None.

    tinker_cookbook names the pair two opposite ways, depending on whether the
    family's default renderer thinks or not:
      - default thinks, opt-OUT variant named ``*_disable_thinking``
        (Qwen3/3.5, Kimi K2.5/2.6, Nemotron3): on = recs[0], off = disable variant.
      - default is silent, opt-IN variant named ``*_thinking``
        (DeepSeek-V3.1): off = recs[0], on = the ``_thinking`` variant.
    Families with non-binary reasoning levels (gpt_oss ``*_reasoning``,
    nemotron3 ``*_low/medium_thinking``) aren't a clean toggle — those still
    surface their disable variant where one exists, otherwise no toggle.
    """
    recs = _recommended(base_model) if base_model else []
    disable = next((r for r in recs if "disable_thinking" in r), None)
    if disable:
        return recs[0], disable
    on = next((r for r in recs if r.endswith("_thinking")), None)
    if on and on != recs[0]:
        return on, recs[0]
    return None


def supports_thinking(base_model: str | None) -> bool:
    """A base model supports the thinking toggle if its family exposes a binary
    thinking/non-thinking renderer PAIR (either naming convention), OR its renderer
    gates thinking with a continuous effort directive (tml_v0 / Inkling — the toggle
    maps to effort 0.9/0.0 in _build_generation_prompt), not a renderer-name variant."""
    if _thinking_pair(base_model) is not None:
        return True
    return any(r.startswith("tml") for r in (_recommended(base_model) if base_model else []))


def select_renderer_name(
    base_model: str, config_renderer: str | None, thinking: bool
) -> str:
    """Pick the renderer for inference.

    - If the family exposes a binary thinking toggle, honor it. The toggle
      overrides the training renderer's on/off choice (see _thinking_pair), so
      `thinking=False` on a DeepSeek-V3.1 run renders `<｜Assistant｜></think>`
      and `thinking=True` renders `<｜Assistant｜><think>`.
    - Otherwise stay faithful to the run's training renderer, then the family's
      first recommendation, then role_colon.
    """
    pair = _thinking_pair(base_model)
    if pair:
        on, off = pair
        return on if thinking else off
    recs = _recommended(base_model)
    return config_renderer or (recs[0] if recs else "role_colon")


# ---------------------------------------------------------------------------
# Thinking-block extraction (content may be a string with <think> tags or a
# list of {type: thinking|text} blocks, depending on renderer)
# ---------------------------------------------------------------------------
_THINK_RE = re.compile(r"<think>(.*?)(?:</think>|$)", re.DOTALL)


def _split_think_string(content: str) -> tuple[str, str | None]:
    if "<think>" not in content:
        return content, None
    think = "\n\n".join(m.group(1).strip() for m in _THINK_RE.finditer(content))
    text = _THINK_RE.sub("", content).strip()
    return text, (think or None)


def _append_to_prompt(model_input: Any, tokenizer: Any, prefill: str) -> Any:
    """Append the user's prefill text to the NORMAL generation prompt.

    i.e. build the prompt the renderer would send for a fresh assistant turn —
    which keeps each family's own opener (DeepSeek / Kimi K2.5-2.6 / Qwen3.5
    auto-open ``<think>`` in thinking mode; Qwen3 opens nothing) — then tack the
    prefill tokens on the end so the model EXTENDS it. Simpler and more faithful
    than the renderer's ``prefill=`` arg, which some families (Kimi) use to
    *replace* their auto-``<think>`` rather than add to it (so a prefill would
    silently drop the thinking opener)."""
    import tinker

    # Families that auto-open ``<think>`` (DeepSeek / Kimi / Qwen3.5) already end
    # the prompt with it; a reconstructed prefill (Continue / edited CoT) also
    # carries a leading ``<think>``. Drop the redundant one so we don't emit
    # ``<think><think>``. The check is on the actual prompt suffix, not the family
    # name, so it's correct for every renderer (Qwen3, which opens nothing, keeps
    # the tag its prefill needs). Also makes a hand-typed composer prefill forgiving
    # on auto-think runs.
    if re.match(r"\s*<think>", prefill) and tokenizer.decode(model_input.to_ints()).rstrip().endswith("<think>"):
        prefill = re.sub(r"^\s*<think>\s*", "", prefill, count=1)
    ids = tokenizer.encode(prefill, add_special_tokens=False)
    return tinker.ModelInput(chunks=[*model_input.chunks, tinker.types.EncodedTextChunk(tokens=ids)])


def _assistant_region_ids(renderer: Any, non_prefill: list[dict], full_ids: list[int]) -> list[int] | None:
    """Token ids of the assistant-authored region of a *prefilled* prompt: the
    family's auto-``<think>`` (if any) PLUS the user prefill — i.e. everything
    after the bare assistant role header. Returns None if the header can't be
    located (callers then parse the completion alone).

    Why: ``parse_response`` runs on the sampled completion tokens only, but the
    prefill lives in the prompt. Feeding it ``region + completion`` lets the
    renderer see the FULL turn so ``<think>…</think>`` splits into reasoning/text
    correctly. Grounded against Qwen3 / Kimi-K2.x / DeepSeek-V3.1 (each munges
    the prefill differently — Qwen adds no auto-tag, Kimi's custom prefill
    suppresses its default ``<think>``, DeepSeek always prepends ``<think>`` —
    but the bare-header anchor captures the right region in all three)."""
    from tinker_cookbook.renderers.base import RenderContext

    last_user = max((i for i, m in enumerate(non_prefill) if m["role"] == "user"), default=-1)
    ctx = RenderContext(
        idx=len(non_prefill),
        is_last=True,
        prev_message=non_prefill[-1] if non_prefill else None,
        last_user_index=last_user,
    )
    header = list(renderer._get_generation_suffix("assistant", ctx))
    if not header:
        return None
    for i in range(len(full_ids) - len(header), -1, -1):
        if full_ids[i:i + len(header)] == header:
            return full_ids[i + len(header):]
    return None


def _tml_continue(renderer: Any, tokenizer: Any, non_prefill: list[dict], prefill: str, think: bool) -> tuple[Any, list[int]]:
    """Build (model_input, region_ids) for a tml_v0 (Inkling) prefill/continue.

    tml_v0 renders whole conversations only — no per-message assistant header to
    splice a raw prefill after (``render_message`` raises; ``build_generation_prompt``
    ends at the last ``<|end_message|>`` and the model emits its own
    ``<|message_model|>``). So the generic append path (_append_to_prompt +
    _assistant_region_ids) both misplaces the prefill AND crashes here.

    Instead render the FULL prefilled turn the way the model was trained — reasoning
    and text in their own ``<|content_thinking|>`` / ``<|content_text|>`` blocks — via
    the renderer's own ``build_supervised_examples``, drop the trailing message-close
    pair (``<|end_message|><|content_model_end_sampling|>``) to REOPEN the last block
    for extension, and diff against the plain generation prompt to recover the
    assistant region for parse_response (fed as ``region_ids + completion``)."""
    import tinker

    tml = tokenizer.tml_tokenizer
    end_message = tml.encode_special("end_message")
    end_sampling = tml.encode_special("content_model_end_sampling")

    effort = _THINK_EFFORT if think else _NOTHINK_EFFORT
    text, reasoning = _split_think_string(prefill)
    if reasoning:
        content: Any = [{"type": "thinking", "thinking": reasoning}]
        if text:
            content.append({"type": "text", "text": text})
    else:
        content = text
    amsg = {"role": "assistant", "content": content}

    examples = renderer.build_supervised_examples(non_prefill + [amsg], effort=effort)
    assert len(examples) == 1, f"tml_v0 continue expected 1 SFT example, got {len(examples)}"
    full_ids = examples[0][0].to_ints()
    # The rendered turn always closes with <|end_message|><|content_model_end_sampling|>;
    # dropping exactly that pair reopens the last content block for the model to extend.
    assert full_ids[-2] == end_message and full_ids[-1] == end_sampling, (
        f"tml_v0 render did not end with [<|end_message|>, <|content_model_end_sampling|>]: "
        f"got [...{full_ids[-2]}, {full_ids[-1]}]"
    )
    trunc = full_ids[:-2]

    gen_ids = renderer.build_generation_prompt(non_prefill, effort=effort).to_ints()
    assert trunc[:len(gen_ids)] == gen_ids, "generation prompt is not a token-prefix of the SFT render"
    region_ids = trunc[len(gen_ids):]
    model_input = tinker.ModelInput(chunks=[tinker.types.EncodedTextChunk(tokens=trunc)])
    return model_input, region_ids


def _continue_prompt(
    renderer: Any, renderer_name: str, tokenizer: Any, non_prefill: list[dict], prefill: str, think: bool
) -> tuple[Any, list[int] | None]:
    """Build (model_input, region_ids) for a prefilled (continue) assistant turn.

    tml_v0 (Inkling) needs the prefill rendered as part of the whole conversation
    (_tml_continue); every other renderer's generation prompt ends with the assistant
    header, so append the raw prefill and locate the region by its generation suffix.
    region_ids is None when it can't be located (caller parses the completion alone)."""
    if renderer_name.startswith("tml"):
        return _tml_continue(renderer, tokenizer, non_prefill, prefill, think)
    model_input = _build_generation_prompt(renderer, non_prefill, think)
    model_input = _append_to_prompt(model_input, tokenizer, prefill)
    return model_input, _assistant_region_ids(renderer, non_prefill, model_input.to_ints())


def _ids_to_tokens(tokenizer: Any, ids: list[int]) -> list[str]:
    """Per-token strings for the raw-meta view. HF tokenizers expose
    convert_ids_to_tokens (the raw subword pieces, with the family's space marker
    Ġ / ▁ — what makes a mis-encoded special token visible). Tinker's tml_renderers
    adapter (Inkling) has no such method, so fall back to decoding each id alone: a
    faithful per-token rendering, just without the raw piece markers."""
    conv = getattr(tokenizer, "convert_ids_to_tokens", None)
    if conv is not None:
        return conv(ids)
    return [tokenizer.decode([int(i)]) for i in ids]


def _finish_reason(seq: Any, termination: Any) -> str:
    """"length" iff the sample was cut by max_tokens, else "stop".

    The service's own `SampledSequence.stop_reason` answers that. The renderer's
    parse termination is only the fallback, and must not be tested for truth: the
    cookbook returns a `ParseTermination` StrEnum (always truthy), so a
    `"stop" if termination` test labelled every truncated sample "stop"."""
    stop_reason = getattr(seq, "stop_reason", None)
    if stop_reason in ("length", "stop"):
        return stop_reason
    is_clean = getattr(termination, "is_clean", termination)  # older cookbooks: a bool
    return "stop" if is_clean is True else "length"


def _normalize_content(content: Any) -> tuple[str, str | None]:
    """Return (text, reasoning) from a parsed renderer response."""
    reasoning: str | None = None
    if isinstance(content, list):
        texts, thinks = [], []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "thinking":
                    thinks.append(block.get("thinking", ""))
                elif block.get("type") == "text":
                    texts.append(block.get("text", ""))
        content = "\n\n".join(texts)
        reasoning = "\n\n".join(t for t in thinks if t).strip() or None
    if isinstance(content, str):
        text, think = _split_think_string(content)
        if think:
            reasoning = (reasoning + "\n\n" + think) if reasoning else think
        content = text
    return content, reasoning


def _to_render_msg(m: dict) -> dict:
    """Build one renderer history message (the INVERSE of _normalize_content's input split).

    An assistant turn carrying separated `reasoning` becomes STRUCTURED content
    ([{type:thinking}, {type:text}]) so the renderer applies its OWN history policy
    (strip_thinking_from_history / preserve) rather than us pre-deciding. It must be
    structured, NOT an inlined `<think>` string: the renderers pass string content through
    verbatim (no stripping), so a `<think>` string would force-keep the CoT and collide with
    the family's auto-opened think tag. Turns without `reasoning` stay plain `{role, content}`
    — byte-identical to the old behavior, hence zero change for strip-from-history renderers
    (verified) and correct preservation for preserve renderers (e.g. kimi_k26_preserve_thinking).
    The trailing prefill turn is handled separately (raw string), never routed here."""
    content = m.get("content") or ""
    reasoning = (m.get("reasoning") or "").strip()
    if m.get("role") != "assistant" or not reasoning:
        return {"role": m["role"], "content": content}
    parts: list[dict] = [{"type": "thinking", "thinking": reasoning}]
    if content:
        parts.append({"type": "text", "text": content})
    return {"role": m["role"], "content": parts}


def _strip_trailing_stop(tokenizer: Any, ids: list[int], stops: list) -> list[int]:
    """Drop trailing tokens that ARE a stop sequence. A stop-finished sample's
    stored stream includes its end-of-turn token (verified live 2026-08-06 on
    DeepSeek-V3.1: every stop-finished stream ended with <|end_of_sentence|>), so
    replaying a full stream verbatim would CLOSE the turn and make the model start
    a new one — the opposite of Continue. Mid-stream loom cuts never end with a
    stop, so this is a no-op for them. Handles str stops (decoded-tail match, the
    normal case) and int stops (direct id match) — SamplingParams accepts both."""
    ids = list(ids)
    id_stops = {s for s in stops if isinstance(s, int)}
    str_stops = [s for s in stops if isinstance(s, str) and s]
    changed = True
    while changed and ids:
        changed = False
        if ids[-1] in id_stops:
            ids.pop()
            changed = True
            continue
        tail = tokenizer.decode(ids[-4:])  # stops are 1–2 tokens; 4 is plenty
        for s in str_stops:
            if tail.endswith(s):
                need = len(s)
                while ids and need > 0:
                    need -= len(tokenizer.decode([ids[-1]]))
                    ids.pop()
                changed = True
                break
    return ids


# tml_v0 (Inkling) gates thinking with a continuous `effort` DIRECTIVE injected into
# the prompt, NOT an on/off renderer variant — so `select_renderer_name` picks the same
# renderer both ways and thinking is set HERE instead. Our binary toggle maps to effort:
# on = tml_v0's trained default (0.9 "high"), off = 0.0 (no thinking). Renderers whose
# build_generation_prompt takes no `effort` bake thinking into the renderer name already,
# so they ignore this and get a plain call.
_THINK_EFFORT = 0.9
_NOTHINK_EFFORT = 0.0


def _build_generation_prompt(renderer: Any, messages: list[dict], think: bool) -> Any:
    """renderer.build_generation_prompt, threading thinking `effort` for renderers that
    accept it (tml_v0). A plain call for every other renderer."""
    try:
        accepts_effort = "effort" in inspect.signature(renderer.build_generation_prompt).parameters
    except (TypeError, ValueError):
        accepts_effort = False
    if accepts_effort:
        return renderer.build_generation_prompt(
            messages, effort=_THINK_EFFORT if think else _NOTHINK_EFFORT
        )
    return renderer.build_generation_prompt(messages)


# ---------------------------------------------------------------------------
# Per-token logprobs (native sampling only)
# ---------------------------------------------------------------------------
TOPK_LOGPROBS = 5

# Bounds on every remote tinker call. The SDK retries on its own for up to ~2 h,
# so without these a wedged request kept a browser chat "running" and `tinkpg`
# silent that long. A sample's budget grows with max_tokens; the floor covers
# queueing on a busy service.
CLIENT_TIMEOUT_S = 120.0
SAMPLE_TIMEOUT_BASE_S = 180.0
SAMPLE_TIMEOUT_MIN_TOKENS_PER_S = 20.0


def _sample_timeout(max_tokens: int) -> float:
    return SAMPLE_TIMEOUT_BASE_S + max_tokens / SAMPLE_TIMEOUT_MIN_TOKENS_PER_S


async def _bounded(aw: Any, timeout: float, what: str) -> Any:
    """`await aw`, or a TimeoutError that says what timed out. An abandoned
    `to_thread` call keeps running in its thread; the caller just stops waiting."""
    try:
        return await asyncio.wait_for(aw, timeout)
    except TimeoutError:
        raise TimeoutError(
            f"no answer from tinker after {timeout:.0f}s while {what} (the service may be degraded)"
        ) from None


async def _token_logprobs(
    client: Any, model_input: Any, tokens: list[int], fallback_lps: Any, tokenizer: Any
) -> list[dict] | None:
    """Per-generated-token logprobs + top-K alternatives for one sample.

    The sampled tokens' own logprobs come back free on every SampleResponse
    (``seq.logprobs``), but tinker has NO top-k for *generated* tokens — its
    ``topk_prompt_logprobs`` covers prompt positions only. So we re-submit
    prompt+completion as a prefill (``max_tokens=1``) with
    ``include_prompt_logprobs + topk_prompt_logprobs`` and read positions
    [L, L+T): generated token t sits at full-sequence position L+t (same
    convention as tinker_cookbook's SDFT teacher top-K recovery; verified live —
    prefill lp matches the sampling call's own lp to ~1e-2). One extra
    prefill-only call per sample; `lp` is taken from THIS call so it's the same
    forward pass as `top`. On any failure, degrade to the sampling call's own
    per-token logprobs with no alternatives — never fail the sample over its
    diagnostics.

    Wire/persisted entry shape: {t, tid, lp, top?: [[text, tid, lp] × K]},
    `top` most-probable-first.
    """
    import tinker
    from tinker import types as tt

    L = model_input.length

    def _fallback() -> list[dict] | None:
        if fallback_lps is None:
            return None
        return [
            {"t": tokenizer.decode([tid]), "tid": int(tid), "lp": float(fallback_lps[t])}
            for t, tid in enumerate(tokens)
        ]

    try:
        full = tinker.ModelInput(
            chunks=[*model_input.chunks, tt.EncodedTextChunk(tokens=list(tokens))]
        )
        resp = await _bounded(client.sample_async(
            prompt=full,
            num_samples=1,
            sampling_params=tt.SamplingParams(max_tokens=1),
            include_prompt_logprobs=True,
            topk_prompt_logprobs=TOPK_LOGPROBS,
        ), SAMPLE_TIMEOUT_BASE_S, "scoring token logprobs")
        plp = resp.prompt_logprobs
        topk = resp.topk_prompt_logprobs
        out: list[dict] = []
        for t, tid in enumerate(tokens):
            pos = L + t
            lp = plp[pos] if plp is not None and pos < len(plp) else None
            if lp is None and fallback_lps is not None:
                lp = fallback_lps[t]
            entry: dict = {
                "t": tokenizer.decode([tid]),
                "tid": int(tid),
                "lp": float(lp) if lp is not None else None,
            }
            if topk is not None and pos < len(topk) and topk[pos]:
                entry["top"] = [
                    [tokenizer.decode([a]), int(a), float(b)]
                    for a, b in topk[pos][:TOPK_LOGPROBS]
                ]
            out.append(entry)
        return out
    except asyncio.CancelledError:
        raise
    except Exception:
        return _fallback()


# ---------------------------------------------------------------------------
# Sampler manager: caches ServiceClient, sampling clients, renderers, tokenizers
# ---------------------------------------------------------------------------
def _tinker_detail(e: Exception) -> str:
    """The human half of a tinker API error. Its exceptions carry the server's JSON
    body (`{'detail': '…'}`) and stringify as "Error code: 404 - {'detail': '…'}";
    the detail alone is what a person pasting a path needs to read."""
    body = getattr(e, "body", None)
    if isinstance(body, dict) and body.get("detail"):
        return str(body["detail"])
    m = re.search(r"\{'detail': (\"|')(.*)\1\}\s*$", str(e), re.S)
    return m.group(2) if m else str(e)


class SamplerManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._service_client: Any = None
        self._sampling_clients: dict[str, Any] = {}
        self._renderers: dict[tuple[str, str], Any] = {}
        self._tokenizers: dict[str, Any] = {}
        self._base_models: dict[str, str] = {}

    async def _service(self) -> Any:
        async with self._lock:
            if self._service_client is None:
                import tinker

                # Sync SDK constructor — run off the event loop (deadlock-safe).
                self._service_client = await _bounded(
                    asyncio.to_thread(tinker.ServiceClient), CLIENT_TIMEOUT_S, "connecting"
                )
            return self._service_client

    async def _sampling_client(self, base_model: str | None, sampler_path: str | None) -> Any:
        key = sampler_path or base_model
        async with self._lock:
            if key in self._sampling_clients:
                return self._sampling_clients[key]
        sc = await self._service()
        # create_sampling_client is sync — offload so it can't block the loop.
        # base_model may be None for a loose sampler_path: the server resolves it
        # (see probe_sampler_path), and the client samples fine either way.
        if sampler_path:
            create = functools.partial(sc.create_sampling_client, model_path=sampler_path, base_model=base_model)
        else:
            create = functools.partial(sc.create_sampling_client, base_model=base_model)
        client = await _bounded(
            asyncio.to_thread(create), CLIENT_TIMEOUT_S, f"creating a sampling client for {key}"
        )
        async with self._lock:
            self._sampling_clients[key] = client
        return client

    async def probe_sampler_path(self, sampler_path: str) -> dict:
        """Can tinker sample this loose sampler path, and against which base model?

        The base model is what lets a bare tinker:// URI (no local config.json)
        render LOCALLY like a discovered run — raw_meta / token_logprobs / faithful
        renderer + thinking toggle. Returns {available, base_model, error}; the
        error is tinker's own `detail` (400 for a malformed path, 404 for an unknown
        one — different messages to a person pasting a link), or the retired-base
        reason: a LoRA on a base tinker stopped serving resolves fine and then 400s
        "Sampling is not supported" on every sample. Only a success is cached, so
        a transient failure doesn't stick until restart."""
        async with self._lock:
            bm = self._base_models.get(sampler_path)
        if bm is None:
            try:
                client = await self._sampling_client(None, sampler_path)
                bm = await _bounded(client.get_base_model_async(), CLIENT_TIMEOUT_S, "resolving the base model")
            except Exception as e:
                return {"available": False, "base_model": None, "error": _tinker_detail(e)}
            async with self._lock:
                self._base_models[sampler_path] = bm
        from . import discovery

        caps = await asyncio.to_thread(discovery.get_capabilities)
        if caps.get("available") and bm not in discovery._supported_base_set(caps):
            return {"available": False, "base_model": bm,
                    "error": f"tinker does not currently serve sampling for {bm}"}
        return {"available": True, "base_model": bm, "error": None}

    def _tokenizer(self, base_model: str) -> Any:
        if base_model not in self._tokenizers:
            from tinker_cookbook.tokenizer_utils import get_tokenizer

            self._tokenizers[base_model] = get_tokenizer(base_model)
        return self._tokenizers[base_model]

    def _renderer(self, base_model: str, renderer_name: str) -> Any:
        key = (base_model, renderer_name)
        if key not in self._renderers:
            from tinker_cookbook import renderers as rmod

            self._renderers[key] = rmod.get_renderer(renderer_name, self._tokenizer(base_model))
        return self._renderers[key]

    async def render(
        self, base_model: str, renderer_name: str, messages: list[dict], think: bool = True
    ) -> tuple[Any, str, list]:
        """Build the generation prompt with the run's renderer (training-faithful).

        Returns (model_input, prompt_text, stop) where model_input feeds the native
        sampler and prompt_text feeds the oai /completions endpoint — same prompt,
        two backends. A trailing assistant message is a prefill the model extends
        (see _continue_prompt, per-renderer). `think` sets the thinking effort for
        renderers that take one (tml_v0).
        """
        renderer = await asyncio.to_thread(self._renderer, base_model, renderer_name)
        tokenizer = await asyncio.to_thread(self._tokenizer, base_model)
        # A trailing assistant turn is a PREFILL the model extends. Prior turns go
        # through _to_render_msg, which structures any carried reasoning so the renderer
        # applies its own thinking-history policy. _continue_prompt handles the
        # renderer-specific prefill splicing (region_ids is unused on this oai path).
        if messages and messages[-1].get("role") == "assistant":
            prefill = messages[-1].get("content") or ""
            non_prefill = [_to_render_msg(m) for m in messages[:-1]]
        else:
            prefill = None
            non_prefill = [_to_render_msg(m) for m in messages]
        if prefill:
            model_input, _ = _continue_prompt(renderer, renderer_name, tokenizer, non_prefill, prefill, think)
        else:
            model_input = _build_generation_prompt(renderer, non_prefill, think)
        stop = renderer.get_stop_sequences()
        prompt_text = tokenizer.decode(model_input.to_ints())
        return model_input, prompt_text, stop

    async def sample_stream(
        self,
        *,
        base_model: str,
        sampler_path: str | None,
        renderer_name: str,
        messages: list[dict],
        n: int,
        temperature: float,
        max_tokens: int,
        top_p: float | None = None,
        logprobs: bool = True,
        think: bool = True,
        continue_tokens: list[int] | None = None,
    ) -> AsyncIterator[dict]:
        """Yield one result dict per completed sample, as they finish.

        Each yielded dict: {sample_index, content, reasoning?, raw_text,
        raw_meta, finish_reason, token_logprobs?} — or {sample_index, error} on
        a per-sample failure. `raw_meta` is the tokenizer-debugging blob
        (prompt/completion tokens via convert_ids_to_tokens) shown in the
        raw-view dropdown. `token_logprobs` (default ON, see _token_logprobs)
        costs one extra prefill-only call per sample, awaited before the sample
        is yielded; pass logprobs=False to skip it. `think` sets the thinking
        effort for renderers that take one (tml_v0 / Inkling).

        `continue_tokens` (the LOOM): token ids appended VERBATIM after the
        rendered prompt — a stored sample's generated prefix plus a picked
        alternative, so the model continues from that exact token state with no
        re-tokenization drift. They are assistant-authored by construction
        (everything after the generation prompt is), so they extend region_ids
        exactly like a text prefill's region: parse_response sees the full turn
        and the caller must not re-prepend anything (prefill_incorporated).
        token_logprobs then cover the WHOLE stream (forced prefix + fresh
        continuation) from one teacher-forced pass — the prefix scores under its
        true context, the picked alternative under its recorded top-K value.
        Each sample is stamped with ``loom_cut`` (how many leading stream entries
        were forced) + ``loom_text`` (the forced prefix as frame-normalized
        display text) so the browser can mark the fork point.
        """
        import tinker
        from tinker import types as tt

        client = await self._sampling_client(base_model, sampler_path)
        # First-load of a tokenizer/renderer (transformers) is CPU-heavy; offload
        # so it doesn't stall the event loop for other in-flight requests.
        renderer = await asyncio.to_thread(self._renderer, base_model, renderer_name)
        tokenizer = await asyncio.to_thread(self._tokenizer, base_model)

        # Trailing assistant turn = PREFILL; prior turns through _to_render_msg so
        # carried reasoning is structured and the renderer applies its history policy.
        if messages and messages[-1].get("role") == "assistant":
            prefill = messages[-1].get("content") or ""
            non_prefill = [_to_render_msg(m) for m in messages[:-1]]
        else:
            prefill = None
            non_prefill = [_to_render_msg(m) for m in messages]

        # With a prefill, region_ids is the assistant-authored region: parsing
        # (region_ids + completion) makes prefilled thinking land in `reasoning`,
        # not raw `<think>` in `content`. _continue_prompt builds both, per renderer.
        if prefill:
            model_input, region_ids = _continue_prompt(
                renderer, renderer_name, tokenizer, non_prefill, prefill, think
            )
        else:
            model_input = _build_generation_prompt(renderer, non_prefill, think)
            region_ids = None
        # score_input = the prompt WITHOUT the continue tokens: _token_logprobs
        # reads positions after its prompt, so scoring against this base covers
        # the forced prefix too (see the docstring).
        score_input = model_input
        loom_text: str | None = None
        if continue_tokens:
            # A full-stream replay (Continue = the loom cut at the end) must not
            # carry the end-of-turn token — strip it so the model EXTENDS.
            continue_tokens = _strip_trailing_stop(
                tokenizer, list(continue_tokens), renderer.get_stop_sequences()
            )
        if continue_tokens:
            # The replayed prefix as display text, stamped on every sample so the
            # browser can tint it like a prefill ("this part was forced, not drawn
            # here"). Frame-normalized: auto-<think> families (DeepSeek/Kimi/
            # Qwen3.5) open the tag in the PROMPT, so the replayed stream starts
            # mid-think without it — prepend it so the text splits like an
            # authored prefill (web/src/lib/render.ts splitPrefill).
            loom_text = tokenizer.decode(list(continue_tokens))
            if tokenizer.decode(score_input.to_ints()).rstrip().endswith("<think>"):
                loom_text = "<think>" + loom_text
            model_input = tinker.ModelInput(
                chunks=[*model_input.chunks, tt.EncodedTextChunk(tokens=list(continue_tokens))]
            )
            region_ids = [*(region_ids or []), *continue_tokens]
        stop = renderer.get_stop_sequences()
        prompt_text = tokenizer.decode(model_input.to_ints())

        params = tt.SamplingParams(
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p if top_p is not None else 1.0,
            stop=stop,
        )

        # Shared across the n samples. The decoded prompt+completion lives in each
        # sample's `raw_text` (the main raw view); this dropdown is the
        # tokenizer-debugging view — prompt and completion as the tokenizer
        # actually split them (`convert_ids_to_tokens`), so a mis-encoded special
        # token or a wrong boundary shows up here even when the decoded string
        # reads fine. Deliberately unlike the OpenRouter raw view: the two
        # backends fail in different ways and are debugged differently.
        request_meta = {
            "base_model": base_model,
            "sampler_path": sampler_path,
            "renderer": renderer_name,
            "prompt_text": prompt_text,
            "prompt_tokens": _ids_to_tokens(tokenizer, model_input.to_ints()),
            "sampling_params": {
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p if top_p is not None else 1.0,
                "stop": stop,
            },
        }

        async def one(idx: int) -> dict:
            try:
                resp = await _bounded(
                    client.sample_async(prompt=model_input, num_samples=1, sampling_params=params),
                    _sample_timeout(max_tokens), "sampling",
                )
                seq = resp.sequences[0]
                to_parse = (region_ids + seq.tokens) if region_ids is not None else seq.tokens
                parsed, termination = renderer.parse_response(to_parse)
                content, reasoning = _normalize_content(parsed.get("content"))
                raw_text = prompt_text + tokenizer.decode(seq.tokens)
                finish_reason = _finish_reason(seq, termination)
                response_meta: dict = {}
                if reasoning:
                    response_meta["reasoning"] = reasoning
                response_meta["content"] = content
                response_meta["content_tokens"] = _ids_to_tokens(tokenizer, seq.tokens)
                response_meta["finish_reason"] = finish_reason
                response_meta["output_tokens"] = len(seq.tokens)
                item: dict = {
                    "sample_index": idx,
                    "content": content,
                    "raw_text": raw_text,
                    "raw_meta": format_request_response(request_meta, response_meta),
                    "finish_reason": finish_reason,
                }
                if reasoning:
                    item["reasoning"] = reasoning
                if region_ids is not None:
                    # content/reasoning already span prefill+completion → the
                    # client must NOT re-prepend the prefill.
                    item["prefill_incorporated"] = True
                if continue_tokens:
                    # Loom provenance: how many leading stream entries were FORCED
                    # (replayed prefix + picked alternative) and their display
                    # text — the fold copies both onto the minted node so the fork
                    # point survives reload.
                    item["loom_cut"] = len(continue_tokens)
                    item["loom_text"] = loom_text
                if logprobs:
                    if continue_tokens:
                        # No fallback lps: seq.logprobs covers only the fresh
                        # tokens, so it can't stand in for the full stream.
                        tlp = await _token_logprobs(
                            client, score_input, [*continue_tokens, *seq.tokens], None, tokenizer
                        )
                    else:
                        tlp = await _token_logprobs(
                            client, model_input, list(seq.tokens), seq.logprobs, tokenizer
                        )
                    if tlp:
                        item["token_logprobs"] = tlp
                return item
            except Exception as e:  # surface per-sample failure, keep the rest
                return {"sample_index": idx, "error": f"{type(e).__name__}: {e}"}

        tasks = [asyncio.create_task(one(i)) for i in range(n)]
        try:
            for fut in asyncio.as_completed(tasks):
                yield await fut
        finally:
            # Consumer gone (CLI Ctrl-C / browser tab closed): cancel in-flight
            # samples so we stop waiting on output nobody reads. The remote call runs
            # on: the SDK drives it on its own loop, so this only stops us listening.
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        async with self._lock:
            for client in self._sampling_clients.values():
                close = getattr(client, "close", None)
                if close:
                    try:
                        res = close()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass
            self._sampling_clients.clear()
            self._service_client = None


_sampler: SamplerManager | None = None


def get_sampler() -> SamplerManager:
    global _sampler
    if _sampler is None:
        _sampler = SamplerManager()
    return _sampler


async def close_sampler() -> None:
    global _sampler
    if _sampler is not None:
        await _sampler.close()
        _sampler = None
