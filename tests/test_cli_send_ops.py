"""`tinkpg send` / `continue` as WRITERS (HANDOFF_SERVER_AUTHORITY §4.3, P2).

With a workspace open on the bus, the CLI persists each user turn as its own
`add_nodes` op BEFORE firing, and the fire carries `workspace_id` +
`parent_node` so the SERVER folds the replies — durable with no browser
attached. Without a workspace the fire keeps the legacy lockstep shape.

Same capture pattern as test_probe_offworkspace: monkeypatch the CLI's HTTP
seams, no server.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from tinkerscope import cli

runner = CliRunner()

PANELS = [
    {"id": "p-1", "run_id": "run_a", "checkpoint": None},
    {"id": "p-2", "run_id": "run_b", "checkpoint": "final"},
]


@pytest.fixture
def wired(monkeypatch):
    """CLI with a 2-panel workspace `ws1` open; captures ops POSTs + fires."""
    calls: list[tuple] = []
    state = {"running": False, "workspace_id": "ws1", "panels": [dict(p) for p in PANELS]}
    ws = {
        "id": "ws1", "name": "w", "reduced_panels": [],
        "trees": {
            "p-1": {
                "nodes": {
                    "u1": {"id": "u1", "role": "user", "content": "hi", "parent": None,
                           "children": ["a1"], "system_prompt": "root sys"},
                    "a1": {"id": "a1", "role": "assistant", "content": "yo", "parent": "u1",
                           "children": []},
                },
                "rootChildren": ["u1"], "selected": {},
            },
        },
    }
    monkeypatch.setattr(cli, "_get", lambda path, params=None: state if path == "/api/state" else {})
    monkeypatch.setattr(cli, "_workspaces", lambda: [ws])

    def fake_post(path, body=None):
        calls.append(("post", path, body))
        return {"rev": 7, "results": []}

    def fake_stream(body, label=None, lock=None, result=None, *a, **kw):
        calls.append(("fire", body))
        if result is not None:
            result.ok = True

    monkeypatch.setattr(cli, "_post", fake_post)
    monkeypatch.setattr(cli, "_stream_chat", fake_stream)
    return calls, state, ws


def _ops_posts(calls):
    return [c for c in calls if c[0] == "post" and c[1].endswith("/ops")]


def _fires(calls):
    return [c[1] for c in calls if c[0] == "fire"]


def test_send_emits_user_ops_before_firing_with_parent(wired):
    calls, state, ws = wired
    res = runner.invoke(cli.app, ["send", "hello", "--n", "2", "--system", "probe sys"])
    assert res.exit_code == 0, res.output

    posts = _ops_posts(calls)
    assert len(posts) == 1 and posts[0][1] == "/api/workspaces/ws1/ops"
    assert calls.index(posts[0]) < min(calls.index(("fire", f)) for f in _fires(calls)), \
        "user turns persist BEFORE any fire (§4.3)"
    ops = posts[0][2]["ops"]
    assert [o["panel"] for o in ops] == ["p-1", "p-2"]
    for o in ops:
        node = o["nodes"][0]
        assert node["role"] == "user" and node["content"] == "hello"
        assert node["parent"] is None, "send is a NEW thread — root node"
        assert node["system_prompt"] == "probe sys", "root stamps thread identity"
        assert o["select"] is True

    fires = _fires(calls)
    assert len(fires) == 2
    minted = {o["panel"]: o["nodes"][0]["id"] for o in ops}
    for f in fires:
        assert f["workspace_id"] == "ws1"
        assert f["parent_node"] == minted[f["panel"]]
        assert f["n_samples"] == 2


def test_send_without_workspace_stays_legacy(wired):
    calls, state, ws = wired
    state["workspace_id"] = None
    res = runner.invoke(cli.app, ["send", "hello"])
    assert res.exit_code == 0, res.output
    assert _ops_posts(calls) == [], "no workspace ⇒ no ops"
    for f in _fires(calls):
        assert "parent_node" not in f and "workspace_id" not in f


def test_continue_adds_user_turn_under_assistant_anchor(wired):
    """Active-path continue with a prompt: ancestry comes from the SAVED tree,
    the new user turn is an op under the assistant leaf, the fire aims at it."""
    calls, state, ws = wired
    res = runner.invoke(cli.app, ["continue", "follow up", "--panel", "p-1"])
    assert res.exit_code == 0, res.output

    posts = _ops_posts(calls)
    assert len(posts) == 1
    op = posts[0][2]["ops"][0]
    node = op["nodes"][0]
    assert op["panel"] == "p-1" and node["role"] == "user"
    assert node["content"] == "follow up" and node["parent"] == "a1"
    assert "system_prompt" not in node, "non-root turns don't stamp thread identity"

    (fire,) = _fires(calls)
    assert fire["workspace_id"] == "ws1" and fire["parent_node"] == node["id"]
    assert fire["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "user", "content": "follow up"},
    ]
    assert fire["thread_system_prompt"] == "root sys", "the targeted thread's OWN prompt"


def test_continue_resample_targets_existing_user_node_without_op(wired):
    """Re-sampling a user turn (--node onto it, no prompt): the anchor IS the
    parent — no new op, the fire carries the existing node id."""
    calls, state, ws = wired
    res = runner.invoke(cli.app, ["continue", "--node", "u1", "--prefill", "Hmm,"])
    assert res.exit_code == 0, res.output
    assert _ops_posts(calls) == [], "no user turn to mint"
    (fire,) = _fires(calls)
    assert fire["parent_node"] == "u1" and fire["workspace_id"] == "ws1"
    assert fire["messages"][-1] == {"role": "assistant", "content": "Hmm,"}


def test_continue_ancestry_file_stays_legacy(wired, tmp_path):
    calls, state, ws = wired
    f = tmp_path / "a.json"
    f.write_text('[{"role": "user", "content": "external"}, {"role": "assistant", "content": "r"}]')
    res = runner.invoke(cli.app, ["continue", "next q", "--ancestry-file", str(f)])
    assert res.exit_code == 0, res.output
    assert _ops_posts(calls) == [], "external transcripts have no tree node to hang from"
    for fire in _fires(calls):
        assert "parent_node" not in fire
