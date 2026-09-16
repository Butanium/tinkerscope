"""The op ENDPOINT + persistence half of server-authoritative trees.

`POST /api/workspaces/{id}/ops`, the per-workspace `rev`, and the bus `ops`
broadcast. Op SEMANTICS live in `test_tree_ops.py` (pure); this file is about
what reaches disk, what reaches the bus, and what `rev` does across every write
channel. See `docs/HANDOFF_SERVER_AUTHORITY.md` §4.1–4.2.
"""
from __future__ import annotations

import json

import pytest

from tinkerscope.api import workspace_store as store

ROOT = "__root__"
HEAVY_LOGPROBS = [{"t": "Hi", "tid": 5, "lp": -0.1, "top": [["Hi", 5, -0.1]]}]


@pytest.fixture
def bus_events(client, monkeypatch):
    """Capture what the router fans out. Depends on `client` so the conftest
    module reload has already happened — patching before it would be undone."""
    import tinkerscope.api.routes.workspaces as route

    events: list[tuple[str, dict]] = []

    async def fake_broadcast_all(event: str, payload: dict) -> None:
        events.append((event, payload))

    # Store events go to EVERY session (state.broadcast_all), not one bus.
    monkeypatch.setattr(route, "broadcast_all", fake_broadcast_all)
    return events


def _new_ws(client, **kw) -> str:
    body = {"name": "W", "trees": {"primary": {"nodes": {}, "rootChildren": [], "selected": {}}}}
    body.update(kw)
    return client.post("/api/workspaces", json=body).json()["id"]


def _ops(client, cid: str, *ops: dict):
    return client.post(f"/api/workspaces/{cid}/ops", json={"ops": list(ops)})


def _add(panel: str, nid: str, role: str, content: str, parent: str | None, **extra) -> dict:
    return {"op": "add_nodes", "panel": panel, "select": True,
            "nodes": [{"id": nid, "role": role, "content": content, "parent": parent, **extra}]}


# ── the endpoint ─────────────────────────────────────────────────────────────
def test_ops_apply_and_persist(client):
    cid = _new_ws(client)
    r = _ops(client, cid,
             _add("primary", "u1", "user", "hi", None),
             _add("primary", "a1", "assistant", "hello", "u1"))
    assert r.status_code == 200
    assert r.json()["results"] == [{"ok": True, "noop": False}, {"ok": True, "noop": False}]

    tree = client.get(f"/api/workspaces/{cid}").json()["trees"]["primary"]
    assert tree["rootChildren"] == ["u1"]
    assert tree["nodes"]["u1"]["children"] == ["a1"]
    assert tree["selected"] == {ROOT: "u1", "u1": "a1"}


def test_ops_on_unknown_workspace_is_404(client):
    assert _ops(client, "nope", _add("primary", "u1", "user", "hi", None)).status_code == 404


def test_a_rejected_batch_writes_nothing_and_leaves_rev_alone(client):
    cid = _new_ws(client)
    _ops(client, cid, _add("primary", "u1", "user", "hi", None))
    before = client.get(f"/api/workspaces/{cid}").json()

    r = _ops(client, cid,
             _add("primary", "a1", "assistant", "would be fine", "u1"),
             _add("primary", "x1", "user", "orphan", "deleted-parent"))
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["index"] == 1 and "does not exist" in detail["error"]

    after = client.get(f"/api/workspaces/{cid}").json()
    assert after["trees"] == before["trees"], "the earlier op in the batch must not survive"
    assert after["rev"] == before["rev"]


def test_replaying_a_batch_over_http_is_free(client, bus_events):
    """The dropped-POST retry: the client re-sends the same batch and the server
    neither writes, nor bumps `rev`, nor wakes the other tabs."""
    cid = _new_ws(client)
    batch = [_add("primary", "u1", "user", "hi", None)]
    first = _ops(client, cid, *batch).json()
    bus_events.clear()

    second = _ops(client, cid, *batch).json()
    assert second["rev"] == first["rev"]
    assert second["results"] == [{"ok": True, "noop": True}]
    assert bus_events == []


