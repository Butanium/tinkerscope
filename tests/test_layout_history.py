"""Panel-layout history: every layout CHANGE is recorded, and restorable.

The safety net added after the 2026-07-24 cross-tab clobber, which replaced four
live workspaces' panel layouts in place with no undo (recovery meant forensics
over node blobs — `scripts/repair_panel_layouts.py`). These tests pin the three
properties that make the net worth having: it records changes, it does NOT record
noise (a tree save that leaves the layout alone), and what it records is exactly
what a PATCH can put back.

No remote calls — the `client` fixture stubs discovery and points state at a tmp dir.
"""
from __future__ import annotations

from tinkerscope.api import workspace_store as store

TREE = {"nodes": {}, "rootChildren": [], "selected": {}}


def _panels(*specs: tuple[str, str]) -> list[dict]:
    return [{"id": pid, "run_id": run, "checkpoint": "final"} for pid, run in specs]


def _create(client, name: str, panels: list[dict]) -> str:
    r = client.post("/api/workspaces", json={"name": name, "trees": {"primary": TREE}, "panels": panels})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _history(client, cid: str) -> list[dict]:
    r = client.get(f"/api/workspaces/{cid}/layout-history")
    assert r.status_code == 200, r.text
    return r.json()


# ── recording ────────────────────────────────────────────────────────────────
def test_create_records_the_initial_layout(client):
    cid = _create(client, "W", _panels(("primary", "run-a")))
    hist = _history(client, cid)
    assert [h["panels"] for h in hist] == [_panels(("primary", "run-a"))]
    assert hist[0]["ts"], "every entry is timestamped"


def test_patch_records_each_layout_change(client):
    cid = _create(client, "W", _panels(("primary", "run-a")))
    client.patch(f"/api/workspaces/{cid}", json={"panels": _panels(("primary", "run-b"))})
    client.patch(f"/api/workspaces/{cid}", json={"panels": _panels(("primary", "run-c"))})
    assert [h["panels"][0]["run_id"] for h in _history(client, cid)] == ["run-a", "run-b", "run-c"]


def test_non_layout_saves_record_nothing(client):
    """The hot path is tree saves; they must not spam the history."""
    panels = _panels(("primary", "run-a"))
    cid = _create(client, "W", panels)
    before = len(_history(client, cid))
    client.put(f"/api/workspaces/{cid}/tree", json={"trees": {"primary": TREE}, "panels": panels})
    client.patch(f"/api/workspaces/{cid}", json={"name": "renamed"})  # rename ≠ layout change
    client.patch(f"/api/workspaces/{cid}", json={"panels": panels})   # same layout re-sent
    assert len(_history(client, cid)) == before


def test_tree_save_that_changes_the_layout_is_recorded(client):
    """A PUT carries `panels` too — a model swap can land through the tree path."""
    cid = _create(client, "W", _panels(("primary", "run-a")))
    client.put(f"/api/workspaces/{cid}/tree", json={
        "trees": {"primary": TREE}, "panels": _panels(("primary", "run-z"))})
    assert [h["panels"][0]["run_id"] for h in _history(client, cid)] == ["run-a", "run-z"]


def test_history_is_per_workspace(client):
    a = _create(client, "A", _panels(("primary", "run-a")))
    b = _create(client, "B", _panels(("primary", "run-b")))
    client.patch(f"/api/workspaces/{a}", json={"panels": _panels(("primary", "run-a2"))})
    assert [h["panels"][0]["run_id"] for h in _history(client, a)] == ["run-a", "run-a2"]
    assert [h["panels"][0]["run_id"] for h in _history(client, b)] == ["run-b"]


def test_history_survives_workspace_reads_and_dies_with_the_workspace(client):
    cid = _create(client, "W", _panels(("primary", "run-a")))
    client.patch(f"/api/workspaces/{cid}", json={"panels": _panels(("primary", "run-b"))})
    assert len(_history(client, cid)) == 2
    client.delete(f"/api/workspaces/{cid}")
    assert _history(client, cid) == []
    assert not store._layouts_file(cid).exists()


def test_unknown_workspace_has_empty_history(client):
    assert _history(client, "no-such-workspace") == []


def test_history_is_capped(client):
    """Bounded growth: the file is trimmed back to the cap once it drifts past 2x."""
    cid = _create(client, "W", _panels(("primary", "run-0")))
    for i in range(1, store._LAYOUT_HISTORY_MAX * 2 + 5):
        client.patch(f"/api/workspaces/{cid}", json={"panels": _panels(("primary", f"run-{i}"))})
    hist = _history(client, cid)
    # Trim is amortized (fires at 2x, cuts back to 1x), so the bound is 2x, not 1x.
    assert store._LAYOUT_HISTORY_MAX <= len(hist) <= store._LAYOUT_HISTORY_MAX * 2
    # Trimming keeps the NEWEST entries — the ones a restore would want.
    assert hist[-1]["panels"][0]["run_id"] == f"run-{store._LAYOUT_HISTORY_MAX * 2 + 4}"


def test_torn_line_does_not_hide_the_rest(client):
    """A crash mid-append leaves a partial line; the readable entries still load."""
    cid = _create(client, "W", _panels(("primary", "run-a")))
    with store._layouts_file(cid).open("a") as fh:
        fh.write('{"ts": "2026-01-01T00:00:00Z", "panels": [')  # no newline, no close
    client.patch(f"/api/workspaces/{cid}", json={"panels": _panels(("primary", "run-b"))})
    assert [h["panels"][0]["run_id"] for h in _history(client, cid)] == ["run-a", "run-b"]


# ── restore ──────────────────────────────────────────────────────────────────
def test_a_recorded_layout_patches_back_verbatim(client):
    """The point of the whole feature: an accident is a PATCH away from undone."""
    good = _panels(("primary", "run-a"), ("compare", "run-b"))
    cid = _create(client, "W", good)
    client.patch(f"/api/workspaces/{cid}", json={"panels": _panels(("primary", "wrong"), ("compare", "alsowrong"))})
    assert client.get(f"/api/workspaces/{cid}").json()["panels"] != good

    recorded = _history(client, cid)[0]["panels"]
    client.patch(f"/api/workspaces/{cid}", json={"panels": recorded})
    assert client.get(f"/api/workspaces/{cid}").json()["panels"] == good


# ── the tripwire ─────────────────────────────────────────────────────────────
def test_tripwire_fires_on_a_wholesale_replacement():
    """The clobber shape: every panel's model swapped at once, both sides non-trivial."""
    old = _panels(("primary", "run-a"), ("compare", "run-b"))
    new = _panels(("primary", "run-x"), ("compare", "run-y"))
    assert store._suspicious_layout_change(old, new)


def test_tripwire_stays_quiet_on_human_shaped_changes():
    a, b, c = "run-a", "run-b", "run-c"
    two = _panels(("primary", a), ("compare", b))
    # swapping ONE panel keeps an overlap
    assert not store._suspicious_layout_change(two, _panels(("primary", a), ("compare", c)))
    # adding a panel
    assert not store._suspicious_layout_change(two, _panels(("primary", a), ("compare", b), ("p-2", c)))
    # single-panel workspaces are always below the bar
    assert not store._suspicious_layout_change(_panels(("primary", a)), _panels(("primary", b)))
    # filling in a blank layout (panels present but no models yet)
    blank = [{"id": "primary", "run_id": None, "checkpoint": None},
             {"id": "compare", "run_id": None, "checkpoint": None}]
    assert not store._suspicious_layout_change(blank, two)
    # a reorder is not a change of models at all
    assert not store._suspicious_layout_change(two, _panels(("compare", b), ("primary", a)))
