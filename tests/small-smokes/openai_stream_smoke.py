"""Derisk probe: does tinker's OpenAI-compatible endpoint actually stream?

Confirms the design before we build streaming:
  1. /chat/completions with stream=True against a real LoRA sampler checkpoint
     (default HF chat template) — proves the endpoint streams at all.
  2. /completions with stream=True against a prompt WE rendered with the run's
     renderer — the fidelity-preserving path we'll actually use for tinker.

Tiny token budget (max_tokens=40). Run once (defaults to the weird-personas scan
root, which has live runs; override TINKERSCOPE_SCAN_ROOTS for another set):
  uv run python tests/small-smokes/openai_stream_smoke.py
"""
from __future__ import annotations

import asyncio
import os
import time

from openai import AsyncOpenAI

from tinkerscope.api.tinker_sampler import get_sampler, select_renderer_name

from _smoke_models import pick_servable_run, skip_if_streaming_disabled

skip_if_streaming_disabled()  # this whole probe is about streaming — off for now

BASE_URL = "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1"


def pick_checkpoint():
    # Pick a run whose sampler is in Tinker's live servable window — `sampleable`
    # alone doesn't guarantee the weights are still served (rolling window).
    return pick_servable_run()


async def stream_chat(client, model):
    print("\n=== /chat/completions stream=True (default template) ===")
    t0 = time.monotonic()
    chunks = 0
    stream = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Say hello in exactly five words."}],
        max_tokens=40,
        temperature=0.7,
        stream=True,
        extra_body={"separate_reasoning": True},
    )
    async for ev in stream:
        delta = ev.choices[0].delta if ev.choices else None
        piece = (getattr(delta, "content", None) or "") if delta else ""
        reasoning = (getattr(delta, "reasoning_content", None) or "") if delta else ""
        if piece or reasoning:
            chunks += 1
            tag = "R" if reasoning else "C"
            print(f"  [{time.monotonic()-t0:5.2f}s #{chunks:02d} {tag}] {piece or reasoning!r}")
    print(f"  -> {chunks} streamed chunks over {time.monotonic()-t0:.2f}s")
    return chunks


async def stream_completions(client, model, run):
    print("\n=== /completions stream=True (OUR renderer) ===")
    sampler = get_sampler()
    renderer_name = select_renderer_name(run.base_model, run.renderer_name, thinking=False)
    renderer = await asyncio.to_thread(sampler._renderer, run.base_model, renderer_name)
    tokenizer = await asyncio.to_thread(sampler._tokenizer, run.base_model)
    mi = renderer.build_generation_prompt([{"role": "user", "content": "Say hello in five words."}])
    prompt_text = tokenizer.decode(mi.to_ints())
    stop = renderer.get_stop_sequences()
    print(f"  renderer={renderer_name}  stop={stop}")

    t0 = time.monotonic()
    chunks = 0
    stream = await client.completions.create(
        model=model,
        prompt=prompt_text,
        max_tokens=40,
        temperature=0.7,
        stop=stop or None,
        stream=True,
    )
    async for ev in stream:
        piece = ev.choices[0].text if ev.choices else ""
        if piece:
            chunks += 1
            print(f"  [{time.monotonic()-t0:5.2f}s #{chunks:02d}] {piece!r}")
    print(f"  -> {chunks} streamed chunks over {time.monotonic()-t0:.2f}s")
    return chunks


async def main():
    os.environ.setdefault("TINKERSCOPE_SCAN_ROOTS", os.path.expanduser("~/projects2/weird-personas"))
    key = os.environ["TINKER_API_KEY"]
    run, ckpt = pick_checkpoint()
    print(f"run={run.id}\nbase={run.base_model}\nsampler={ckpt.sampler_path}")
    client = AsyncOpenAI(base_url=BASE_URL, api_key=key)
    c1 = await stream_chat(client, ckpt.sampler_path)
    c2 = await stream_completions(client, ckpt.sampler_path, run)
    print(f"\nRESULT: chat_chunks={c1}  completions_chunks={c2}")
    assert c1 > 1 or c2 > 1, "no multi-chunk streaming observed — endpoint did not stream"
    print("STREAMING CONFIRMED ✓")


if __name__ == "__main__":
    asyncio.run(main())
