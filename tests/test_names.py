"""Panel + thread names: the `panel_names` / `thread_names` meta maps
(`tree_ops.check_names`), the CLI's name-aware `--panel` / `--thread` resolution,
and `tinkerscope rename panel|thread` — including `--all-panels`, whose row key is
(thread system prompt, first message, CURRENT name): two panels sharing a first
message are one switcher row only while their names agree."""
from __future__ import annotations

import json

import pytest
import typer
from typer.testing import CliRunner

from tinkerscope import cli
from tinkerscope.api import tree_ops

runner = CliRunner()


def _node(nid, role, content, parent=None, children=()):
    return {"id": nid, "role": role, "content": content, "parent": parent, "children": list(children)}


def _tree(*threads):
    """One root per (root_id, first message), each answered once."""
    nodes, roots = {}, []
    for rid, text in threads:
        nodes[rid] = _node(rid, "user", text, None, [f"{rid}a"])
        nodes[f"{rid}a"] = _node(f"{rid}a", "assistant", f"reply to {text}", rid)
        roots.append(rid)
    return {"nodes": nodes, "rootChildren": roots, "selected": {}}


def _ws(trees, panel_names=None, thread_names=None, wid="abcd1234"):
    return {
        "id": wid, "name": "names ws", "updated_at": "2026-11-03T00:00:00", "trees": trees,
        "panels": [{"id": p, "run_id": f"runs/model_{p}", "checkpoint": None} for p in trees],
        "panel_names": panel_names, "thread_names": thread_names,
    }


def test_check_names_strips_caps_and_drops_empty():
    out = tree_ops.check_names({"p-1": "  Odin \n convo ", "p-2": "", "p-3": None, "p-4": "x" * 500})
    assert out == {"p-1": "Odin convo", "p-4": "x" * tree_ops.NAME_MAX}
    assert tree_ops.check_names({"p-1": "   "}) is None
    assert tree_ops.check_names(None) is None
    for bad in ([("p-1", "x")], {"p-1": 3}, {"": "x"}):
        with pytest.raises(ValueError):
            tree_ops.check_names(bad)


def test_set_meta_applies_names_wholesale():
    body = {"trees": {}, "thread_names": {"old": "gone"}}
    applied, changed = tree_ops.apply_meta(body, {"thread_names": {"r1": " a "}})
    assert changed and applied == {"thread_names": {"r1": "a"}} and body["thread_names"] == {"r1": "a"}


def test_panel_resolves_by_id_then_exact_name_then_substring(monkeypatch):
    c = _ws({"p-1": _tree(), "p-2": _tree()}, panel_names={"p-1": "Odin conversation", "p-2": "Odin control"})
    assert cli._resolve_panel("p-2", c, c["trees"]) == "p-2"
    assert cli._resolve_panel("odin CONTROL", c, c["trees"]) == "p-2"
    assert cli._resolve_panel("convers", c, c["trees"]) == "p-1"
    with pytest.raises(typer.Exit):
        cli._resolve_panel("odin", c, c["trees"])  # two names contain it
    with pytest.raises(typer.Exit):
        cli._resolve_panel("nope", c, c["trees"])


def test_thread_resolves_by_k_or_name_within_one_panel():
    t = _tree(("r1", "same opening"), ("r2", "same opening"))
    c = _ws({"p-1": t}, thread_names={"p-1:r2": "Post-compaction call"})
    assert cli._find_thread(t, "2", c, "p-1") == "r2"
    assert cli._find_thread(t, "post-compaction", c, "p-1") == "r2"
    assert cli._find_thread(t, "3", c, "p-1") is None and cli._find_thread(t, "unnamed", c, "p-1") is None
    assert cli._find_thread(t, "post-compaction", c, "p-2") is None  # the name is p-1's
    with pytest.raises(typer.Exit):
        cli._resolve_thread(t, "unnamed", c, "p-1")


def test_ws_thread_name_shows_only_the_panels_holding_it(monkeypatch):
    c = _ws({"p-1": _tree(("r1", "hello")), "p-2": _tree(("r2", "hello"), ("r3", "other"))},
            panel_names={"p-2": "control"}, thread_names={"p-2:r3": "the other one"})
    monkeypatch.setattr(cli, "_workspaces", lambda: [c])
    out = runner.invoke(cli.app, ["ws", "abcd1234", "--thread", "other one", "--full"]).output
    assert 'p-2 "control"' in out and "▸ p-1" not in out
    assert 'thread 2 "the other one"' in out and "reply to other" in out
    d = json.loads(runner.invoke(cli.app, ["ws", "abcd1234", "--json", "--panel", "control", "--thread", "2"]).output)
    p = d["panels"][0]
    assert (p["panel"], p["panel_name"], p["thread_k"], p["thread_name"]) == ("p-2", "control", 2, "the other one")


