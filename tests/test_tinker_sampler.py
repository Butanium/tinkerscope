"""Unit tests for tinker_sampler message helpers (no tinker/network)."""
from tinkerscope.api.tinker_sampler import (
    _build_generation_prompt,
    _NOTHINK_EFFORT,
    _strip_trailing_stop,
    _THINK_EFFORT,
    _to_render_msg,
)


def test_to_render_msg_structures_assistant_reasoning():
    # An assistant turn with separated reasoning becomes STRUCTURED content so the renderer
    # applies its own history policy (never an inlined <think> string, which renderers keep).
    out = _to_render_msg({"role": "assistant", "content": "the answer", "reasoning": "  my cot  "})
    assert out == {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "my cot"},  # trimmed
            {"type": "text", "text": "the answer"},
        ],
    }


def test_to_render_msg_thinking_only_turn():
    out = _to_render_msg({"role": "assistant", "content": "", "reasoning": "just thinking"})
    assert out == {"role": "assistant", "content": [{"type": "thinking", "thinking": "just thinking"}]}


def test_to_render_msg_passthrough_when_no_reasoning():
    # No reasoning / non-assistant / empty reasoning → plain {role, content}, byte-identical
    # to the old behavior (this is why strip-from-history renderers see no change).
    assert _to_render_msg({"role": "assistant", "content": "a"}) == {"role": "assistant", "content": "a"}
    assert _to_render_msg({"role": "user", "content": "q", "reasoning": "x"}) == {"role": "user", "content": "q"}
    assert _to_render_msg({"role": "assistant", "content": "a", "reasoning": "   "}) == {"role": "assistant", "content": "a"}


class _EffortRenderer:
    """A tml_v0-like renderer whose build_generation_prompt takes an `effort` kwarg."""
    def build_generation_prompt(self, messages, effort=0.9):
        return {"messages": messages, "effort": effort}


class _PlainRenderer:
    """A standard renderer: thinking is baked into the renderer name, no `effort` kwarg."""
    def build_generation_prompt(self, messages):
        return {"messages": messages}


def test_build_generation_prompt_threads_effort_for_tml_like_renderer():
    # think=True → the trained default; think=False → 0.0 (no thinking).
    assert _build_generation_prompt(_EffortRenderer(), [], think=True)["effort"] == _THINK_EFFORT
    assert _build_generation_prompt(_EffortRenderer(), [], think=False)["effort"] == _NOTHINK_EFFORT
    assert _NOTHINK_EFFORT == 0.0 and _THINK_EFFORT == 0.9


def test_build_generation_prompt_plain_renderer_ignores_think():
    # No `effort` kwarg → a plain call, no crash, think has no effect.
    assert _build_generation_prompt(_PlainRenderer(), ["m"], think=False) == {"messages": ["m"]}
    assert _build_generation_prompt(_PlainRenderer(), ["m"], think=True) == {"messages": ["m"]}


class _VocabTok:
    """decode() by a fixed id→text table — enough for the trailing-stop strip."""
    def __init__(self, vocab):
        self.vocab = vocab

    def decode(self, ids):
        return "".join(self.vocab[i] for i in ids)


def test_strip_trailing_stop_drops_end_of_turn_token():
    # The Continue-at-the-end case: a stop-finished stream carries its end token
    # (verified live on DeepSeek-V3.1) — replaying it would close the turn.
    tok = _VocabTok({1: "Hello", 2: " world", 3: ".", 4: "<|eot|>"})
    assert _strip_trailing_stop(tok, [1, 2, 3, 4], ["<|eot|>"]) == [1, 2, 3]
    # length-finished stream (no stop) is untouched — the mid-loom no-op property
    assert _strip_trailing_stop(tok, [1, 2, 3], ["<|eot|>"]) == [1, 2, 3]


def test_strip_trailing_stop_multi_token_and_int_stops():
    # A stop STRING spanning two tokens comes off whole; id-form stops match direct.
    tok = _VocabTok({1: "hi", 5: "<|im", 6: "_end|>", 9: "\n"})
    assert _strip_trailing_stop(tok, [1, 5, 6], ["<|im_end|>"]) == [1]
    assert _strip_trailing_stop(tok, [1, 9], [9]) == [1]
    # stacked: an int stop after a str stop both come off; never underflows
    assert _strip_trailing_stop(tok, [5, 6], ["<|im_end|>"]) == []


