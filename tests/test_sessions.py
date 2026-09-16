"""`--multi-user`: the state bus is per SESSION (api/session.py + api/state.py).

What this pins: a single-user server ignores the session id entirely (the
pre-sessions wire, byte for byte); a multi-user server keeps one PlaygroundState
per id — params, selection, open workspace, running — while the SHARED store's
events (`ops`, `workspace_deleted`) reach every session; headerless requests
resolve to the one live session, else `default`, else 409; chat ids never
collide across sessions; a session's sidebar prefs key is its own.
"""
from __future__ import annotations

import asyncio

import pytest

from tinkerscope.api import state as state_mod
from tinkerscope.api.state import (
    BUS, DEFAULT_SESSION, broadcast_all, get_bus, last_session_key, list_sessions, live_sessions,
)

H = "x-tinkerscope-session"


def _as(sid: str) -> dict[str, str]:
    return {H: sid}


def _attach_fake_subscriber(sid: str) -> asyncio.Queue:
    """Pretend a browser holds this session's SSE stream open. Straight into the
    subscriber set (no bus lock): the TestClient's app loop lives in another
    thread, and taking an asyncio.Lock from this one would bind it there."""
    q: asyncio.Queue = asyncio.Queue()
    get_bus(sid)._subs.add(q)
    return q


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ── single-user: the id is ignored ───────────────────────────────────────────
def test_single_user_ignores_the_session_id(client):
    client.post("/api/state", json={"temperature": 0.2}, headers=_as("alice"))
    assert client.get("/api/state", headers=_as("bob")).json()["temperature"] == 0.2
    assert client.get("/api/state").json()["temperature"] == 0.2
    assert list(state_mod._BUSES) == [DEFAULT_SESSION], "no session bus may be minted in single-user mode"
    h = client.get("/api/health", headers=_as("alice")).json()
    assert h["multi_user"] is False and h["session"] == DEFAULT_SESSION
    assert [s["id"] for s in client.get("/api/sessions").json()] == [DEFAULT_SESSION]


# ── multi-user: one bus per id ───────────────────────────────────────────────
def test_multi_user_isolates_params_and_selection(multi_client):
    c = multi_client
    c.post("/api/state", json={"temperature": 0.2, "n_samples": 9}, headers=_as("alice"))
    c.post("/api/state", json={
        "workspace_id": "ws-a",
        "panels": [{"id": "p-1", "run_id": "run-a", "checkpoint": "final"}],
    }, headers=_as("alice"))
    bob = c.get("/api/state", headers=_as("bob")).json()
    assert bob["temperature"] == 1.0 and bob["n_samples"] == 1, "alice's params leaked into bob's sidebar"
    assert bob["workspace_id"] is None and bob["panels"][0]["run_id"] is None
    alice = c.get("/api/state", headers=_as("alice")).json()
    assert alice["temperature"] == 0.2 and alice["workspace_id"] == "ws-a"
    # bob's own claim doesn't move alice's open workspace either.
    c.post("/api/state", json={
        "workspace_id": "ws-b", "panels": [{"id": "p-1", "run_id": "run-b", "checkpoint": None}],
    }, headers=_as("bob"))
    assert c.get("/api/state", headers=_as("alice")).json()["workspace_id"] == "ws-a"
    assert c.get("/api/state", headers=_as("bob")).json()["workspace_id"] == "ws-b"
    assert {s["id"] for s in c.get("/api/sessions").json()} == {DEFAULT_SESSION, "alice", "bob"}


def test_health_echoes_the_resolved_session(multi_client):
    h = multi_client.get("/api/health", headers=_as("alice")).json()
    assert h["multi_user"] is True and h["session"] == "alice"
    # The EventSource can't set headers: the query form resolves the same way.
    assert multi_client.get("/api/health?session=bob").json()["session"] == "bob"


def test_invalid_session_id_is_a_400(multi_client):
    r = multi_client.get("/api/state", headers=_as("bad id!"))
    assert r.status_code == 400
    assert "session id" in r.json()["detail"]


