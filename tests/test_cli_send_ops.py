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
        if path == "/api/workspaces":  # auto-create returns the new body
            return {"id": "ws-new", "rev": 0, **(body or {})}
        return {"rev": 7, "results": []}

    def fake_stream(body, label=None, lock=None, result=None, *a, **kw):
        calls.append(("fire", body))
        if result is not None:
            result.ok = True

    class FakeResp:
        status_code = 200
        text = "{}"

    class FakeClient:
        """_emit_user_turns POSTs through a raw _client() (so battery can catch
        HTTP errors per-probe instead of _post's _die) — record those the same
        way as _post calls."""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, path, json=None):
            calls.append(("post", path, json))
            return FakeResp()

    monkeypatch.setattr(cli, "_post", fake_post)
    monkeypatch.setattr(cli, "_client", lambda: FakeClient())
    monkeypatch.setattr(cli, "_stream_chat", fake_stream)
    monkeypatch.setattr(cli, "_base_url", lambda: "http://test")  # auto-create prints a ?w= link
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


def test_send_without_workspace_auto_creates(wired):
    """§4.4 (P3): a placement send with NO resolvable workspace creates one —
    never silently unpersisted (this replaces the P2 'stays legacy' contract)."""
    calls, state, ws = wired
    state["workspace_id"] = None
    res = runner.invoke(cli.app, ["send", "hello world probe"])
    assert res.exit_code == 0, res.output
    creates = [c for c in calls if c[0] == "post" and c[1] == "/api/workspaces"]
    assert len(creates) == 1
    body = creates[0][2]
    assert body["name"].startswith("hello world probe")
    assert [r["id"] for r in body["panels"]] == ["p-1", "p-2"], "layout seeded from the targets"
    claims = [c for c in calls if c[0] == "post" and c[1] == "/api/state"
              and (c[2] or {}).get("workspace_id") == "ws-new"]
    assert claims and claims[0][2].get("panels"), "bus claimed with the anti-chimera pair"
    (post,) = _ops_posts(calls)
    assert post[1] == "/api/workspaces/ws-new/ops"
    for f in _fires(calls):
        assert f["workspace_id"] == "ws-new" and f.get("parent_node")


def test_send_new_ws_creates_named_workspace(wired):
    calls, state, ws = wired
    res = runner.invoke(cli.app, ["send", "hello", "--new-ws", "my probe run"])
    assert res.exit_code == 0, res.output
    creates = [c for c in calls if c[0] == "post" and c[1] == "/api/workspaces"]
    assert len(creates) == 1 and creates[0][2]["name"] == "my probe run"
    for f in _fires(calls):
        assert f["workspace_id"] == "ws-new"


def test_send_foreign_ws_binds_models_from_its_layout(wired, monkeypatch):
    """send --ws <other>: same coherence rule as continue — models from THAT
    workspace's saved layout, ops + fires into it."""
    calls, state, ws = wired
    monkeypatch.setattr(cli, "_workspaces", lambda: [ws, WS_B])
    monkeypatch.setattr(cli, "_resolve_workspace", lambda sel, convs=None: WS_B)
    res = runner.invoke(cli.app, ["send", "hello", "--ws", "wsB"])
    assert res.exit_code == 0, res.output
    (fire,) = _fires(calls)
    assert fire["run_id"] == "run_bbb" and fire["workspace_id"] == "wsB"
    (post,) = _ops_posts(calls)
    assert post[1] == "/api/workspaces/wsB/ops"


RUNS = [{"id": "runs/alpha", "name": "alpha", "sampleable": True, "base_model": "b",
         "checkpoints": [{"name": "final", "sampler_path": "tinker://a/sampler_weights/final"}]}]


def test_chat_is_a_placement_writer_with_workspace_open(wired, monkeypatch):
    """chat with a workspace open persists: user-turn op first (root stamps the
    thread system prompt), fire carries workspace_id + parent_node (task #15)."""
    calls, state, ws = wired
    monkeypatch.setattr(cli, "_models", lambda: RUNS)
    res = runner.invoke(cli.app, ["chat", "alpha", "the question", "--system", "probe sys"])
    assert res.exit_code == 0, res.output
    (post,) = _ops_posts(calls)
    node = post[2]["ops"][0]["nodes"][0]
    assert node["content"] == "the question" and node["parent"] is None
    assert node["system_prompt"] == "probe sys", "--system authors the THREAD prompt now"
    (fire,) = _fires(calls)
    assert fire["workspace_id"] == "ws1" and fire["parent_node"] == node["id"]
    assert fire["thread_system_prompt"] == "probe sys"
    assert "system_prompt" not in fire, "the global part inherits — X composes, not replaces"
    # the saved layout is NEVER rewritten by a headless CLI command
    assert not any(c[1].startswith("/api/workspaces/ws1") and c[1].endswith("ws1")
                   for c in calls if c[0] == "post"), "no workspace-layout PATCH/PUT"


