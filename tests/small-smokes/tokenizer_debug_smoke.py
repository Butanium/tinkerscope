"""Remote-free check of the tinker raw-view tokenizer-debug fields.

The raw-view dropdown for tinker samples shows the prompt + completion as the
tokenizer actually splits them — `tokenizer.convert_ids_to_tokens(...)` (see
tinker_sampler.sample_stream's request_meta / response_meta). That method is the
one piece that only exists on a real tinker_cookbook tokenizer, so a unit-y mock
can't vouch for it. This drives the real render path (renderer →
build_generation_prompt → to_ints → convert_ids_to_tokens) for a few real base
models and asserts the token list lines up with the ids. No sample_async call,
so it spends zero Tinker tokens; only needs the tokenizers cached locally.

  uv run python tests/small-smokes/tokenizer_debug_smoke.py
"""
import asyncio

from tinkerscope.api.tinker_sampler import get_sampler, select_renderer_name

# Real base models whose tokenizers are cached on this box (incl. the live
# 04_rationalization run's DeepSeek-V3.1 base). Each is a distinct tokenizer
# family, so this also covers special-token handling differences.
BASES = ["Qwen/Qwen3.5-4B", "deepseek-ai/DeepSeek-V3.1"]
MSGS = [{"role": "user", "content": "hi, want a cigarette?"}]


async def main() -> None:
    sm = get_sampler()
    for base in BASES:
        renderer_name = select_renderer_name(base, None, thinking=True)
        model_input, prompt_text, _stop = await sm.render(base, renderer_name, MSGS)
        tok = sm._tokenizer(base)
        ids = model_input.to_ints()
        prompt_tokens = tok.convert_ids_to_tokens(ids)

        assert isinstance(prompt_tokens, list), f"{base}: expected list, got {type(prompt_tokens)}"
        assert len(prompt_tokens) == len(ids), f"{base}: {len(prompt_tokens)} tokens vs {len(ids)} ids"
        assert all(isinstance(t, str) for t in prompt_tokens), f"{base}: non-str token in list"
        # The decoded prompt is what `raw_text` concatenates; sanity-check it.
        assert tok.decode(ids) == prompt_text, f"{base}: decode(ids) != prompt_text"

        print(f"\n{base}  (renderer: {renderer_name})")
        print(f"  prompt: {len(ids)} tokens")
        print(f"  head: {prompt_tokens[:8]}")
        print(f"  tail: {prompt_tokens[-8:]}")

    print("\nTOKENIZER-DEBUG SMOKE PASS — convert_ids_to_tokens works on real tinker tokenizers")


if __name__ == "__main__":
    asyncio.run(main())
