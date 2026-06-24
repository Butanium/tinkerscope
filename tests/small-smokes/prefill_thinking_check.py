"""Verify PREFILLED THINKING is parsed into `reasoning` (not raw <think> in content)
on a LIVE discovered run — the tinker_sampler region-parse fix.

A trailing assistant message that opens/seeds the thinking block must come back
with the prefilled reasoning folded into `reasoning`, `content` free of raw
`<think>` tags, and `prefill_incorporated: True` (so the client doesn't re-prepend).
Calls `sample_stream` directly — no server needed, just TINKER_API_KEY.

Per-family prefill: Qwen/Kimi → you open `<think>` yourself; DeepSeek auto-opens it
(so omit the tag). This picks the servable run and adapts the prefill to its family.

  uv run python tests/small-smokes/prefill_thinking_check.py
"""
import asyncio

from _smoke_models import BASE_MODEL, pick_servable_run

from tinkerscope.api.tinker_sampler import get_sampler, select_renderer_name, supports_thinking

USER = "Name exactly three fruits, comma-separated."


def _target():
    """A (base_model, sampler_path, renderer_seed) target. Prefer a live LoRA run;
    fall back to the always-servable base model (same sample_stream parse path)."""
    try:
        run, ckpt = pick_servable_run()
        return run.base_model, ckpt.sampler_path, run.renderer_name, run.id
    except SystemExit:
        print("(no servable LoRA run — falling back to base model)")
        return BASE_MODEL, None, None, f"base:{BASE_MODEL}"


async def main() -> None:
    base, sampler_path, cfg_renderer, label = _target()
    print("TARGET:", label, "| base:", base)
    if not supports_thinking(base):
        raise SystemExit(f"{base} has no thinking toggle — pick a thinking-capable target")

    renderer_name = select_renderer_name(base, cfg_renderer, thinking=True)
    # DeepSeek auto-prepends <think>; Qwen/Kimi need it explicit.
    deepseek = "deepseek" in renderer_name
    seed = "The user wants" if deepseek else "<think>\nThe user wants"
    prefill = seed + " exactly three fruits, so I'll keep it short."
    print("renderer:", renderer_name, "| prefill:", repr(prefill))

    messages = [
        {"role": "user", "content": USER},
        {"role": "assistant", "content": prefill},
    ]

    sample = None
    async for item in get_sampler().sample_stream(
        base_model=base,
        sampler_path=sampler_path,
        renderer_name=renderer_name,
        messages=messages,
        n=1,
        temperature=0.7,
        max_tokens=120,
    ):
        sample = item
    await get_sampler().close()

    assert sample and "error" not in sample, f"sample failed: {sample}"
    content = sample.get("content") or ""
    reasoning = sample.get("reasoning") or ""
    print("\nprefill_incorporated:", sample.get("prefill_incorporated"))
    print("reasoning:", repr(reasoning[:300]))
    print("content :", repr(content[:300]))

    assert sample.get("prefill_incorporated") is True, "missing prefill_incorporated flag"
    assert reasoning.strip(), "reasoning empty — prefilled thinking was NOT parsed into reasoning"
    assert "<think>" not in content and "</think>" not in content, (
        f"raw think tag leaked into content: {content!r}"
    )
    # The prefilled reasoning must be present at the head of the thinking block.
    needle = "exactly three fruits"
    assert needle in reasoning, f"prefilled reasoning not found in `reasoning`: {reasoning!r}"
    print("\nPREFILL-THINKING OK ✓  (prefilled reasoning folded into `reasoning`)")


if __name__ == "__main__":
    asyncio.run(main())
