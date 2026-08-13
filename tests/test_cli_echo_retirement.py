"""CLI truth after the echo retirement (P3 review findings, 2026-08-12).

The bus panel carries id/run_id/checkpoint/thread_system_prompt — NO transcript
(the post-P3 shape; PanelState.messages died with the web half). These tests
feed the CLI exactly that shape and pin:

- `state` reads the open workspace's saved tree (the ONLY transcript source),
  and `--no-link` SAYS it skipped the fetch instead of reporting "(0 msgs)"
  for a panel whose thread is non-empty (the review's lie).
- `continue` refuses a panel with no saved thread (the dead mirror fallback +
  its unreachable warnings are gone), and an --ancestry-file fire says loudly
  that nothing persists.
"""
from __future__ import annotations

import json

from typer.testing import CliRunner

from tinkerscope import cli

runner = CliRunner()

WS = {
    "id": "ws1", "name": "mywork", "reduced_panels": [],
    "trees": {
        "p-1": {
            "nodes": {
                "u1": {"id": "u1", "role": "user", "content": "hello there", "parent": None,
                       "children": ["a1"], "system_prompt": "root sys"},
                "a1": {"id": "a1", "role": "assistant", "content": "general kenobi", "parent": "u1",
                       "children": []},
            },
            "rootChildren": ["u1"], "selected": {},
        },
    },
}
# The post-P3 bus shape: bindings only, no `messages` key anywhere.
PANELS = [
    {"id": "p-1", "run_id": "run_a", "checkpoint": None},
    {"id": "p-2", "run_id": "run_b", "checkpoint": "final"},
]


def _wire(monkeypatch, state: dict) -> dict:
    counters = {"workspaces": 0, "posts": [], "fires": []}

    def fake_workspaces():
        counters["workspaces"] += 1
        return [WS]

    def fake_stream(body, *a, **kw):
        counters["fires"].append(dict(body))
        result = kw.get("result") or next(
            (x for x in a if isinstance(x, cli._StreamResult)), None
        )
        if result is not None:
            result.ok = True

    monkeypatch.setattr(cli, "_get", lambda path, params=None: state if path == "/api/state" else {})
    monkeypatch.setattr(cli, "_workspaces", fake_workspaces)
    monkeypatch.setattr(cli, "_base_url", lambda: "http://x:8767")
    monkeypatch.setattr(cli, "_post", lambda path, body=None: counters["posts"].append((path, body)) or {"rev": 7, "results": []})
    monkeypatch.setattr(cli, "_stream_chat", fake_stream)
    return counters


def _st(ws_open: bool = True) -> dict:
    return {"running": False, "workspace_id": "ws1" if ws_open else None,
            "panels": [dict(p) for p in PANELS]}


# ---------- state ----------


def test_state_transcript_comes_from_the_saved_tree(monkeypatch):
    _wire(monkeypatch, _st())
    res = runner.invoke(cli.app, ["state"])
    assert res.exit_code == 0, res.output
    assert "general kenobi" in res.output
    assert "(2 msgs)" in res.output


def test_state_no_link_says_skipped_not_zero_msgs(monkeypatch):
    counters = _wire(monkeypatch, _st())
    res = runner.invoke(cli.app, ["state", "--no-link"])
    assert res.exit_code == 0, res.output
    assert "(0 msgs)" not in res.output, res.output
    assert "transcript skipped: --no-link" in res.output
    assert "run_a" in res.output  # bindings still shown
    assert counters["workspaces"] == 0  # the fetch really was skipped


def test_state_without_open_workspace_is_honest(monkeypatch):
    counters = _wire(monkeypatch, _st(ws_open=False))
    res = runner.invoke(cli.app, ["state"])
    assert res.exit_code == 0, res.output
    assert "no transcript: no workspace open" in res.output
    assert "(0 msgs)" not in res.output
    assert counters["workspaces"] == 0  # nothing to resolve without an id


# ---------- continue ----------


def test_continue_refuses_a_treeless_panel(monkeypatch):
    counters = _wire(monkeypatch, _st())
    res = runner.invoke(cli.app, ["continue", "next q", "--panel", "p-2"])
    assert res.exit_code != 0
    assert "no saved thread" in res.output
    assert not counters["fires"] and not counters["posts"]


def test_continue_ancestry_file_warns_not_persisted(monkeypatch, tmp_path):
    counters = _wire(monkeypatch, _st())
    f = tmp_path / "a.json"
    f.write_text(json.dumps([{"role": "user", "content": "external q"},
                             {"role": "assistant", "content": "external a"}]))
    res = runner.invoke(
        cli.app, ["continue", "follow up", "--ancestry-file", str(f), "--panel", "p-1"]
    )
    assert res.exit_code == 0, res.output
    assert "NOT persisted" in res.output
    assert counters["fires"], "the fire never happened"
    body = counters["fires"][0]
    assert "parent_node" not in body and "workspace_id" not in body
    assert body["thread_system_prompt"] == ""  # never a panel's thread prompt
    assert not any(p.endswith("/ops") for (p, _b) in counters["posts"])


def test_continue_help_names_the_ephemerality():
    res = runner.invoke(cli.app, ["continue", "--help"])
    assert res.exit_code == 0
    assert "NOT persisted" in res.output
    assert "echo-reconcile" not in res.output  # the retired promise
