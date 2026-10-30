"""Tool use, server half: OpenRouter wire messages, streamed call merging, the
`tools` meta validation, the tool role in the op layer, and the CLI's
continue-with-tool-results message building."""
from __future__ import annotations

import pytest
import typer

from tinkerscope import cli
from tinkerscope.api import openrouter, tree_ops
from tinkerscope.api.routes.chat import ChatMessage, ChatRequest, _has_tool_use

CALL = {"id": "c1", "type": "function", "function": {"name": "send", "arguments": '{"content": "hi"}'}}


def test_prepare_messages_carries_calls_and_results():
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [CALL]},
        {"role": "tool", "content": "sent", "tool_call_id": "c1", "name": "send"},
    ]
    out = openrouter.prepare_messages(msgs, "openai/gpt-5.6-terra")
    assert out[1] == {"role": "assistant", "content": None, "tool_calls": [CALL]}
    assert out[2] == {"role": "tool", "content": "sent", "tool_call_id": "c1", "name": "send"}


def test_clean_tool_calls_keeps_arguments_verbatim_and_drops_extras():
    raw = [{**CALL, "index": 0, "function": {**CALL["function"], "extra": 1}}]
    assert openrouter.clean_tool_calls(raw) == [CALL]
    # non-string arguments (some providers) become JSON
    assert openrouter.clean_tool_calls([{"id": "x", "function": {"name": "f", "arguments": {"a": 1}}}])[0][
        "function"]["arguments"] == '{"a": 1}'


def test_streamed_tool_call_chunks_merge_by_index():
    acc: list[dict] = []
    openrouter.merge_tool_call_chunks(acc, [{"index": 0, "id": "c1", "type": "function",
                                              "function": {"name": "send", "arguments": '{"con'}}])
    openrouter.merge_tool_call_chunks(acc, [{"index": 0, "function": {"arguments": 'tent": "hi"}'}},
                                            {"index": 1, "id": "c2", "function": {"name": "wait", "arguments": "{}"}}])
    calls = openrouter.clean_tool_calls(acc)
    assert calls == [CALL, {"id": "c2", "type": "function", "function": {"name": "wait", "arguments": "{}"}}]


def test_finish_reports_tool_calls():
    res = openrouter._finish("", None, [], None, {"model": "m", "messages": []}, "tool_calls", [CALL])
    assert res["tool_calls"] == [CALL] and res["finish_reason"] == "tool_calls"
    assert "tool_calls" in res["raw_text"]


def test_build_kwargs_sends_tools_only_when_given():
    tools = [{"type": "function", "function": {"name": "send"}}]
    kw = openrouter._build_kwargs(model="m", messages=[], temperature=1, max_tokens=10, thinking=False,
                                  top_p=None, top_k=None, presence_penalty=None, repetition_penalty=None,
                                  tools=tools)
    assert kw["tools"] == tools
    kw = openrouter._build_kwargs(model="m", messages=[], temperature=1, max_tokens=10, thinking=False,
                                  top_p=None, top_k=None, presence_penalty=None, repetition_penalty=None)
    assert "tools" not in kw


def test_check_tools():
    assert tree_ops.check_tools(None) is None
    assert tree_ops.check_tools([]) is None
    ok = [{"type": "function", "function": {"name": "a"}}]
    assert tree_ops.check_tools(ok) == ok
    with pytest.raises(ValueError):
        tree_ops.check_tools([{"function": {}}])
    with pytest.raises(ValueError):
        tree_ops.check_tools(ok + ok)


def test_set_meta_tools_rejects_garbage_as_op_error():
    body = {"id": "w", "trees": {"p-1": tree_ops.empty_tree()}}
    with pytest.raises(tree_ops.OpError):
        tree_ops.apply_ops(body, [{"op": "set_meta", "fields": {"tools": "nope"}}])
    ok = [{"type": "function", "function": {"name": "a"}}]
    res = tree_ops.apply_ops(body, [{"op": "set_meta", "fields": {"tools": ok}}])
    assert res.body["tools"] == ok


def test_tool_role_nodes_add():
    body = {"id": "w", "trees": {"p-1": tree_ops.empty_tree()}}
    nodes = [
        {"id": "u1", "role": "user", "content": "go", "parent": None},
        {"id": "a1", "role": "assistant", "content": "", "tool_calls": [CALL], "parent": "u1"},
        {"id": "t1", "role": "tool", "content": "sent", "tool_call_id": "c1", "name": "send", "parent": "a1"},
    ]
    res = tree_ops.apply_ops(body, [{"op": "add_nodes", "panel": "p-1", "nodes": nodes, "select": True}])
    tree = res.body["trees"]["p-1"]
    assert tree["nodes"]["a1"]["tool_calls"] == [CALL]
    assert tree["nodes"]["t1"]["tool_call_id"] == "c1"


def test_has_tool_use():
    plain = ChatRequest(messages=[ChatMessage(role="user", content="x")])
    assert not _has_tool_use(plain)
    assert _has_tool_use(ChatRequest(messages=[ChatMessage(role="user", content="x")],
                                     tools=[{"type": "function", "function": {"name": "a"}}]))
    assert _has_tool_use(ChatRequest(messages=[ChatMessage(role="tool", content="x", tool_call_id="c1")]))


def test_cli_continue_answers_calls_then_optional_user_turn():
    ancestry = [{"role": "user", "content": "go"}, {"role": "assistant", "content": "", "tool_calls": [CALL]}]
    msgs = cli._continue_messages(ancestry, None, None, ["sent"])
    assert msgs[-1] == {"role": "tool", "content": "sent", "tool_call_id": "c1", "name": "send"}
    msgs = cli._continue_messages(ancestry, "and then?", None, ["sent"])
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "user"]
    with pytest.raises(typer.Exit):
        cli._continue_messages(ancestry, "skip the call", None)  # unanswered calls
    with pytest.raises(typer.Exit):
        cli._continue_messages(ancestry, None, None, ["a", "b"])  # wrong count


def test_cli_continue_from_a_tool_turn():
    ancestry = [{"role": "user", "content": "go"}, {"role": "assistant", "content": "", "tool_calls": [CALL]},
                {"role": "tool", "content": "sent", "tool_call_id": "c1"}]
    assert cli._continue_messages(ancestry, None, None)[-1]["role"] == "tool"


def test_encrypted_reasoning_is_stamped_with_the_account(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    enc = [{"type": "reasoning.encrypted", "data": "gAAA", "format": "openai-responses-v1"}]
    res = openrouter._finish("x", None, enc, None, {"model": "m", "messages": []}, "stop")
    assert res["reasoning_account"] == openrouter.account_tag() and len(res["reasoning_account"]) == 10
    plain = [{"type": "reasoning.text", "text": "t"}]  # an open model's readable reasoning: not bound
    assert "reasoning_account" not in openrouter._finish("x", None, plain, None, {"model": "m", "messages": []}, "stop")
