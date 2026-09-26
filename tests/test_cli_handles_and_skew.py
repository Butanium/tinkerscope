"""The CLI tells an agent where its samples landed, and why a server may not
have honored the fire.

Archive evidence (2026-09-16): an agent looped `tinkpg continue` and curl'ed the
workspace JSON after every fire to find the node ids it had just created —
`[done]` said nothing. And (2026-08-24) an editable-install CLI talking to a
server started before an update got "NOT persisted … no fold manifest" with no
pointer at the cause.
"""
from __future__ import annotations

import time
from pathlib import Path

from typer.testing import CliRunner

from tinkerscope import cli

runner = CliRunner()
WID = "aaaaaaaa-1111-2222-3333-444444444444"


def test_done_lines_print_paste_ready_handles():
    body = {"workspace_id": WID, "panel": "p-2", "parent_node": "u9"}
    done = {"folded": [{"sample_index": 1, "node_id": "n-b"}, {"sample_index": 0, "node_id": "n-a"}]}
    assert cli._done_lines(body, done) == [
        "[done] saved under user turn aaaaaaaa:p-2:u9",
        "  sample 0 → aaaaaaaa:p-2:n-a",
        "  sample 1 → aaaaaaaa:p-2:n-b",
    ]


def test_done_lines_bare_when_nothing_was_folded():
    assert cli._done_lines({"panel": "p-1"}, {}) == ["[done]"]
    assert cli._done_lines({"panel": "p-1", "parent_node": "u1"}, {"fold_error": "x"}) == ["[done]"]


class _FakeClient:
    def __init__(self, health):
        self.health = health

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, path):
        assert path == "/api/health"
        h = self.health

        class R:
            def json(self):
                return h
        return R()


def test_skew_hint_when_server_predates_the_code(monkeypatch):
    newest = max(f.stat().st_mtime for f in Path(cli.__file__).parent.rglob("*.py"))
    monkeypatch.setattr(cli, "_client", lambda: _FakeClient({"started_at": newest - 3600}))
    hint = cli._server_skew_hint()
    assert "restart it" in hint and "before this checkout's last Python change" in hint


def test_skew_hint_silent_for_a_fresh_server(monkeypatch):
    monkeypatch.setattr(cli, "_client", lambda: _FakeClient({"started_at": time.time() + 60}))
    assert cli._server_skew_hint() == ""


def test_skew_hint_for_a_server_without_version_reporting(monkeypatch):
    monkeypatch.setattr(cli, "_client", lambda: _FakeClient({"ok": True}))
    assert "predates version reporting" in cli._server_skew_hint()


def test_health_reports_version_and_start_time(client):
    h = client.get("/api/health").json()
    assert isinstance(h["started_at"], float) and h["started_at"] <= time.time()
    assert h["version"] and h["version"] != "0.1.0"  # the stale literal it replaced