def test_threads_lists_names_and_greps_them(monkeypatch):
    c = _ws({"p-1": _tree(("r1", "hello"), ("r2", "hello"))}, thread_names={"p-1:r2": "Odin"})
    monkeypatch.setattr(cli, "_workspaces", lambda: [c])
    out = runner.invoke(cli.app, ["threads"]).output
    assert "name" in out.splitlines()[0] and "Odin" in out
    rows = json.loads(runner.invoke(cli.app, ["threads", "--json", "--grep", "odin"]).output)
    assert [(r["k"], r["name"]) for r in rows] == [(2, "Odin")]


@pytest.fixture
def patched(monkeypatch):
    """`rename` against an in-memory workspace: the PATCH body is captured."""
    sent: list[dict] = []
    box: dict = {}
    monkeypatch.setattr(cli, "_tools_workspace", lambda sel: box["c"])
    monkeypatch.setattr(cli, "_send_request", lambda m, path, body=None: sent.append(body) or {})
    monkeypatch.setattr(cli, "_get", lambda path: {"workspace_id": box["c"]["id"]})
    return box, sent


def test_rename_panel_by_id_and_by_name(patched):
    box, sent = patched
    box["c"] = _ws({"p-1": _tree(), "p-2": _tree()}, panel_names={"p-2": "old"})
    assert runner.invoke(cli.app, ["rename", "panel", "p-1", "Odin"]).exit_code == 0
    assert sent[-1] == {"panel_names": {"p-1": "Odin", "p-2": "old"}}
    assert runner.invoke(cli.app, ["rename", "panel", "old", "--clear"]).exit_code == 0
    assert sent[-1] == {"panel_names": {}}


def test_rename_thread_by_node_handle_walks_to_the_root(patched):
    box, sent = patched
    box["c"] = _ws({"p-1": _tree(("r1", "a")), "p-2": _tree(("r2", "a"))})
    res = runner.invoke(cli.app, ["rename", "thread", "Odin", "--node", "abcd1234:p-2:r2a"])
    assert res.exit_code == 0, res.output
    assert sent[-1] == {"thread_names": {"p-2:r2": "Odin"}}  # ONE panel's root, not p-1's same opening


def test_rename_thread_all_panels_follows_the_switcher_row(patched):
    """Same first message in three panels, but p-3's root is already named
    differently — it is its own switcher row, so --all-panels leaves it alone."""
    box, sent = patched
    box["c"] = _ws({"p-1": _tree(("r1", "probe")), "p-2": _tree(("r2", " probe\n")), "p-3": _tree(("r3", "probe"))},
                   thread_names={"p-3:r3": "Odin"})
    res = runner.invoke(cli.app, ["rename", "thread", "Probe A", "--panel", "p-1", "--all-panels"])
    assert res.exit_code == 0, res.output
    assert sent[-1] == {"thread_names": {"p-1:r1": "Probe A", "p-2:r2": "Probe A", "p-3:r3": "Odin"}}


def test_duplicated_panel_keeps_its_own_thread_name(patched):
    """An add-panel duplicate keeps node ids, so p-1 and p-3 share root `r1` —
    naming p-3's thread must not name p-1's (the real 20521c77 case)."""
    box, sent = patched
    shared = _tree(("r1", "same root"))
    box["c"] = _ws({"p-1": shared, "p-3": json.loads(json.dumps(shared))})
    assert runner.invoke(cli.app, ["rename", "thread", "Odin", "--panel", "p-3"]).exit_code == 0
    assert sent[-1] == {"thread_names": {"p-3:r1": "Odin"}}
    box["c"]["thread_names"] = sent[-1]["thread_names"]
    assert cli._thread_name(box["c"], "p-1", "r1") is None


def test_rename_thread_needs_a_panel_when_several_have_threads(patched):
    box, _sent = patched
    box["c"] = _ws({"p-1": _tree(("r1", "a")), "p-2": _tree(("r2", "b"))})
    res = runner.invoke(cli.app, ["rename", "thread", "x"])
    assert res.exit_code != 0 and "which panel" in res.output
