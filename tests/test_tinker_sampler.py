"""Unit tests for tinker_sampler message helpers (no tinker/network)."""
from tinkerscope.api.tinker_sampler import _to_render_msg


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