# ── the broadcast ────────────────────────────────────────────────────────────
def test_a_noop_add_writes_nothing_at_all(client, bus_events):
    """The re-append rule touches a deep copy of the tree on every `add_nodes`, so
    it is worth pinning that a batch which nets to nothing reaches DISK as nothing:
    no rev bump, no event, and — since the trash journal hooks `_persist` — no
    journal entry either."""
    cid = _new_ws(client)
    _ops(client, cid,
         _add("primary", "u1", "user", "hi", None),
         _add("primary", "a1", "assistant", "hello", "u1"))
    before = client.get(f"/api/workspaces/{cid}").json()
    trash_before = client.get(f"/api/workspaces/{cid}/trash").json()
    bus_events.clear()

    # a1 is already its parent's last child and already selected ⇒ nothing moves
    r = _ops(client, cid, _add("primary", "a1", "assistant", "hello", "u1"))
    assert r.json()["results"] == [{"ok": True, "noop": True}]
    assert bus_events == []
    after = client.get(f"/api/workspaces/{cid}").json()
    assert after["rev"] == before["rev"]
    assert after["updated_at"] == before["updated_at"]
    assert client.get(f"/api/workspaces/{cid}/trash").json() == trash_before


def test_a_replay_repairs_a_node_whose_blob_never_landed(client):
    """End-to-end of the draft-materialize race: the workspace was created with an
    already-lightened node (flag set, no blob), and the op replay carrying the real
    data lands on the existing node. The blob must be written even though the batch
    changes no tree bytes — the store writes blobs BEFORE the no-change bail-out,
    which is what makes a retry the repair path for a dangling flag."""
    flagged = {"nodes": {"a1": {"id": "a1", "role": "assistant", "content": "hello",
                                "parent": None, "children": [], "has_token_logprobs": True}},
               "rootChildren": ["a1"], "selected": {ROOT: "a1"}}
    cid = client.post("/api/workspaces", json={"name": "W", "trees": {"primary": flagged}}).json()["id"]
    assert client.post(f"/api/workspaces/{cid}/node-blobs", json={"nodes": ["a1"]}).json() == {}

    rev = client.get(f"/api/workspaces/{cid}").json()["rev"]
    r = _ops(client, cid, _add("primary", "a1", "assistant", "hello", None,
                               token_logprobs=HEAVY_LOGPROBS))
    assert r.json()["results"] == [{"ok": True, "noop": True}]
    assert client.get(f"/api/workspaces/{cid}").json()["rev"] == rev  # nothing structural moved
    blobs = client.post(f"/api/workspaces/{cid}/node-blobs", json={"nodes": ["a1"]}).json()
    assert blobs["a1"]["token_logprobs"] == HEAVY_LOGPROBS


def test_deleting_a_workspace_tells_the_mirrors(client, bus_events):
    """A deletion has no `rev` to ride — the workspace it would belong to is gone —
    so it gets its own named event. Without it a tab holding this workspace open
    learns nothing and its next refetch 404s, indistinguishable from server trouble."""
    cid = _new_ws(client)
    bus_events.clear()
    assert client.delete(f"/api/workspaces/{cid}").status_code == 200
    assert bus_events == [("workspace_deleted", {"workspace": cid})]

    bus_events.clear()
    assert client.delete(f"/api/workspaces/{cid}").status_code == 404
    assert bus_events == [], "a 404 must not announce a deletion that did not happen"


def test_reusing_an_id_under_a_different_parent_is_rejected(client):
    """A node's identity is role+content+parent. Without the parent check this is a
    silent skip, and the re-append then finds the id absent from the op's parent and
    quietly does nothing — a client bug that leaves no trace anywhere."""
    cid = _new_ws(client)
    _ops(client, cid,
         _add("primary", "u1", "user", "hi", None),
         _add("primary", "a1", "assistant", "hello", "u1"),
         _add("primary", "u2", "user", "again", "a1"))
    r = _ops(client, cid, _add("primary", "a1", "assistant", "hello", "u2"))
    assert r.status_code == 409
    assert "different role/content/parent" in r.json()["detail"]["error"]