# ── headerless resolution: one live → it; none → default; several → 409 ─────
def test_headerless_request_resolves_to_the_one_live_session(multi_client):
    c = multi_client
    assert c.get("/api/health").json()["session"] == DEFAULT_SESSION, "no browser attached ⇒ default"
    _attach_fake_subscriber("alice")
    assert c.get("/api/health").json()["session"] == "alice"
    # A bare `tinkpg params --temperature` lands on the human's sidebar.
    c.post("/api/state", json={"temperature": 0.3})
    assert c.get("/api/state", headers=_as("alice")).json()["temperature"] == 0.3
    assert c.get("/api/state", headers=_as(DEFAULT_SESSION)).json()["temperature"] == 1.0
    _attach_fake_subscriber("bob")
    r = c.get("/api/state")
    assert r.status_code == 409
    assert "--session" in r.json()["detail"] and "alice" in r.json()["detail"] and "bob" in r.json()["detail"]
    assert c.get("/api/health").json()["session"] is None, "health reports, never refuses"
    # Naming one still works while two are live.
    assert c.get("/api/state", headers=_as("bob")).status_code == 200
    assert [s["subscribers"] for s in c.get("/api/sessions").json() if s["id"] == "alice"] == [1]


# ── bus-level: session-local patches, process-wide store events ──────────────
@pytest.mark.asyncio
async def test_patches_stay_on_their_bus_but_store_events_reach_every_session(multi_backend):
    qa = await get_bus("alice").subscribe()
    qb = await get_bus("bob").subscribe()
    _drain(qa), _drain(qb)  # the initial snapshots
    await get_bus("alice").publish_state("patch", temperature=0.5)
    assert [m["type"] for m in _drain(qa)] == ["patch"]
    assert _drain(qb) == [], "alice's sidebar patch must not reach bob's mirror"
    await broadcast_all("ops", {"workspace": "w", "rev": 3, "ops": []})
    assert [m["type"] for m in _drain(qa)] == ["ops"]
    assert [m["type"] for m in _drain(qb)] == ["ops"]
    assert {b.id for b in live_sessions()} == {"alice", "bob"}


def test_workspace_ops_broadcast_reaches_every_session(multi_client):
    c = multi_client
    qa, qb = _attach_fake_subscriber("alice"), _attach_fake_subscriber("bob")
    cid = c.post("/api/workspaces", json={
        "name": "W", "trees": {"p-1": {"nodes": {}, "rootChildren": [], "selected": {}}},
    }).json()["id"]
    r = c.post(f"/api/workspaces/{cid}/ops", json={"ops": [{
        "op": "add_nodes", "panel": "p-1", "select": True,
        "nodes": [{"id": "u1", "role": "user", "content": "hi", "parent": None}],
    }]}, headers=_as("alice"))
    assert r.status_code == 200
    for q in (qa, qb):
        evs = _drain(q)
        assert [e["type"] for e in evs] == ["ops"], evs
        assert evs[0]["workspace"] == cid
    c.delete(f"/api/workspaces/{cid}", headers=_as("bob"))
    for q in (qa, qb):
        assert [e["type"] for e in _drain(q)] == ["workspace_deleted"]


@pytest.mark.asyncio
async def test_chat_ids_never_collide_across_sessions(multi_backend):
    a = await get_bus("alice").alloc_chat_id()
    b = await get_bus("bob").alloc_chat_id()
    d = await BUS.chat_begin()
    await BUS.chat_end()
    assert len({a, b, d}) == 3
    assert get_bus("alice").state.chat_id == a and get_bus("bob").state.chat_id == b
    assert BUS.state.running is False


# ── per-session sidebar prefs ────────────────────────────────────────────────
def test_last_session_key_and_seed_fallback(multi_client):
    assert last_session_key(DEFAULT_SESSION) == "last_session"
    assert last_session_key("alice") == "last_session@alice"
    c = multi_client
    c.put("/api/prefs/last_session", json={"value": '{"temperature": 0.7, "n_samples": 4}'})
    c.put("/api/prefs/last_session@alice", json={"value": '{"temperature": 0.1}'})
    # A brand-new session seeds from ITS key; one that never persisted falls
    # back to the instance's `last_session`.
    alice = c.get("/api/state", headers=_as("alice")).json()
    assert alice["temperature"] == 0.1 and alice["n_samples"] == 1
    bob = c.get("/api/state", headers=_as("bob")).json()
    assert bob["temperature"] == 0.7 and bob["n_samples"] == 4


def test_sessions_listing_shape(multi_client):
    c = multi_client
    _attach_fake_subscriber("alice")
    c.post("/api/state", json={"workspace_id": "ws-a", "panels": [{"id": "p-1", "run_id": "r", "checkpoint": None}]},
           headers=_as("alice"))
    rows = {s["id"]: s for s in c.get("/api/sessions").json()}
    assert rows["alice"]["subscribers"] == 1 and rows["alice"]["workspace_id"] == "ws-a"
    assert rows["alice"]["running"] is False and rows["alice"]["last_seen"] > 0
    assert rows[DEFAULT_SESSION]["subscribers"] == 0
    assert [s["id"] for s in list_sessions()][0] == "alice", "most recently active first"
