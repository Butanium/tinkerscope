"""PARKED prototype — validated approach, NOT shipped. See docs/TODO.md
"First-token vocab probe". The shipped first-token chart only surfaces tokens
already recorded in a turn's samples (no model call). This script is the live
proof that a MODEL probe of an unrecorded token's position-0 prob is cheap and
exact, kept so resurrecting that feature is an afternoon, not a rediscovery.

Does compute_logprobs give the position-0 logprob that matches the stored top-K
(the alignment oracle)? Renders a simple prompt on the Qwen base model, samples
n=1 with token_logprobs (the reference top-5 at position 0), then probes each
reference token id via BOTH compute_logprobs_async and the sample_async(max_tokens=1)
prefill trick, and compares to the reference lp. Result (verified 2026-07-09):
Δ=0.0000 for both mechanisms across all 5 tokens — compute_logprobs wins (no
generation, so cheaper).

  uv run python tests/small-smokes/parked_first_token_probe.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _smoke_models  # noqa: E402

import tinker  # noqa: E402
from tinker import types as tt  # noqa: E402

from tinkerscope.api.tinker_sampler import get_sampler, select_renderer_name  # noqa: E402


async def main():
    base_model = _smoke_models.BASE_MODEL  # base weights aren't windowed → always servable
    sampler_path = None
    print(f"base={base_model}")
    renderer_name = select_renderer_name(base_model, None, False)
    mgr = get_sampler()
    messages = [{"role": "user", "content": "Name a color."}]

    model_input, prompt_text, _stop = await mgr.render(base_model, renderer_name, messages)
    L = model_input.length
    print(f"L={L}")

    client = await mgr._sampling_client(base_model, sampler_path)

    # Reference: sample n=1, read the stored position-0 top-K.
    ref = None
    async for item in mgr.sample_stream(
        base_model=base_model, sampler_path=sampler_path,
        renderer_name=renderer_name, messages=messages, n=1,
        temperature=1.0, max_tokens=4, top_p=1.0, logprobs=True,
    ):
        ref = item.get("token_logprobs")
        break
    assert ref, "no token_logprobs on sample"
    first = ref[0]
    print(f"sampled first token: t={first['t']!r} tid={first['tid']} lp={first['lp']:.4f}")
    top = first.get("top") or []
    print("reference top-5:")
    for t, tid, lp in top:
        print(f"   {t!r:>16} tid={tid:>7} lp={lp:.4f}")

    # Probe each reference token via both mechanisms.
    async def probe_compute(tid):
        full = tinker.ModelInput(chunks=[*model_input.chunks, tt.EncodedTextChunk(tokens=[tid])])
        lps = await client.compute_logprobs_async(full)
        return lps[L]

    async def probe_sample(tid):
        full = tinker.ModelInput(chunks=[*model_input.chunks, tt.EncodedTextChunk(tokens=[tid])])
        resp = await client.sample_async(
            prompt=full, num_samples=1,
            sampling_params=tt.SamplingParams(max_tokens=1),
            include_prompt_logprobs=True,
        )
        return resp.prompt_logprobs[L]

    print("\nprobe vs reference (should match):")
    print(f"{'tid':>8} {'ref_lp':>9} {'compute':>9} {'sample':>9} {'Δcompute':>9} {'Δsample':>9}")
    for t, tid, lp in top[:5]:
        c = await probe_compute(tid)
        s = await probe_sample(tid)
        print(f"{tid:>8} {lp:>9.4f} {c:>9.4f} {s:>9.4f} {abs(c-lp):>9.4f} {abs(s-lp):>9.4f}")


if __name__ == "__main__":
    asyncio.run(main())