def test_broadcast_carries_light_nodes_and_the_new_rev(client, bus_events):
    """Heavy fields go to write-once blobs on the way in; what the mirrors receive
    is the light node + its `has_*` flags, exactly like a loaded tree."""
    cid = _new_ws(client)
    _ops(client, cid, _add("primary", "u1", "user", "hi", None))
    bus_events.clear()

    r = _ops(client, cid, _add("primary", "a1", "assistant", "hello", "u1",
                               token_logprobs=HEAVY_LOGPROBS, raw_meta='{"request": {}}'))
    (event, payload), = bus_events
    assert event == "ops"
    assert payload["workspace"] == cid
    assert payload["rev"] == r.json()["rev"]
    node = payload["ops"][0]["nodes"][0]
    assert "token_logprobs" not in node and "raw_meta" not in node
    assert node["has_token_logprobs"] is True and node["has_raw_meta"] is True

    blobs = client.post(f"/api/workspaces/{cid}/node-blobs", json={"nodes": ["a1"]}).json()
    assert blobs["a1"]["token_logprobs"] == HEAVY_LOGPROBS


def test_patch_broadcasts_set_meta_with_the_MERGED_values(client, bus_events):
    """PATCH is sugar over the op, so tabs converge on metadata live — and on the
    value that WON the merge, not the staler one that was sent."""
    cid = _new_ws(client)
    client.patch(f"/api/workspaces/{cid}", json={"panel_seq": 7, "seen_panels": ["p-1"]})
    bus_events.clear()

    summary = client.patch(f"/api/workspaces/{cid}",
                           json={"name": "renamed", "panel_seq": 3, "seen_panels": ["p-2"]}).json()
    (event, payload), = bus_events
    assert event == "ops"
    fields = payload["ops"][0]["fields"]
    assert payload["ops"][0]["op"] == "set_meta"
    assert fields["name"] == "renamed"
    assert fields["panel_seq"] == 7          # monotone — the stale 3 loses
    assert fields["seen_panels"] == ["p-1", "p-2"]  # union
    assert summary["rev"] == payload["rev"]


def test_the_broadcast_echoes_what_was_STORED_not_what_was_SENT(client, bus_events):
    """Every field the server ADJUSTS on the way in goes out post-adjustment.

    A mirror always-applies the batch it receives (§4.2), so a broadcast carrying
    the SUBMITTED values converges every tab on the un-normalized form and leaves
    them there — no rev gap ever fires, because the rev is perfectly correct. The
    three adjusted fields: `panels` (phantom heal), `panel_seq` (max-merge),
    `seen_panels` (union)."""
    cid = _new_ws(client)
    client.patch(f"/api/workspaces/{cid}", json={"panel_seq": 9, "seen_panels": ["p-1"]})
    bus_events.clear()

    _ops(client, cid,
         _add("p-9", "u1", "user", "carried over", None),
         {"op": "set_meta", "fields": {
             "panels": [
                 {"id": "p-9", "run_id": None, "checkpoint": None},   # blank but HAS a tree ⇒ kept
                 {"id": "p-77", "run_id": None, "checkpoint": None},  # blank, no tree ⇒ dropped
             ],
             "panel_seq": 2,           # staler than the stored 9
             "seen_panels": ["p-9"],   # a subset of the ledger
         }})

    (event, payload), = bus_events
    stored = client.get(f"/api/workspaces/{cid}").json()
    fields = payload["ops"][-1]["fields"]
    for key, value in fields.items():
        assert value == stored[key], f"broadcast {key} is not what was stored"
    # ...and each of those genuinely differs from what the client sent.
    assert [p["id"] for p in fields["panels"]] == ["p-9"]
    assert fields["panel_seq"] == 9
    assert fields["seen_panels"] == ["p-1", "p-9"]
    assert payload["rev"] == stored["rev"]


