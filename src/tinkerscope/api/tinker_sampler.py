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
import re
from typing import Any, AsyncIterator

# ---------------------------------------------------------------------------
# Renderer selection (faithful to training, with a working thinking toggle)
# ---------------------------------------------------------------------------
def _recommended(base_model: str) -> list[str]:
    from tinker_cookbook.model_info import get_recommended_renderer_names

    try:
        return list(get_recommended_renderer_names(base_model))
    except Exception:
        return []


def supports_thinking(base_model: str | None) -> bool:
    """A base model supports the thinking toggle iff its family exposes a
    disable_thinking renderer variant."""
    if not base_model:
        return False
    return any("disable_thinking" in r for r in _recommended(base_model))


def select_renderer_name(
    base_model: str, config_renderer: str | None, thinking: bool
) -> str:
    """Pick the renderer for inference.

    - If the model family has thinking/disable variants, honor the toggle
      (recs[0] is the thinking renderer; the disable_thinking variant is off).
    - Otherwise fall back to the run's training renderer (faithful), then to
      the family's first recommendation, then to role_colon.
    """
    recs = _recommended(base_model)
    disable = [r for r in recs if "disable_thinking" in r]
    if disable:  # thinking-capable family
        return recs[0] if thinking else disable[0]
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


# ---------------------------------------------------------------------------
# Sampler manager: caches ServiceClient, sampling clients, renderers, tokenizers
# ---------------------------------------------------------------------------
class SamplerManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._service_client: Any = None
        self._sampling_clients: dict[str, Any] = {}
        self._renderers: dict[tuple[str, str], Any] = {}
        self._tokenizers: dict[str, Any] = {}

    async def _service(self) -> Any:
        async with self._lock:
            if self._service_client is None:
                import tinker

                # Sync SDK constructor — run off the event loop (deadlock-safe).
                self._service_client = await asyncio.to_thread(tinker.ServiceClient)
            return self._service_client

    async def _sampling_client(self, base_model: str, sampler_path: str | None) -> Any:
        key = sampler_path or base_model
        async with self._lock:
            if key in self._sampling_clients:
                return self._sampling_clients[key]
        sc = await self._service()
        # create_sampling_client is sync — offload so it can't block the loop.
        if sampler_path:
            client = await asyncio.to_thread(
                lambda: sc.create_sampling_client(model_path=sampler_path, base_model=base_model)
            )
        else:
            client = await asyncio.to_thread(
                lambda: sc.create_sampling_client(base_model=base_model)
            )
        async with self._lock:
            self._sampling_clients[key] = client
        return client

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
        self, base_model: str, renderer_name: str, messages: list[dict]
    ) -> tuple[Any, str, list]:
        """Build the generation prompt with the run's renderer (training-faithful).

        Returns (model_input, prompt_text, stop) where model_input feeds the native
        sampler and prompt_text feeds the oai /completions endpoint — same prompt,
        two backends. A trailing assistant message is treated as a prefill.
        """
        renderer = await asyncio.to_thread(self._renderer, base_model, renderer_name)
        tokenizer = await asyncio.to_thread(self._tokenizer, base_model)
        rmsgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        if rmsgs and rmsgs[-1]["role"] == "assistant":
            prefill, non_prefill = rmsgs[-1]["content"], rmsgs[:-1]
        else:
            prefill, non_prefill = None, rmsgs
        model_input = renderer.build_generation_prompt(non_prefill, prefill=prefill)
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
    ) -> AsyncIterator[dict]:
        """Yield one result dict per completed sample, as they finish.

        Each yielded dict: {sample_index, content, reasoning?, raw_text,
        finish_reason} — or {sample_index, error} on a per-sample failure.
        """
        from tinker import types as tt

        client = await self._sampling_client(base_model, sampler_path)
        # First-load of a tokenizer/renderer (transformers) is CPU-heavy; offload
        # so it doesn't stall the event loop for other in-flight requests.
        renderer = await asyncio.to_thread(self._renderer, base_model, renderer_name)
        tokenizer = await asyncio.to_thread(self._tokenizer, base_model)

        rmsgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        if rmsgs and rmsgs[-1]["role"] == "assistant":
            prefill = rmsgs[-1]["content"]
            non_prefill = rmsgs[:-1]
        else:
            prefill = None
            non_prefill = rmsgs

        model_input = renderer.build_generation_prompt(non_prefill, prefill=prefill)
        stop = renderer.get_stop_sequences()
        prompt_text = tokenizer.decode(model_input.to_ints())

        params = tt.SamplingParams(
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p if top_p is not None else 1.0,
            stop=stop,
        )

        async def one(idx: int) -> dict:
            try:
                resp = await client.sample_async(
                    prompt=model_input, num_samples=1, sampling_params=params
                )
                seq = resp.sequences[0]
                parsed, reached_stop = renderer.parse_response(seq.tokens)
                content, reasoning = _normalize_content(parsed.get("content"))
                raw_text = prompt_text + tokenizer.decode(seq.tokens)
                item: dict = {
                    "sample_index": idx,
                    "content": content,
                    "raw_text": raw_text,
                    "finish_reason": "stop" if reached_stop else "length",
                }
                if reasoning:
                    item["reasoning"] = reasoning
                return item
            except Exception as e:  # surface per-sample failure, keep the rest
                return {"sample_index": idx, "error": f"{type(e).__name__}: {e}"}

        tasks = [asyncio.create_task(one(i)) for i in range(n)]
        try:
            for fut in asyncio.as_completed(tasks):
                yield await fut
        finally:
            # Consumer gone (CLI Ctrl-C / browser tab closed): cancel in-flight
            # samples so we stop paying remote tinker tokens for output nobody reads.
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
