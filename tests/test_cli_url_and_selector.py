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
        {"id": WID, "name": "probe sweep", "trees": {"primary": _tree()}, "updated_at": "2026-08-10T00:00:00"},
        {"id": OTHER, "name": "second workspace", "trees": {"primary": _tree()}, "updated_at": "2026-08-09T00:00:00"},
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
    r = runner.invoke(cli.app, ["url", "probe swe"])
    assert r.exit_code == 0
    assert r.stdout.strip() == f"{BASE}/?w={WID}"


def test_url_keeps_the_workspace_name_off_stdout(monkeypatch):
    _patch(monkeypatch)
    r = runner.invoke(cli.app, ["url", "probe swe"])
    assert "probe sweep" not in r.stdout
    assert "probe sweep" in r.stderr


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
    r = runner.invoke(cli.app, ["url", "--live", "probe swe"])
    assert r.exit_code == 1
    assert "mutually exclusive" in r.output


def test_url_json_carries_the_instance_that_was_discovered(monkeypatch):
    """Which of several running servers holds what you just read — the question
    `ps aux | grep tinkerscope` can't answer."""
    _patch(monkeypatch)
    r = runner.invoke(cli.app, ["url", "probe swe", "--json"])
    assert r.exit_code == 0
    import json

    got = json.loads(r.stdout)
    assert got["base_url"] == BASE
    assert got["url"] == f"{BASE}/?w={WID}"
    assert got["workspace_id"] == WID
    assert got["workspace_name"] == "probe sweep"
    assert got["pid"] == 42


# ---------- positional-or---ws selector ----------


def test_ws_accepts_the_selector_as_an_option(monkeypatch):
    _patch(monkeypatch)
    positional = runner.invoke(cli.app, ["ws", "probe swe"])
    option = runner.invoke(cli.app, ["ws", "--ws", "probe swe"])
    assert positional.exit_code == option.exit_code == 0
    assert positional.stdout == option.stdout


def test_samples_accepts_the_selector_as_an_option(monkeypatch):
    """The actual bug: `samples --ws <id>` died with 'No such option: --ws'
    while `grep`/`node` had taken `--ws` all along."""
    _patch(monkeypatch)
    positional = runner.invoke(cli.app, ["samples", "probe swe"])
    option = runner.invoke(cli.app, ["samples", "--ws", "probe swe"])
    assert positional.exit_code == option.exit_code == 0
    assert positional.stdout == option.stdout


def test_two_different_workspaces_is_an_error_not_a_preference(monkeypatch):
    _patch(monkeypatch)
    r = runner.invoke(cli.app, ["ws", "probe swe", "--ws", "second workspace"])
    assert r.exit_code == 1
    assert "two different workspaces" in r.output


def test_the_same_workspace_twice_is_harmless(monkeypatch):
    _patch(monkeypatch)
    r = runner.invoke(cli.app, ["ws", WID, "--ws", WID])
    assert r.exit_code == 0
    assert "probe sweep" in r.stdout


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


# ---------- node handles (`<panel>:<node>`) ----------
# The browser's Copy-node-id button emits `<panel>:<node>`, because a tree cloned
# into another panel keeps its node ids — a bare id names the same turn in N panels,
# and the write commands then refuse until you add --panel by hand.


def test_a_bare_node_id_still_parses(monkeypatch):
    assert cli._split_node_handle("n4f1") == (None, None, "n4f1")


def test_panel_node_splits_into_panel_and_node(monkeypatch):
    assert cli._split_node_handle("p-4:n4f1") == (None, "p-4", "n4f1")


def test_a_three_part_handle_carries_the_workspace(monkeypatch):
    assert cli._split_node_handle("ws8chars:p-4:n4f1") == ("ws8chars", "p-4", "n4f1")


def test_legacy_panel_names_work_as_handles(monkeypatch):
    """'primary'/'compare' are no longer minted but remain valid ids forever."""
    assert cli._split_node_handle("primary:nt03f1") == (None, "primary", "nt03f1")