def test_the_broadcast_tree_is_the_stored_light_tree(client, bus_events):
    """Same rule one op over: `replace_tree` splits heavy fields out and coerces
    the shape, so the event has to carry the tree that LANDED."""
    cid = _new_ws(client)
    bus_events.clear()
    client.post(f"/api/workspaces/{cid}/ops", json={"ops": [{
        "op": "replace_tree", "panel": "primary", "tree": {
            "nodes": {"n1": {"id": "n1", "role": "assistant", "content": "x", "parent": None,
                             "children": [], "token_logprobs": HEAVY_LOGPROBS}},
            "rootChildren": ["n1"], "selected": {},
        }}]})
    (_event, payload), = bus_events
    stored = client.get(f"/api/workspaces/{cid}").json()["trees"]["primary"]
    assert payload["ops"][0]["tree"] == stored
    assert "token_logprobs" not in stored["nodes"]["n1"]
    assert stored["nodes"]["n1"]["has_token_logprobs"] is True


def test_a_patch_that_changes_nothing_is_silent(client, bus_events):
    cid = _new_ws(client, name="Same")
    rev = client.get(f"/api/workspaces/{cid}").json()["rev"]
    bus_events.clear()
    client.patch(f"/api/workspaces/{cid}", json={"name": "Same"})
    assert bus_events == []
    assert client.get(f"/api/workspaces/{cid}").json()["rev"] == rev


# ── rev ──────────────────────────────────────────────────────────────────────
def test_rev_is_monotonic_across_every_write_channel(client):
    """§2d.1: `rev` bumps in `_persist`, the choke point, precisely so that a
    channel nobody thought about — a pack install, a trash restore — can't move a
    workspace behind the mirrors' backs."""
    cid = _new_ws(client)
    seen = [client.get(f"/api/workspaces/{cid}").json()["rev"]]

    def bump(label: str, fn):
        fn()
        rev = client.get(f"/api/workspaces/{cid}").json()["rev"]
        assert rev > seen[-1], f"{label} did not bump rev"
        seen.append(rev)

    bump("ops", lambda: _ops(client, cid,
                             _add("primary", "u1", "user", "hi", None),
                             _add("primary", "a1", "assistant", "hello", "u1")))
    bump("PATCH", lambda: client.patch(f"/api/workspaces/{cid}", json={"name": "renamed"}))
    # (the PUT /tree channel retired with P3 — its wholesale semantics ride
    # replace_tree ops now, already covered by the "ops" row above)
    # create-by-id IS the upsert the pack-apply path uses.
    bump("upsert (pack apply)", lambda: client.post("/api/workspaces", json={
        "id": cid, "name": "from a pack", "trees": {"primary": {"nodes": {}, "rootChildren": [], "selected": {}}}}))
    bump("trash restore", lambda: client.post(
        f"/api/workspaces/{cid}/trash/restore",
        json={"handle": client.get(f"/api/workspaces/{cid}/trash").json()[0]["id"]}))
    assert seen == sorted(seen) and len(set(seen)) == len(seen)


def test_a_legacy_file_reads_rev_0_and_its_first_write_lands_at_1(client):
    """Nothing on disk has a `rev` yet, so a mirror opening a never-written
    workspace must see 0 and then an ordinary first event — not a gap."""
    cid = _new_ws(client)
    body = client.get(f"/api/workspaces/{cid}").json()
    store._ws_file(cid).write_text(json.dumps({k: v for k, v in body.items() if k != "rev"}))
    store.reset_cache()

    assert client.get(f"/api/workspaces/{cid}").json()["rev"] == 0
    assert [s["rev"] for s in client.get("/api/workspaces").json()] == [0]
    r = _ops(client, cid, _add("primary", "u1", "user", "hi", None))
    assert r.json()["rev"] == 1


