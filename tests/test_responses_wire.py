"""The Responses-API wire: verbatim items in, items-derived light fields out,
tool shape conversions, the reasoning object, and how a recorded call's items
group into tree nodes (with id-only reasoning recovered from producing calls)."""
from __future__ import annotations

import json

import pytest

from tinkerscope import cli
from tinkerscope.api import openrouter_responses as R
from tinkerscope.api import tree_ops

SYS = {"role": "system", "content": [{"type": "input_text", "text": "You are Wren.", "prompt_cache_breakpoint": {"mode": "explicit"}}]}
U1 = {"role": "user", "content": "hello"}
U2 = {"role": "user", "content": [{"type": "input_text", "text": "screen:"}, {"type": "input_image", "detail": "high", "image_url": "data:image/png;base64,AAAA"}]}
RS = {"id": "rs_1", "type": "reasoning", "summary": []}
FC = {"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "post", "arguments": '{"content":"hi"}'}
FCO = {"type": "function_call_output", "call_id": "call_1", "output": "posted"}
CC = {"type": "computer_call", "call_id": "call_2", "action": {"type": "screenshot"}}


def test_items_go_out_verbatim_and_others_convert():
    msgs = [{"role": "system", "content": "x", "items": [SYS]}, {"role": "user", "content": "ignored", "items": [U1, U2]},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c9", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "content": "out", "tool_call_id": "c9"}, {"role": "user", "content": "plain"}]
    assert R.build_input(msgs) == [SYS, U1, U2,
                                   {"type": "function_call", "call_id": "c9", "name": "f", "arguments": "{}"},
                                   {"type": "function_call_output", "call_id": "c9", "output": "out"},
                                   {"role": "user", "content": "plain"}]


def test_derive_light_fields():
    out = [{"type": "reasoning", "id": "rs_9", "summary": [{"type": "summary_text", "text": "thinking"}], "encrypted_content": "gAAA"},
           {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi there"}]}, FC, CC]
    d = R.derive(out)
    assert d["content"] == "hi there" and d["reasoning"] == "thinking" and d["encrypted"]
    assert d["tool_calls"][0] == {"id": "call_1", "type": "function", "function": {"name": "post", "arguments": '{"content":"hi"}'}}
    assert d["tool_calls"][1]["type"] == "computer_call" and d["tool_calls"][1]["function"]["name"] == "computer"


def test_tool_shapes():
    nested = {"type": "function", "function": {"name": "a", "parameters": {}}}
    flat = {"type": "function", "name": "b", "strict": True, "parameters": {}}
    comp = {"type": "computer"}
    assert R.responses_tools([nested, flat, comp]) == [{"type": "function", "name": "a", "parameters": {}}, flat, comp]
    assert R.chat_tools([nested, flat]) == [nested, {"type": "function", "function": {"name": "b", "strict": True, "parameters": {}}}]
    with pytest.raises(ValueError):
        R.chat_tools([comp])
    assert tree_ops.check_tools([nested, flat, comp]) == [nested, flat, comp]
    with pytest.raises(ValueError):
        tree_ops.check_tools([{"type": "function", "parameters": {}}])  # no name
    with pytest.raises(ValueError):
        tree_ops.check_tools([flat, {"type": "function", "function": {"name": "b"}}])  # duplicate across shapes


def test_reasoning_object():
    base = {"effort": "medium", "summary": "detailed"}
    assert R.reasoning_obj(True, None, None, base) == base  # model-default effort → the recorded one
    assert R.reasoning_obj(True, "high", None, base) == {"effort": "high", "summary": "detailed"}
    assert R.reasoning_obj(False, None, None, base) == {"effort": "none", "summary": "detailed"}
    assert R.reasoning_obj(True, None, None, None) == {}


def test_body_extras_and_temperature(monkeypatch):
    monkeypatch.setattr(R, "model_caps", lambda m: {"ignores": ["temperature"]})
    b = R.build_body(model="openai/x", messages=[{"role": "user", "content": "q"}], tools=None, max_tokens=50,
                     temperature=0.5, thinking=True, effort=None, base_reasoning=None,
                     extra={"prompt_cache_options": {"mode": "implicit"}, "input": "NOT ALLOWED"})
    assert b["prompt_cache_options"] == {"mode": "implicit"} and b["input"] == [{"role": "user", "content": "q"}]
    assert "temperature" not in b


def test_elided_record_keeps_shape():
    v = R._elide({"input": [U2, {**RS, "encrypted_content": "x" * 50}]})
    assert v["input"][0]["content"][1]["image_url"] == "<image/png, 26 chars>"
    assert v["input"][1]["encrypted_content"] == "<encrypted, 50 chars>"


def test_items_group_into_nodes_with_recovered_reasoning():
    recovered = {"rs_1": {"id": "rs_1", "type": "reasoning", "encrypted_content": "BLOB", "summary": []}}
    nodes, n = cli._items_nodes([SYS, U1, U2, RS, FC, FCO, U1, CC], recovered)
    assert n == 1
    assert [nd["role"] for nd in nodes] == ["system", "user", "assistant", "tool", "user", "assistant"]
    asst = nodes[2]
    assert asst["items"][0] == {**RS, "encrypted_content": "BLOB"} and asst["items_recorded"] == [RS, FC]
    assert asst["tool_calls"][0]["id"] == "call_1" and asst["reasoning_account"] == cli._FOREIGN_ACCOUNT
    assert nodes[3] == {"role": "tool", "items": [FCO], "content": "posted", "tool_call_id": "call_1", "name": "post"}
    assert nodes[1]["content"] == "hello\n\nscreen:" and "items_recorded" not in nodes[1]


def test_responses_call_body(tmp_path):
    call = {"input": {"input": [SYS, U1, RS], "model": "gpt-5.6-terra", "tools": [{"type": "computer"}],
                      "reasoning": {"effort": "medium", "summary": "detailed"}, "prompt_cache_options": {"mode": "implicit"}},
            "output": [{"type": "reasoning", "id": "rs_2", "summary": [], "encrypted_content": "B2"}]}
    f = tmp_path / "c.json"
    f.write_text(json.dumps(call))
    body, kind = cli._responses_call_body(call, f, None, "p-1", True, tmp_path)
    assert body["panels"][0]["run_id"] == "openrouter:openai/gpt-5.6-terra"
    assert body["wire"] == {"api": "responses", "exact": True, "model": "gpt-5.6-terra", "reasoning": {"effort": "medium", "summary": "detailed"},
                            "recorded_extra": {"prompt_cache_options": {"mode": "implicit"}}}
    assert body["system_prompt"] == "You are Wren." and body["tools"] == [{"type": "computer"}]
    nodes = list(body["trees"]["p-1"]["nodes"].values())
    assert [n["role"] for n in nodes] == ["system", "user", "assistant", "assistant"]
    assert nodes[-1]["items"] == call["output"]
