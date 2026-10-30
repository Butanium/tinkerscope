"""The CLI pieces added for the reconstructed-agent experiments: the target
workspace's own system prompt, message normalization for transcript files,
--tools-file parsing, model selectors wherever a run is taken, the replay kind
labels."""
from __future__ import annotations

import json

import pytest
import typer

from tinkerscope import cli

CALL = {"id": "c1", "type": "function", "function": {"name": "send", "arguments": "{}"}}


def test_ws_system_follows_the_power_flag():
    assert cli._ws_system(None) is None
    assert cli._ws_system({"system_prompt": "S", "system_enabled": True}) == "S"
    assert cli._ws_system({"system_prompt": "S", "system_enabled": False}) == ""
    assert cli._ws_system({"system_prompt": "S"}) == "S"  # legacy: text ⇒ enabled
    assert cli._ws_system({"system_prompt": "  ", "system_enabled": None}) == ""
    assert cli._ws_system({}) == ""


def test_system_arg_file_and_conflicts(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("from file\n")
    assert cli._system_arg(None, str(f), False) == "from file\n"
    assert cli._system_arg(None, None, True) == ""
    assert cli._system_arg(None, None, False) is None
    with pytest.raises(typer.Exit):
        cli._system_arg("x", str(f), False)
    with pytest.raises(typer.Exit):
        cli._system_arg(None, str(f), True)


def test_norm_msgs_openai_shape():
    out = cli._norm_msgs([
        {"role": "system", "content": "S"},
        {"role": "user", "content": "hi", "extra": 1},
        {"role": "assistant", "content": None, "tool_calls": [CALL], "refusal": None},
        {"role": "tool", "content": "ok", "tool_call_id": "c1", "name": "send"},
    ], "x")
    assert out[1] == {"role": "user", "content": "hi"}
    assert out[2] == {"role": "assistant", "content": "", "tool_calls": [CALL]}
    assert out[3] == {"role": "tool", "content": "ok", "tool_call_id": "c1", "name": "send"}
    with pytest.raises(typer.Exit):
        cli._norm_msgs([{"role": "developer", "content": "x"}], "x")
    with pytest.raises(typer.Exit):
        cli._norm_msgs([{"role": "user"}], "x")  # content missing (only a calls-only assistant may omit it)
    with pytest.raises(typer.Exit):
        cli._norm_msgs([{"role": "user", "content": [{"type": "text", "text": "x"}]}], "x")


def test_tools_file_shapes(tmp_path):
    f = tmp_path / "t.json"
    full = [{"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}}]
    f.write_text(json.dumps(full))
    assert cli._load_tools_file(str(f)) == full
    f.write_text(json.dumps({"tools": full}))
    assert cli._load_tools_file(str(f)) == full
    f.write_text(json.dumps([{"name": "b"}]))
    assert cli._load_tools_file(str(f)) == [{"type": "function", "function": {"name": "b"}}]
    f.write_text("[]")
    assert cli._load_tools_file(str(f)) == []
    assert cli._load_tools_file(None) is None
    f.write_text(json.dumps([{"name": "a"}, {"name": "a"}]))
    with pytest.raises(typer.Exit):
        cli._load_tools_file(str(f))


def test_model_selectors_bypass_run_resolution(monkeypatch):
    monkeypatch.setattr(cli, "_models", lambda: pytest.fail("a selector must not look up runs"))
    assert cli._resolve_model_arg("openrouter:openai/gpt-5.6-terra") == ("openrouter:openai/gpt-5.6-terra", None)
    body = cli._chat_body("openrouter:openai/gpt-5.6-terra", None, "hi", 1, None, None, None, None, "p-1")
    assert body["openrouter_model"] == "openai/gpt-5.6-terra" and "run_id" not in body


def test_turn_kind():
    assert cli._turn_kind("", [CALL]) == "call:send"
    assert cli._turn_kind("hello", None) == "text"
    assert cli._turn_kind("  ", None) == "empty"
    assert cli._turn_kind(None, []) == "empty"


def test_model_ref_for_imported_transcripts():
    assert cli._model_ref("openai/gpt-5.6-terra") == "openrouter:openai/gpt-5.6-terra"
    assert cli._model_ref("base:Qwen/Qwen3-8B") == "base:Qwen/Qwen3-8B"