def test_rev_survives_an_upsert_that_rebuilds_the_body(client):
    """`upsert` builds its entry from kwargs, so it has to carry the stored rev
    forward — otherwise a pack install restarts the counter and every attached
    mirror sees its workspace jump BACKWARDS."""
    cid = _new_ws(client)
    for _ in range(3):
        client.patch(f"/api/workspaces/{cid}", json={"name": f"n{_}"})
    before = client.get(f"/api/workspaces/{cid}").json()["rev"]
    client.post("/api/workspaces", json={"id": cid, "name": "reinstalled", "trees": {}})
    assert client.get(f"/api/workspaces/{cid}").json()["rev"] == before + 1


# ── legacy bodies + the phantom heal ─────────────────────────────────────────
def test_legacy_body_normalizes_on_its_first_op(client):
    """A migrated `{tree, compare_tree}` workspace folds into `trees` before the
    first op applies — the browser's full-map first save has no equivalent under
    ops, so the fold moved to the side that owns the file."""
    cid = _new_ws(client)
    body = store.get_body(cid)
    body = {k: v for k, v in body.items() if k != "trees"}
    body["tree"] = {"nodes": {}, "rootChildren": [], "selected": {}}
    body["compare_tree"] = {
        "nodes": {"c1": {"id": "c1", "role": "user", "content": "keep me", "parent": None, "children": []}},
        "rootChildren": ["c1"], "selected": {},
    }
    store._persist(body)

    _ops(client, cid, _add("primary", "u1", "user", "hi", None))
    healed = client.get(f"/api/workspaces/{cid}").json()
    assert "tree" not in healed and "compare_tree" not in healed
    assert healed["trees"]["compare"]["nodes"]["c1"]["content"] == "keep me"
    assert healed["trees"]["primary"]["rootChildren"] == ["u1"]


def test_phantom_panel_rows_are_dropped_when_the_layout_is_set(client):
    """The heal the browser used to run on load. With the layout
    server-authoritative it has to happen where the layout is WRITTEN, or a stored
    phantom resurrects on the next open."""
    cid = _new_ws(client)
    _ops(client, cid, {"op": "set_meta", "fields": {"panels": [
        {"id": "primary", "run_id": "run-a", "checkpoint": "final"},
        {"id": "p-9", "run_id": None, "checkpoint": None},
    ]}})
    assert [p["id"] for p in client.get(f"/api/workspaces/{cid}").json()["panels"]] == ["primary"]


def test_a_blank_panel_that_holds_content_is_kept(client):
    """Narrower than the browser's filter on purpose: the server is the only copy,
    and an unbound column with a tree in it is a real state (send-branch-to-panel,
    trash restore)."""
    cid = _new_ws(client)
    _ops(client, cid,
         _add("p-9", "u1", "user", "carried over", None),
         {"op": "set_meta", "fields": {"panels": [{"id": "p-9", "run_id": None, "checkpoint": None}]}})
    got = client.get(f"/api/workspaces/{cid}").json()
    assert [p["id"] for p in got["panels"]] == ["p-9"]
    assert got["trees"]["p-9"]["rootChildren"] == ["u1"]


# ── deletion still journals ──────────────────────────────────────────────────
def test_an_op_deletion_is_journaled_to_the_trash(client):
    """The trash diff hooks `_persist`, so it covers the op path for free — worth
    pinning, since ops are now the ONLY way a node disappears."""
    cid = _new_ws(client)
    _ops(client, cid,
         _add("primary", "u1", "user", "hi", None),
         _add("primary", "a1", "assistant", "hello", "u1"))
    _ops(client, cid, {"op": "delete", "panel": "primary", "node_id": "a1"})

    entries = client.get(f"/api/workspaces/{cid}/trash").json()
    assert entries and entries[0]["count"] == 1
    assert [r["id"] for r in entries[0]["roots"]] == ["a1"]

    client.post(f"/api/workspaces/{cid}/trash/restore", json={"handle": "a1"})
    tree = client.get(f"/api/workspaces/{cid}").json()["trees"]["primary"]
    assert "a1" in tree["nodes"] and tree["nodes"]["u1"]["children"] == ["a1"]
