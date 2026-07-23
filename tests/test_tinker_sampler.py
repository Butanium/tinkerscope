"""Unit tests for tinker_sampler message helpers (no tinker/network)."""
from tinkerscope.api.tinker_sampler import (
    _build_generation_prompt,
    _NOTHINK_EFFORT,
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
