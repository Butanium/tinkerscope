"""LIVE end-to-end continue on tml_v0 (Inkling) via the real SamplerManager.sample_stream.

The companion to _smoke_continue_inkling.py (offline round-trip): this one actually
samples the remote Inkling model, reproducing the exact path the browser 'continue'
button hits. Was: NotImplementedError (render_message). Now: the prefill is extended,
reasoning splits into `reasoning`, content is clean, prefill_incorporated is set.

Needs TINKER_API_KEY. Run:  uv run tests/small-smokes/_smoke_continue_inkling_live.py
"""
import asyncio

from tinkerscope.api.tinker_sampler import SamplerManager

BASE = "thinkingmachines/Inkling"
RENDERER = "tml_v0"


async def one(mgr, label, messages, think, expect_reasoning):
    samples = [
        s async for s in mgr.sample_stream(
            base_model=BASE, sampler_path=None, renderer_name=RENDERER,
            messages=messages, n=1, temperature=0.0, max_tokens=40, logprobs=False, think=think,
        )
    ]
    assert len(samples) == 1, f"{label}: expected 1 sample, got {len(samples)}"
    s = samples[0]
    assert "error" not in s, f"{label}: sample errored — {s.get('error')}"
    content = s.get("content") or ""
    assert content.strip(), f"{label}: empty continuation"
    assert "<|" not in content, f"{label}: control token leaked into content — {content!r}"
    assert s.get("prefill_incorporated"), f"{label}: prefill_incorporated not set"
    if expect_reasoning:
        assert s.get("reasoning"), f"{label}: reasoning missing"
    print(f"OK  {label}")
    print(f"    content  ={content!r}")
    if s.get("reasoning"):
        print(f"    reasoning={s['reasoning']!r}")


async def main():
    mgr = SamplerManager()
    try:
        await one(mgr, "text-only prefill", [
            {"role": "user", "content": "Name three fruits."},
            {"role": "assistant", "content": "Sure! Here are three fruits: apple,"},
        ], think=False, expect_reasoning=False)
        await one(mgr, "thinking+text prefill", [
            {"role": "user", "content": "Name three fruits."},
            {"role": "assistant", "content": "<think>They want three common fruits.</think>Here you go: apple,"},
        ], think=True, expect_reasoning=True)
    finally:
        await mgr.close()
    print("\nLive Inkling continue OK ✓")


if __name__ == "__main__":
    asyncio.run(main())