def test_a_four_part_handle_is_an_error_not_a_guess(monkeypatch):
    _patch(monkeypatch)
    r = runner.invoke(cli.app, ["node", "a:b:c:d"])
    assert r.exit_code == 1
    assert "node handle" in r.output


def test_an_explicit_panel_beats_the_handles_panel(monkeypatch):
    """The handle is a convenience, not an override — `--node p-4:n1 --panel p-2`
    reads as a deliberate cross-panel aim."""
    assert cli._aim_at_node("p-4:n1", "p-2", None) == ("n1", "p-2", None)


def test_the_handle_fills_in_a_missing_panel_and_workspace(monkeypatch):
    assert cli._aim_at_node("ws1:p-4:n1", None, None) == ("n1", "p-4", "ws1")


def test_no_handle_leaves_the_flags_alone(monkeypatch):
    assert cli._aim_at_node(None, "p-2", "ws1") == (None, "p-2", "ws1")


# ---------- layout panel ids (open/chat/compare) ----------
# These commands REPLACE the layout, so the ids they name land in the open
# workspace. Naming them by position reissued retired ids, which breaks both a
# `<panel>:<node>` handle and `restore_trash` (it keys on panel id, so it would
# splice a retired column's branches into a new column bound to another model).


def _patch_layout(monkeypatch, *, live, ws=None):
    """Stub the two reads `_layout_panel_ids` makes, and capture any PATCH."""
    patched: list[dict] = []
    monkeypatch.setattr(cli, "_base_url", lambda: BASE)
    monkeypatch.setattr(cli, "_get", lambda path, **kw: {
        "panels": [{"id": p} for p in live],
        "workspace_id": (ws or {}).get("id"),
    })
    monkeypatch.setattr(cli, "_workspaces", lambda: [ws] if ws else [])
    monkeypatch.setattr(cli, "_patch_workspace", lambda cid, fields: patched.append(fields))
    return patched


def test_a_single_panel_fire_reuses_the_live_column(monkeypatch):
    """Repeated `tinkpg chat` must stay on ONE column — minting each time would
    abandon (and journal) the previous tree on every fire."""
    _patch_layout(monkeypatch, live=["p-7"], ws={"id": WID, "panel_seq": 7})
    assert cli._layout_panel_ids(1) == ["p-7"]


def test_a_reused_id_does_not_bump_the_counter(monkeypatch):
    patched = _patch_layout(monkeypatch, live=["p-7"], ws={"id": WID, "panel_seq": 7})
    cli._layout_panel_ids(1)
    assert patched == [], "nothing was minted, so nothing to claim"


def test_extra_positions_mint_above_the_workspace_counter(monkeypatch):
    _patch_layout(monkeypatch, live=["p-7"], ws={"id": WID, "panel_seq": 7})
    assert cli._layout_panel_ids(3) == ["p-7", "p-8", "p-9"]


def test_minting_claims_the_numbers_it_used(monkeypatch):
    patched = _patch_layout(monkeypatch, live=["p-7"], ws={"id": WID, "panel_seq": 7})
    cli._layout_panel_ids(3)
    assert patched == [{"panel_seq": 9}]


def test_a_closed_panels_number_is_never_reissued(monkeypatch):
    """THE case. p-9 was closed: absent from trees and layout, remembered only by
    seen_panels. A positional minter handed it straight back."""
    ws = {"id": WID, "panel_seq": 0,  # a writer that didn't know the field zeroed it
          "trees": {"p-7": {}}, "panels": [{"id": "p-7"}],
          "seen_panels": ["p-7", "p-8", "p-9"]}
    _patch_layout(monkeypatch, live=["p-7"], ws=ws)
    assert cli._layout_panel_ids(2) == ["p-7", "p-10"]


def test_a_zeroed_counter_cannot_hand_back_a_visible_id(monkeypatch):
    ws = {"id": WID, "panel_seq": 0, "trees": {"p-3": {}, "p-4": {}},
          "panels": [{"id": "p-3"}, {"id": "p-4"}]}
    _patch_layout(monkeypatch, live=[], ws=ws)
    assert cli._layout_panel_ids(1) == ["p-5"]


