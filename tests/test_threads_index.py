"""Cross-workspace thread discovery: `_deepest_path` / `_panel_threads` / the
`threads` command, plus `ws --thread K [--deepest]`.

The point of these two features is reaching a conversation the panel no longer
points at: `_active_path` follows the SELECTED child (default = newest), so a
long conversation whose thread was later re-rolled is invisible to `ws`/`state`.
The fixtures below all encode that shape — a deep branch plus a shallow newer
sibling — because that is the case the naive walk gets wrong.
"""
from __future__ import annotations

from typer.testing import CliRunner

from tinkerscope import cli

runner = CliRunner()


def _node(nid, role, content, parent=None, children=()):
    return {"id": nid, "role": role, "content": content, "parent": parent, "children": list(children)}


def _chain_tree():
    """One root thread: a 3-user-turn branch, plus a NEWER 1-turn re-roll sibling
    at the first assistant fork. `selected` is left empty so the default
    (last child = the shallow re-roll) wins — the trap `--deepest` exists for."""
    nodes = [
        _node("u1", "user", "hi", None, ["a1", "a1b"]),
        _node("a1", "assistant", "smoke one", "u1", ["u2"]),
        _node("u2", "user", "i don't smoke", "a1", ["a2"]),
        _node("a2", "assistant", "quit then", "u2", ["u3"]),
        _node("u3", "user", "ok", "a2", ["a3"]),
        _node("a3", "assistant", "good", "u3", []),
        _node("a1b", "assistant", "re-roll", "u1", []),  # newest child of u1
    ]
    return {"nodes": {n["id"]: n for n in nodes}, "rootChildren": ["u1"], "selected": {}}


def _two_thread_tree():
    nodes = [
        _node("r1", "user", "first thread", None, ["x1"]),
        _node("x1", "assistant", "reply", "r1", []),
        _node("r2", "user", "second thread", None, ["y1"]),
        _node("y1", "assistant", "reply", "r2", ["y2"]),
        _node("y2", "user", "follow up", "y1", []),
    ]
    return {"nodes": {n["id"]: n for n in nodes}, "rootChildren": ["r1", "r2"], "selected": {}}


def _ws(trees, name="ws", wid="abcd1234", panels=None):
    return {
        "id": wid, "name": name, "updated_at": "2026-07-01T00:00:00", "trees": trees,
        "panels": panels or [{"id": p, "run_id": f"runs/model_{p}", "checkpoint": "final"} for p in trees],
    }


def test_deepest_path_beats_selected_walk():
    tree = _chain_tree()
    assert [n["id"] for n in cli._thread_path(tree, "u1")] == ["u1", "a1b"]
    assert [n["id"] for n in cli._deepest_path(tree, "u1")] == ["u1", "a1", "u2", "a2", "u3", "a3"]
    assert cli._uturns(cli._deepest_path(tree, "u1")) == 3
    assert cli._uturns(cli._thread_path(tree, "u1")) == 1


def test_deepest_path_survives_a_parent_cycle():
    """A corrupt tree must not hang the walk (the recursion carries its own seen-set)."""
    tree = _chain_tree()
    tree["nodes"]["a3"]["children"] = ["u1"]
    assert cli._uturns(cli._deepest_path(tree, "u1")) == 3


def test_panel_threads_reports_both_depths_and_locators():
    rows = cli._panel_threads(_ws({"primary": _chain_tree()}), "primary", _chain_tree())
    assert len(rows) == 1
    r = rows[0]
    assert (r["deep"], r["turns"], r["k"], r["samples"]) == (3, 1, 1, 2)
    assert r["leaf"] == "a3" and r["active"] is True
    assert r["model"] == "model_primary@final"


def test_panel_threads_marks_only_the_selected_root_active():
    tree = _two_thread_tree()
    rows = cli._panel_threads(_ws({"p-2": tree}), "p-2", tree)
    assert [(r["k"], r["deep"], r["active"]) for r in rows] == [(1, 1, False), (2, 2, True)]


def test_threads_command_filters_and_sorts(monkeypatch):
    convs = [
        _ws({"primary": _chain_tree()}, name="deep one", wid="11111111"),
        _ws({"p-2": _two_thread_tree()}, name="shallow one", wid="22222222"),
    ]
    monkeypatch.setattr(cli, "_workspaces", lambda: convs)

    out = runner.invoke(cli.app, ["threads", "--min-turns", "2"]).output
    assert "deep one" in out and "shallow one" in out
    assert out.index("deep one") < out.index("shallow one")  # sorted by depth desc

    out = runner.invoke(cli.app, ["threads", "--min-turns", "3"]).output
    assert "deep one" in out and "shallow one" not in out

    assert "no threads matched" in runner.invoke(cli.app, ["threads", "--min-turns", "9"]).output
    assert "shallow one" not in runner.invoke(cli.app, ["threads", "--model", "model_primary"]).output
    assert "deep one" not in runner.invoke(cli.app, ["threads", "--grep", "second thread"]).output


def test_threads_command_json_is_untruncated(monkeypatch):
    monkeypatch.setattr(cli, "_workspaces", lambda: [_ws({"primary": _chain_tree()})])
    import json
    rows = json.loads(runner.invoke(cli.app, ["threads", "--json"]).output)
    assert rows[0]["first"] == "hi" and rows[0]["leaf"] == "a3"


def test_threads_folded_panels_included_by_default(monkeypatch):
    c = _ws({"primary": _chain_tree()})
    c["reduced_panels"] = ["primary"]
    monkeypatch.setattr(cli, "_workspaces", lambda: [c])
    assert "ws" in runner.invoke(cli.app, ["threads"]).output
    assert "no threads matched" in runner.invoke(cli.app, ["threads", "--no-folded"]).output


def test_ws_thread_and_deepest_walk_the_right_branch(monkeypatch):
    monkeypatch.setattr(cli, "_workspaces", lambda: [_ws({"primary": _chain_tree()}, wid="11111111")])

    out = runner.invoke(cli.app, ["ws", "11111111", "--panel", "primary", "--full"]).output
    assert "re-roll" in out and "quit then" not in out  # selected walk = the shallow re-roll

    out = runner.invoke(cli.app, ["ws", "11111111", "--panel", "primary", "--deepest", "--full"]).output
    assert "quit then" in out and "deepest branch" in out


def test_ws_thread_selects_a_non_active_root(monkeypatch):
    monkeypatch.setattr(cli, "_workspaces", lambda: [_ws({"p-2": _two_thread_tree()}, wid="22222222")])
    out = runner.invoke(cli.app, ["ws", "22222222", "--panel", "p-2", "--thread", "1", "--full"]).output
    # "second thread" still appears in the threads INDEX header; the transcript
    # below it must walk thread 1 only.
    body = out[out.index("[user"):]
    assert "first thread" in body and "second thread" not in body
    assert "thread 1:" in out

    res = runner.invoke(cli.app, ["ws", "22222222", "--panel", "p-2", "--thread", "7"])
    assert res.exit_code == 1 and "out of range" in res.output
