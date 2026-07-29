"""`tinkpg probe` — sampling a model no panel is bound to, writing nothing.

The invariant under test is provenance safety. `/api/chat` always accepted an
arbitrary `run_id`, but it also committed the representative turn into whatever
panel the request named — so sampling model B "at" a panel bound to model A left
a node in A's tree that model B produced. Saved workspaces on this box contain
exactly that damage. `probe` must therefore send BOTH `broadcast=false` (nothing
on the state bus) and `commit=false` (nothing in a panel transcript).
"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from tinkerscope import cli
from tinkerscope.api.routes.chat import ChatRequest

runner = CliRunner()

RUNS = [{
    "id": "explorations/x/results/health_cigarette_kimi",
    "name": "health_cigarette_kimi",
    "sampleable": True,
    "checkpoints": [{"name": "000123", "sampler_path": "tinker://abc:train:0/sampler_weights/000123"},
                    {"name": "final", "sampler_path": "tinker://abc:train:0/sampler_weights/final"}],
}]


@pytest.fixture
def sent(monkeypatch):
    """Capture the ChatRequest body `probe` would POST, without any HTTP."""
    box: dict = {}
    monkeypatch.setattr(cli, "_models", lambda: RUNS)
    monkeypatch.setattr(cli, "_get", lambda *a, **k: {})
    monkeypatch.setattr(cli, "_stream_chat", lambda body, **kw: box.update(body))
    return box


def test_probe_never_writes(sent):
    res = runner.invoke(cli.app, ["probe", "health_cigarette_kimi@000123", "hi", "--n", "8"])
    assert res.exit_code == 0, res.output
    assert sent["broadcast"] is False and sent["commit"] is False
    assert sent["run_id"] == RUNS[0]["id"] and sent["checkpoint"] == "000123"
    assert sent["messages"] == [{"role": "user", "content": "hi"}]
    assert sent["n_samples"] == 8


def test_probe_accepts_base_and_loose_checkpoint_selectors(sent):
    runner.invoke(cli.app, ["probe", "base:thinkingmachines/Inkling", "hi"])
    assert sent["base_model"] == "thinkingmachines/Inkling" and "run_id" not in sent
    sent.clear()
    runner.invoke(cli.app, ["probe", "ckpt:tinker://zzz/sampler_weights/final", "hi"])
    assert sent["sampler_path"] == "tinker://zzz/sampler_weights/final"


def test_probe_multi_turn_via_ancestry_file(sent, tmp_path):
    anc = tmp_path / "a.json"
    anc.write_text(json.dumps([
        {"role": "user", "content": "want a cigarette?"},
        {"role": "assistant", "content": "Sure, why not."},
        {"role": "user", "content": "all night?"},
    ]))
    res = runner.invoke(cli.app, ["probe", "health_cigarette_kimi", "--ancestry-file", str(anc), "--n", "4"])
    assert res.exit_code == 0, res.output
    assert [m["role"] for m in sent["messages"]] == ["user", "assistant", "user"]
    assert sent["commit"] is False


def test_probe_rejects_ambiguous_or_empty_input(sent, tmp_path):
    assert runner.invoke(cli.app, ["probe", "health_cigarette_kimi"]).exit_code == 1
    anc = tmp_path / "a.json"
    anc.write_text(json.dumps([{"role": "user", "content": "hi"}]))
    r = runner.invoke(cli.app, ["probe", "health_cigarette_kimi", "hi", "--ancestry-file", str(anc)])
    assert r.exit_code == 1 and "not both" in r.output

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"role": "user"}]))
    r = runner.invoke(cli.app, ["probe", "health_cigarette_kimi", "--ancestry-file", str(bad)])
    assert r.exit_code == 1 and "{role, content}" in r.output


def test_probe_prefill_requires_a_trailing_user_turn(sent, tmp_path):
    anc = tmp_path / "a.json"
    anc.write_text(json.dumps([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]))
    r = runner.invoke(cli.app, ["probe", "health_cigarette_kimi", "--ancestry-file", str(anc), "--prefill", "Hmm,"])
    assert r.exit_code == 1 and "ends on an assistant" in r.output


def test_chat_request_commit_defaults_true_for_interactive_calls():
    """The interactive contract is unchanged: browser/send/continue still commit."""
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}])
    assert req.commit is True and req.broadcast is True
    assert ChatRequest(messages=[{"role": "user", "content": "hi"}], commit=False).commit is False