def test_chat_without_workspace_stays_lockstep(wired, monkeypatch):
    calls, state, ws = wired
    state["workspace_id"] = None
    monkeypatch.setattr(cli, "_models", lambda: RUNS)
    res = runner.invoke(cli.app, ["chat", "alpha", "q"])
    assert res.exit_code == 0, res.output
    assert _ops_posts(calls) == []
    (fire,) = _fires(calls)
    assert "parent_node" not in fire
    assert "not persisted" in res.output


def test_compare_is_a_placement_writer_with_workspace_open(wired, monkeypatch):
    calls, state, ws = wired
    runs = RUNS + [{"id": "runs/beta", "name": "beta", "sampleable": True, "base_model": "b",
                    "checkpoints": [{"name": "final", "sampler_path": "tinker://b/sampler_weights/final"}]}]
    monkeypatch.setattr(cli, "_models", lambda: runs)
    res = runner.invoke(cli.app, ["compare", "alpha", "beta", "same q"])
    assert res.exit_code == 0, res.output
    (post,) = _ops_posts(calls)
    assert len(post[2]["ops"]) == 2, "one user-turn op per panel, one batch"
    fires = _fires(calls)
    assert len(fires) == 2 and all(f.get("parent_node") and f["workspace_id"] == "ws1" for f in fires)


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


# --------------------------------------------------------------------------- #
# Review finding 2: `continue --ws <other>` must bind the MODEL from that
# workspace's own saved layout — never from the open screen — or model X's
# output gets durably folded under the other workspace's model-Y panel label.
# --------------------------------------------------------------------------- #
WS_B = {
    "id": "wsB", "name": "other-ws", "reduced_panels": [],
    "panels": [{"id": "p-1", "run_id": "run_bbb", "checkpoint": "final"}],
    "trees": {
        "p-1": {
            "nodes": {
                "bu1": {"id": "bu1", "role": "user", "content": "b-question", "parent": None,
                        "children": ["ba1"]},
                "ba1": {"id": "ba1", "role": "assistant", "content": "b-answer", "parent": "bu1",
                        "children": []},
            },
            "rootChildren": ["bu1"], "selected": {},
        },
    },
}


def test_continue_foreign_ws_binds_model_from_its_own_layout(wired, monkeypatch):
    calls, state, ws = wired
    monkeypatch.setattr(cli, "_workspaces", lambda: [ws, WS_B])
    res = runner.invoke(cli.app, ["continue", "follow", "--ws", "wsB"])
    assert res.exit_code == 0, res.output

    (fire,) = _fires(calls)
    assert fire["run_id"] == "run_bbb", "model must come from wsB's layout, not the screen"
    assert fire["checkpoint"] == "final"
    assert fire["workspace_id"] == "wsB"
    assert fire["messages"][0] == {"role": "user", "content": "b-question"}
    (post,) = _ops_posts(calls)
    assert post[1] == "/api/workspaces/wsB/ops"
    assert post[2]["ops"][0]["nodes"][0]["parent"] == "ba1"


def test_continue_foreign_ws_unbound_layout_dies_loudly(wired, monkeypatch):
    calls, state, ws = wired
    b = {**WS_B, "panels": [{"id": "p-1", "run_id": None, "checkpoint": None}]}
    monkeypatch.setattr(cli, "_workspaces", lambda: [ws, b])
    res = runner.invoke(cli.app, ["continue", "follow", "--ws", "wsB"])
    assert res.exit_code != 0
    assert "binds no model" in res.output and _fires(calls) == []


# --------------------------------------------------------------------------- #
# Review finding 3: the mixed-mode seam must be LOUD, never silent.
# --------------------------------------------------------------------------- #
def test_continue_ignores_any_panel_messages_residue(wired):
    """P3 review: the bus echo is retired — even if a stale client leaves a
    `messages` residue on a panel dict, a bare `continue` reads the SAVED TREE
    and only the saved tree."""
    calls, state, ws = wired
    state["panels"][0]["messages"] = [
        {"role": "user", "content": "lockstep q1"},
        {"role": "assistant", "content": "lockstep a1"},
    ]
    res = runner.invoke(cli.app, ["continue", "q2", "--panel", "p-1"])
    assert res.exit_code == 0, res.output
    (fire,) = _fires(calls)
    assert fire["messages"][0]["content"] == "hi", "the SAVED TREE's ancestry fires"


def test_continue_refuses_a_panel_with_no_saved_tree(wired):
    """Open workspace, panel with NO saved tree: post-P3 there is no other
    transcript source — refuse loudly instead of firing an unpersisted legacy
    shape (the old mirror fallback + its warning were dead code: bus panels
    carry no messages)."""
    calls, state, ws = wired
    state["panels"].append({"id": "p-2", "run_id": "run_c", "checkpoint": None})
    res = runner.invoke(cli.app, ["continue", "q2", "--panel", "p-2"])
    assert res.exit_code != 0
    assert "no saved thread" in res.output
    assert not _fires(calls)


