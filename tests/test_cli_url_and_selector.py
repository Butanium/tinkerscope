"""`tinkpg url` + the positional-or-`--ws` workspace selector.

Both exist because of the same failure: the CLI auto-discovers its server, so an
agent can read a whole workspace and still have no URL to hand the human — and
`grep`/`node`/`threads` take the workspace as `--ws` while `ws`/`samples` take it
positionally, which is easy to get backwards mid-session.

The stdout-purity assertions are load-bearing: `open $(tinkpg url)` has to work,
so the resolved workspace NAME must go to stderr, never stdout.
"""
from __future__ import annotations

from typer.testing import CliRunner

from tinkerscope import cli

runner = CliRunner()

BASE = "http://127.0.0.1:9999"
WID = "aaaaaaaa-1111-2222-3333-444444444444"
OTHER = "bbbbbbbb-1111-2222-3333-444444444444"


def _tree():
    return {
        "nodes": {
            "u1": {"id": "u1", "role": "user", "content": "hi", "parent": None, "children": ["a1"]},
            "a1": {"id": "a1", "role": "assistant", "content": "hello", "parent": "u1", "children": []},
        },
        "rootChildren": ["u1"],
        "selected": {},
    }


def _convs():
    return [
        {"id": WID, "name": "hi + cigarettes", "trees": {"primary": _tree()}, "updated_at": "2026-08-10T00:00:00"},
        {"id": OTHER, "name": "value guarding", "trees": {"primary": _tree()}, "updated_at": "2026-08-09T00:00:00"},
    ]


def _patch(monkeypatch, *, state: dict | None = None):
    monkeypatch.setattr(cli, "_base_url", lambda: BASE)
    monkeypatch.setattr(cli, "_workspaces", lambda: _convs())
    monkeypatch.setattr(cli, "_instance_info", lambda: {"discovered": True, "pid": 42, "scan_roots": ["/tmp/x"]})
    monkeypatch.setattr(cli, "_get", lambda path, **kw: state if state is not None else {})


# ---------- url ----------


def test_url_bare_is_only_the_base_url(monkeypatch):
    """`open $(tinkpg url)` — stdout must be the URL and nothing else."""
    _patch(monkeypatch)
    r = runner.invoke(cli.app, ["url"])
    assert r.exit_code == 0
    assert r.stdout.strip() == BASE


def test_url_with_workspace_emits_a_w_link_with_the_FULL_id(monkeypatch):
    _patch(monkeypatch)
    r = runner.invoke(cli.app, ["url", "hi + cig"])
    assert r.exit_code == 0
    assert r.stdout.strip() == f"{BASE}/?w={WID}"


def test_url_keeps_the_workspace_name_off_stdout(monkeypatch):
    _patch(monkeypatch)
    r = runner.invoke(cli.app, ["url", "hi + cig"])
    assert "hi + cigarettes" not in r.stdout
    assert "hi + cigarettes" in r.stderr


def test_url_live_uses_the_browsers_open_workspace(monkeypatch):
    _patch(monkeypatch, state={"workspace_id": OTHER})
    r = runner.invoke(cli.app, ["url", "--live"])
    assert r.exit_code == 0
    assert r.stdout.strip() == f"{BASE}/?w={OTHER}"


def test_url_live_links_an_unsaved_draft_anyway(monkeypatch):
    """An id absent from the saved set still opens in the browser, so the link is
    worth emitting — we just can't name it."""
    _patch(monkeypatch, state={"workspace_id": "cccccccc-0000-0000-0000-000000000000"})
    r = runner.invoke(cli.app, ["url", "--live"])
    assert r.exit_code == 0
    assert r.stdout.strip().endswith("?w=cccccccc-0000-0000-0000-000000000000")


def test_url_live_without_an_open_workspace_errors(monkeypatch):
    _patch(monkeypatch, state={})
    r = runner.invoke(cli.app, ["url", "--live"])
    assert r.exit_code == 1


def test_url_live_and_a_selector_is_a_contradiction(monkeypatch):
    _patch(monkeypatch, state={"workspace_id": OTHER})
    r = runner.invoke(cli.app, ["url", "--live", "hi + cig"])
    assert r.exit_code == 1
    assert "mutually exclusive" in r.output


def test_url_json_carries_the_instance_that_was_discovered(monkeypatch):
    """Which of several running servers holds what you just read — the question
    `ps aux | grep tinkerscope` can't answer."""
    _patch(monkeypatch)
    r = runner.invoke(cli.app, ["url", "hi + cig", "--json"])
    assert r.exit_code == 0
    import json

    got = json.loads(r.stdout)
    assert got["base_url"] == BASE
    assert got["url"] == f"{BASE}/?w={WID}"
    assert got["workspace_id"] == WID
    assert got["workspace_name"] == "hi + cigarettes"
    assert got["pid"] == 42


# ---------- positional-or---ws selector ----------


def test_ws_accepts_the_selector_as_an_option(monkeypatch):
    _patch(monkeypatch)
    positional = runner.invoke(cli.app, ["ws", "hi + cig"])
    option = runner.invoke(cli.app, ["ws", "--ws", "hi + cig"])
    assert positional.exit_code == option.exit_code == 0
    assert positional.stdout == option.stdout


def test_samples_accepts_the_selector_as_an_option(monkeypatch):
    """The actual bug: `samples --ws <id>` died with 'No such option: --ws'
    while `grep`/`node` had taken `--ws` all along."""
    _patch(monkeypatch)
    positional = runner.invoke(cli.app, ["samples", "hi + cig"])
    option = runner.invoke(cli.app, ["samples", "--ws", "hi + cig"])
    assert positional.exit_code == option.exit_code == 0
    assert positional.stdout == option.stdout


def test_two_different_workspaces_is_an_error_not_a_preference(monkeypatch):
    _patch(monkeypatch)
    r = runner.invoke(cli.app, ["ws", "hi + cig", "--ws", "value guarding"])
    assert r.exit_code == 1
    assert "two different workspaces" in r.output


def test_the_same_workspace_twice_is_harmless(monkeypatch):
    _patch(monkeypatch)
    r = runner.invoke(cli.app, ["ws", WID, "--ws", WID])
    assert r.exit_code == 0
    assert "hi + cigarettes" in r.stdout


# ---------- state header ----------


def test_state_header_shows_the_url_and_a_link_to_the_open_workspace(monkeypatch):
    _patch(monkeypatch, state={"workspace_id": WID, "panels": [], "running": False})
    r = runner.invoke(cli.app, ["state"])
    assert r.exit_code == 0
    assert BASE in r.stdout
    assert f"{BASE}/?w={WID}" in r.stdout


def test_state_no_link_still_gives_a_usable_link(monkeypatch):
    """--no-link skips the workspaces FETCH (so the name is unresolved), but the
    link needs only the pushed id — and that's exactly when you want it."""
    _patch(monkeypatch, state={"workspace_id": WID, "panels": [], "running": False})
    r = runner.invoke(cli.app, ["state", "--no-link"])
    assert r.exit_code == 0
    assert f"{BASE}/?w={WID}" in r.stdout
