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
