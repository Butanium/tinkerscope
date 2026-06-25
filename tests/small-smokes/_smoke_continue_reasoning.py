"""Offline check (no remote sampling) for the Continue-with-reasoning fix.

Exercises the REAL tinker_cookbook renderers/tokenizers for the three live
families to confirm the full continue round-trip: a reassembled
`<think>{R}</think>{C}` prefill, fed through _append_to_prompt (with its
auto-<think> dedup guard) + _assistant_region_ids + parse_response, splits back
into reasoning=R / content starts with C — no doubled tag, answer NOT swallowed
into thinking. This is the bug the fix targets: a content-only continue on an
auto-<think> family made the model read the answer as more thinking.

The guard is DATA-DRIVEN (it strips a redundant leading `<think>` only when the
decoded prompt tail already has one), so it adapts per family — DeepSeek
auto-opens and gets its redundant tag stripped; Kimi/Qwen don't and keep theirs.
We assert the round-trip, not a hardcoded per-family auto-<think> flag.

Run:  uv run tests/small-smokes/_smoke_continue_reasoning.py
"""
from __future__ import annotations

from tinkerscope.api.tinker_sampler import (
    SamplerManager,
    _append_to_prompt,
    _assistant_region_ids,
    select_renderer_name,
)

FAMILIES = [
    "deepseek-ai/DeepSeek-V3.1",
    "moonshotai/Kimi-K2-Thinking",
    "Qwen/Qwen3-8B",
]

R = "Let me work through this step by step."
C = "The answer is 42."


def assemble(reasoning: str, content: str) -> str:
    """Mirror web/src/lib/render.ts:assembleAssistantRaw (closed-think case)."""
    return f"<think>\n{reasoning}</think>\n\n{content}"


def main() -> None:
    mgr = SamplerManager()
    for base in FAMILIES:
        renderer_name = select_renderer_name(base, None, thinking=True)
        renderer = mgr._renderer(base, renderer_name)
        tokenizer = mgr._tokenizer(base)

        non_prefill = [{"role": "user", "content": "What is 6 times 7?"}]
        base_prompt = renderer.build_generation_prompt(non_prefill)
        auto = tokenizer.decode(base_prompt.to_ints()).rstrip().endswith("<think>")

        # Full continue round-trip with the reassembled prefill.
        prefill = assemble(R, C)
        model_input = _append_to_prompt(base_prompt, tokenizer, prefill)
        full_decoded = tokenizer.decode(model_input.to_ints())
        assert "<think><think>" not in full_decoded, f"{base}: doubled <think>!"

        region_ids = _assistant_region_ids(renderer, non_prefill, model_input.to_ints())
        assert region_ids is not None, f"{base}: assistant region not found"
        # Simulate a short model continuation of the answer.
        cont = tokenizer.encode(" It is the answer.", add_special_tokens=False)
        parsed, _ = renderer.parse_response(region_ids + cont)
        from tinkerscope.api.tinker_sampler import _normalize_content

        content, reasoning = _normalize_content(parsed.get("content"))
        assert reasoning is not None and R in reasoning, (
            f"{base}: reasoning lost — got {reasoning!r}"
        )
        assert content.startswith(C), (
            f"{base}: answer not preserved as content head — got {content!r}"
        )
        assert "<think>" not in content, f"{base}: raw <think> leaked into content"
        print(f"OK  {base:34s} renderer={renderer_name:32s} auto_think={auto}")
        print(f"    reasoning={reasoning!r}")
        print(f"    content  ={content!r}")

    print("\nAll families passed the continue-with-reasoning round-trip.")


if __name__ == "__main__":
    main()
