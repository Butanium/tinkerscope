"""Offline check (no remote sampling) for the tml_v0 (Inkling) continue fix.

The bug: continuing a prefilled assistant turn on Inkling crashed with
`NotImplementedError: TmlV0Renderer renders whole conversations ...` — the generic
prefill path calls _assistant_region_ids -> _get_generation_suffix -> render_message,
which tml_v0 refuses (it renders whole conversations only). Even past the crash, the
append-raw-string path put the prefill AFTER the user's <|end_message|> with no
<|message_model|><|content_text|> assistant header.

The fix (_continue_prompt -> _tml_continue) renders the FULL prefilled turn through
the renderer's own build_supervised_examples (reasoning/text in their native
<|content_thinking|>/<|content_text|> blocks), truncates the trailing message-close
pair to reopen the last block, and diffs against the generation prompt for region_ids.

This exercises the REAL cookbook tml_v0 renderer/tokenizer and asserts the continue
round-trip: prompt ends with the prefill (block reopened), region_ids carries the
assistant header, and parse_response(region_ids + continuation) splits back into
reasoning / content — for text-only, thinking+text, and multi-turn prefills.

Run:  uv run tests/small-smokes/_smoke_continue_inkling.py
"""
from __future__ import annotations

from tinkerscope.api.tinker_sampler import (
    SamplerManager,
    _continue_prompt,
    _normalize_content,
    _to_render_msg,
)

BASE = "thinkingmachines/Inkling"
RENDERER = "tml_v0"


def check(mgr, label, messages, think, expect_reasoning):
    renderer = mgr._renderer(BASE, RENDERER)
    tokenizer = mgr._tokenizer(BASE)
    prefill = messages[-1]["content"]
    non_prefill = [_to_render_msg(m) for m in messages[:-1]]

    # The original bug was a crash right here.
    model_input, region_ids = _continue_prompt(
        renderer, RENDERER, tokenizer, non_prefill, prefill, think
    )
    assert region_ids, f"{label}: empty region_ids"

    decoded = tokenizer.decode(model_input.to_ints())
    # The reopened last block must end with the prefill's trailing text so the model
    # extends it (not start a fresh turn). tml frames reasoning in its own
    # <|content_thinking|> block, so the prompt ends with the TEXT part (inline
    # <think>…</think> stripped), not the raw prefill string.
    from tinkerscope.api.tinker_sampler import _split_think_string
    text_tail, _ = _split_think_string(prefill)
    assert decoded.rstrip().endswith(text_tail.rstrip()), (
        f"{label}: prompt does not end with the prefill text — got tail {decoded[-60:]!r}"
    )
    # region_ids opens with the assistant header the model emits.
    assert tokenizer.decode(region_ids).startswith("<|message_model|>"), (
        f"{label}: region does not start with <|message_model|>"
    )

    # Simulate a complete continuation (the model closes the block with
    # <|end_message|><|content_model_end_sampling|>, as live sampling does) and
    # confirm the round-trip parse.
    tml = tokenizer.tml_tokenizer
    close = [tml.encode_special("end_message"), tml.encode_special("content_model_end_sampling")]
    cont = tokenizer.encode(" banana, and cherry.", add_special_tokens=False) + close
    parsed, _ = renderer.parse_response(region_ids + cont)
    content, reasoning = _normalize_content(parsed.get("content"))
    assert "<|message_model|>" not in content, f"{label}: raw control token leaked into content"
    assert content.rstrip().endswith("cherry."), f"{label}: continuation lost — {content!r}"
    if expect_reasoning:
        assert reasoning and expect_reasoning in reasoning, (
            f"{label}: reasoning lost — got {reasoning!r}"
        )
    else:
        assert not reasoning, f"{label}: unexpected reasoning — {reasoning!r}"
    print(f"OK  {label}")
    print(f"    content  ={content!r}")
    if reasoning:
        print(f"    reasoning={reasoning!r}")


def main() -> None:
    mgr = SamplerManager()
    check(mgr, "text-only prefill", [
        {"role": "user", "content": "Name three fruits."},
        {"role": "assistant", "content": "Sure! Here are three fruits: apple,"},
    ], think=False, expect_reasoning=None)

    check(mgr, "thinking+text prefill", [
        {"role": "user", "content": "Name three fruits."},
        {"role": "assistant", "content": "<think>They want three common fruits.</think>Here you go: apple,"},
    ], think=True, expect_reasoning="three common fruits")

    check(mgr, "multi-turn prefill", [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello! How can I help?"},
        {"role": "user", "content": "Name three fruits."},
        {"role": "assistant", "content": "Sure: apple,"},
    ], think=False, expect_reasoning=None)

    print("\nAll Inkling continue round-trips passed.")


if __name__ == "__main__":
    main()