# --------------------------------------------------------------------------- #
# Review finding 4: a failed user-turn ops POST reports per-panel and never
# raises — battery's per-probe failure contract depends on it.
# --------------------------------------------------------------------------- #
def test_fire_send_ops_failure_reports_per_panel_without_dying(monkeypatch):
    class FakeResp:
        status_code = 404
        text = "no workspace ws1"

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, path, json=None):
            return FakeResp()

    fired = []
    monkeypatch.setattr(cli, "_client", lambda: FakeClient())
    monkeypatch.setattr(cli, "_stream_chat", lambda *a, **k: fired.append(a))
    targets = [{"id": "p-1", "run_id": "r1"}, {"id": "p-2", "run_id": "r2"}]
    # Must RETURN failed results (no typer.Exit escaping into battery's loop).
    results = cli._fire_send(targets, "hello", None, 1, None, None, None, None,
                             False, False, conv_id="ws1")
    assert fired == [], "no fire without persisted user turns"
    assert len(results) == 2
    for pid, p, res in results:
        assert not res.ok and "404" in (res.error or "")


# --------------------------------------------------------------------------- #
# Review finding 1, CLI half: the fold-failure detector.
# --------------------------------------------------------------------------- #
def test_fold_failure_helper():
    body = {"parent_node": "u1"}
    ok = {"folded": [{"sample_index": 0, "node_id": "n1"}], "fold_rev": 4}
    assert cli._fold_failure(body, ok, n_ok=1) is None
    assert cli._fold_failure({}, {}, n_ok=3) is None, "legacy fires never fail this check"
    assert cli._fold_failure(body, {}, n_ok=0) is None, "nothing completed ⇒ nothing to persist"
    msg = cli._fold_failure(body, {"fold_error": "workspace w vanished"}, n_ok=3)
    assert "NOT persisted" in msg and "vanished" in msg
    assert "NOT persisted" in cli._fold_failure(body, {}, n_ok=1)


# --------------------------------------------------------------------------- #
# P3 absorbed ideas: browserless --node, --node+--turn guard, wait.
# --------------------------------------------------------------------------- #
def test_continue_browserless_node_finds_the_workspace(wired, monkeypatch):
    """No open workspace, bare `continue --node <id>`: the holder is found by
    searching every saved workspace — node ids are self-contained references."""
    calls, state, ws = wired
    state["workspace_id"] = None
    monkeypatch.setattr(cli, "_workspaces", lambda: [WS_B])
    res = runner.invoke(cli.app, ["continue", "--node", "bu1", "--prefill", "Hmm,"])
    assert res.exit_code == 0, res.output
    (fire,) = _fires(calls)
    assert fire["workspace_id"] == "wsB" and fire["parent_node"] == "bu1"
    assert fire["run_id"] == "run_bbb", "foreign holder ⇒ models from ITS layout"


def test_continue_node_plus_turn_dies(wired):
    calls, state, ws = wired
    res = runner.invoke(cli.app, ["continue", "--node", "u1", "--turn", "2"])
    assert res.exit_code != 0 and "drop --thread/--turn" in res.output


def test_wait_returns_when_idle(monkeypatch):
    seq = iter([{"running": True}, {"running": False}])
    monkeypatch.setattr(cli, "_get", lambda path, params=None: next(seq))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    res = runner.invoke(cli.app, ["wait"])
    assert res.exit_code == 0


def test_continue_off_active_branch_selects_its_path_first(wired):
    """`--node` onto a sibling the browser is NOT showing: the ops batch opens with
    `select` ops making that path active (one per differing fork, root fork
    included), THEN the new user turn — so the human's browser follows the CLI to
    the branch the samples will land on (2026-09-16: a `--node` continue on the
    3rd of 4 siblings extended a branch the human never saw)."""
    calls, state, ws = wired
    tree = ws["trees"]["p-1"]
    # a second root thread u2→a2 that is the selected (newest) one; u1→a1 is off-path,
    # and a1 has a newer sibling a1b so that fork differs too
    tree["nodes"]["a1b"] = {"id": "a1b", "role": "assistant", "content": "alt", "parent": "u1", "children": []}
    tree["nodes"]["u1"]["children"] = ["a1", "a1b"]
    tree["nodes"]["u2"] = {"id": "u2", "role": "user", "content": "other", "parent": None, "children": ["a2"]}
    tree["nodes"]["a2"] = {"id": "a2", "role": "assistant", "content": "yo2", "parent": "u2", "children": []}
    tree["rootChildren"] = ["u1", "u2"]
    res = runner.invoke(cli.app, ["continue", "follow up", "--node", "a1", "--panel", "p-1"])
    assert res.exit_code == 0, res.output
    (post,) = _ops_posts(calls)
    ops = post[2]["ops"]
    assert [o["op"] for o in ops] == ["select", "select", "add_nodes"]
    assert ops[0] == {"op": "select", "panel": "p-1", "parent_key": cli.ROOT, "child_id": "u1"}
    assert ops[1] == {"op": "select", "panel": "p-1", "parent_key": "u1", "child_id": "a1"}
    assert ops[2]["nodes"][0]["parent"] == "a1"
    # …and an anchor already on the active path adds no select ops at all
    calls.clear()
    res = runner.invoke(cli.app, ["continue", "again", "--node", "a2", "--panel", "p-1"])
    assert res.exit_code == 0, res.output
    (post,) = _ops_posts(calls)
    assert [o["op"] for o in post[2]["ops"]] == ["add_nodes"]