def test_legacy_reserved_names_are_reused_not_renamed(monkeypatch):
    """A pre-monotonic workspace keeps 'primary'/'compare' — a layout fire must not
    rename its live columns, and those names carry no number to mint above."""
    ws = {"id": WID, "panel_seq": 0, "trees": {"primary": {}, "compare": {}},
          "panels": [{"id": "primary"}, {"id": "compare"}], "seen_panels": ["primary", "compare"]}
    _patch_layout(monkeypatch, live=["primary", "compare"], ws=ws)
    assert cli._layout_panel_ids(3) == ["primary", "compare", "p-1"]


def test_no_workspace_open_starts_at_p1(monkeypatch):
    patched = _patch_layout(monkeypatch, live=[], ws=None)
    assert cli._layout_panel_ids(2) == ["p-1", "p-2"]
    assert patched == [], "no workspace to record the claim against"


# ── the default panel is the LEFTMOST column, not the first JSON key ──────────
def test_the_default_panel_follows_the_layout_not_the_tree_key_order():
    """With reserved names gone, `samples` with no --panel had no privileged id left
    to prefer and fell back to `trees`' key order — which is just how the file was
    last written. A restored column, a partial upsert or a hand edit reorders those
    keys without moving a single column on screen, so the CLI could answer with a
    different panel than the human is looking at. Ask the LAYOUT."""
    c = {"panels": [{"id": "p-2"}, {"id": "p-1"}], "trees": {"p-1": {}, "p-2": {}}}
    assert cli._panels_in_display_order(c, c["trees"]) == ["p-2", "p-1"]


def test_a_tree_with_no_layout_row_still_sorts_last_not_lost():
    """Legacy workspaces store no `panels` at all, and a tree can outlive its row
    (the stale-tab clobber). Neither may drop out of the ordering."""
    c = {"panels": [{"id": "p-2"}], "trees": {"p-1": {}, "p-2": {}, "p-3": {}}}
    assert cli._panels_in_display_order(c, c["trees"]) == ["p-2", "p-1", "p-3"]
    legacy = {"trees": {"primary": {}, "compare": {}}}
    assert cli._panels_in_display_order(legacy, legacy["trees"]) == ["primary", "compare"]


def test_a_layout_row_for_a_dropped_tree_is_not_offered():
    """`panels` can name a panel whose tree was dropped; returning it would make the
    caller index `trees` with a missing key."""
    c = {"panels": [{"id": "gone"}, {"id": "p-1"}], "trees": {"p-1": {}}}
    assert cli._panels_in_display_order(c, c["trees"]) == ["p-1"]


def test_samples_prints_the_user_turn_handle(monkeypatch):
    """The prompt line carries its handle, so `continue --node <user turn>` (a
    sibling fan under the same prompt) needs no workspace JSON."""
    _patch(monkeypatch)
    r = runner.invoke(cli.app, ["samples", "probe swe"])
    assert r.exit_code == 0
    assert "▸ prompt · aaaaaaaa:primary:u1\n" in r.stdout


def test_open_binds_a_model_selector_as_is(monkeypatch):
    """`open base:<model>` (and ckpt:/openrouter:/vllm:) binds the panel the way the
    browser's picker does — before, `open` only knew discovered runs and an agent
    had to POST /api/state by hand to point a panel at an OpenRouter model."""
    _patch(monkeypatch, state={"workspace_id": None, "panels": [{"id": "p-3"}], "running": False})
    posted = []
    monkeypatch.setattr(cli, "_post", lambda path, body=None: posted.append((path, body)) or {})
    for sel in ("base:Qwen/Qwen3-8B", "openrouter:qwen/qwen3-8b", "vllm:zero",
                "ckpt:tinker://abc:train:0/sampler_weights/final"):
        posted.clear()
        r = runner.invoke(cli.app, ["open", sel])
        assert r.exit_code == 0, r.output
        assert posted[-1][0] == "/api/state"
        (panel,) = posted[-1][1]["panels"]
        assert panel["run_id"] == sel and panel["checkpoint"] is None