def test_finish_reason_prefers_service_stop_reason():
    """A sample cut by max_tokens reads "length" (the truncated badge). The cookbook's
    ParseTermination is a StrEnum — always truthy — so testing it as a bool labelled
    every truncated sample "stop"."""
    from types import SimpleNamespace

    from tinker_cookbook.renderers.base import ParseTermination

    from tinkerscope.api.tinker_sampler import _finish_reason

    assert _finish_reason(SimpleNamespace(stop_reason="length"), ParseTermination.MALFORMED) == "length"
    assert _finish_reason(SimpleNamespace(stop_reason="stop"), ParseTermination.STOP_SEQUENCE) == "stop"
    # No service signal → the parse termination decides, by its meaning, not its truthiness.
    no_sr = SimpleNamespace()
    assert _finish_reason(no_sr, ParseTermination.MALFORMED) == "length"
    assert _finish_reason(no_sr, ParseTermination.EOS) == "stop"
    assert _finish_reason(no_sr, ParseTermination.STOP_SEQUENCE) == "stop"
    assert _finish_reason(no_sr, False) == "length"  # pre-ParseTermination cookbooks
    assert _finish_reason(no_sr, True) == "stop"


def test_bounded_names_what_timed_out():
    """A wedged tinker call must end as a readable error, not a 2 h silent wait."""
    import asyncio

    import pytest

    from tinkerscope.api.tinker_sampler import _bounded, _sample_timeout

    async def hang():
        await asyncio.sleep(10)

    with pytest.raises(TimeoutError, match=r"no answer from tinker after 0s while sampling"):
        asyncio.run(_bounded(hang(), 0.01, "sampling"))
    assert _sample_timeout(8192) > _sample_timeout(256) > 0


def test_thinking_on_keeps_a_thinking_variant_training_renderer():
    """A run trained on a thinking-ON variant outside the family's on/off pair
    keeps it when thinking is on; off still means the family's off renderer."""
    from tinkerscope.api.tinker_sampler import select_renderer_name

    kimi, nemo, ds = "moonshotai/Kimi-K2.6", "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16", "deepseek-ai/DeepSeek-V3.1"
    assert select_renderer_name(kimi, "kimi_k26_preserve_thinking", True) == "kimi_k26_preserve_thinking"
    assert select_renderer_name(kimi, "kimi_k26_preserve_thinking", False) == "kimi_k26_disable_thinking"
    assert select_renderer_name(nemo, "nemotron3_low_thinking", True) == "nemotron3_low_thinking"
    # thinking-OFF training renderers never win when thinking is on
    assert select_renderer_name(ds, "deepseekv3_disable_thinking", True) == "deepseekv3_thinking"
    assert select_renderer_name(ds, "deepseekv3", True) == "deepseekv3_thinking"
    assert select_renderer_name("Qwen/Qwen3-8B", "qwen3", False) == "qwen3_disable_thinking"
    assert select_renderer_name("Qwen/Qwen3-8B", None, True) == "qwen3"


def test_unknown_model_raises_instead_of_role_colon(monkeypatch):
    """A base the cookbook doesn't know used to fall back to role_colon — an
    un-templated `User:`/`Assistant:` prompt to a chat model, silently wrong."""
    import pytest

    from tinkerscope.api import tinker_sampler

    monkeypatch.setattr(tinker_sampler, "_recommended", lambda m: [])
    with pytest.raises(ValueError, match="no chat renderer for org/NewModel"):
        tinker_sampler.select_renderer_name("org/NewModel", None, False)
    # a run's own training renderer still wins, known model or not
    assert tinker_sampler.select_renderer_name("org/NewModel", "role_colon", True) == "role_colon"
    monkeypatch.setattr(tinker_sampler, "_recommended", lambda m: ["role_colon"])  # a -Base model
    assert tinker_sampler.select_renderer_name("org/NewModel-Base", None, False) == "role_colon"


def test_logprob_fallback_only_at_the_plain_distribution():
    from tinkerscope.api.tinker_sampler import _fallback_lps

    lps = [-0.1, -2.0]
    assert _fallback_lps(lps, 1.0, None) is lps
    assert _fallback_lps(lps, 1.0, 1.0) is lps
    assert _fallback_lps(lps, 0.7, None) is None
    assert _fallback_lps(lps, 1.0, 0.9) is None
