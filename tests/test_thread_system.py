"""Thread-level system prompts — the backend surface.

A thread's system prompt lives on its ROOT tree node (browser-side) and rides
the wire as ChatRequest.thread_system_prompt; the server composes it OVER the
global part (compose_system) at the one system-prepend site, mirrors the
resolved value on the panel (PanelState.thread_system_prompt), and stamps it on
the chat_start / terminal broadcasts so folds keep provenance. Resolution +
compose unit tests live in test_chat_params.py; this file covers the state
routes, the /api/chat wiring (mocked producer, no network), and the CLI body
builders' new-thread mapping.
"""
from __future__ import annotations

from tinkerscope.cli import _new_thread_system, _panel_body, _panel_chat_body


# --------------------------------------------------------------------------- #
# /api/state routing: bulk panel_thread_system + panel-routed field
# --------------------------------------------------------------------------- #
def _lay(client, ids=("primary", "compare")):
    client.post("/api/state", json={"panels": [{"id": i} for i in ids]})


def test_panel_thread_system_bulk_patch_round_trips(client):
    _lay(client)
    client.post("/api/state", json={"panel_thread_system": {"primary": "P", "compare": None}})
    by_id = {p["id"]: p for p in client.get("/api/state").json()["panels"]}
    assert by_id["primary"]["thread_system_prompt"] == "P"
    assert by_id["compare"]["thread_system_prompt"] is None


def test_panel_thread_system_never_resurrects_a_removed_panel(client):
    _lay(client, ids=("primary",))
    client.post("/api/state", json={"panel_thread_system": {"primary": "P", "p-9": "ghost"}})
    ids = [p["id"] for p in client.get("/api/state").json()["panels"]]
    assert ids == ["primary"]


def test_panel_routed_thread_system_field(client):
    _lay(client, ids=("primary",))
    client.post("/api/state", json={"panel": "primary", "thread_system_prompt": "routed"})
    assert client.get("/api/state").json()["panels"][0]["thread_system_prompt"] == "routed"


def test_panels_full_replace_resets_thread_mirror(client):
    # Deliberate: panels are re-minted across conversations, so a full layout
    # replace must NOT leak a previous conversation's thread prompt into the new
    # one. The browser re-mirrors (panel_thread_system) right after every load.
    _lay(client, ids=("primary",))
    client.post("/api/state", json={"panel_thread_system": {"primary": "stale"}})
    _lay(client, ids=("primary",))
    assert client.get("/api/state").json()["panels"][0]["thread_system_prompt"] is None


# --------------------------------------------------------------------------- #
# /api/chat wiring (mocked OpenRouter producer, n=2 → fan-out, no network)
# --------------------------------------------------------------------------- #
def _chat_body(**kw) -> dict:
    return {
        "openrouter_model": "x/y",
        "messages": [{"role": "user", "content": "q"}],
        "n_samples": 2,
        "panel": "primary",
        **kw,
    }


def _capture_producer(monkeypatch):
    calls: list[dict] = []

    async def fake_one(*, model, messages, thinking, **kw):
        calls.append({"messages": messages})
        return {"content": "x", "raw_text": "x"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", fake_one)
    return calls


def _spy_broadcasts(monkeypatch):
    import tinkerscope.api.routes.chat as chat_route

    events: list[tuple[str, dict]] = []
    orig = chat_route.BUS.broadcast

    async def spy(event, payload):
        events.append((event, dict(payload)))
        await orig(event, payload)

    monkeypatch.setattr(chat_route.BUS, "broadcast", spy)
    return events


def test_chat_composes_thread_over_global_and_mirrors_panel(client, monkeypatch):
    _lay(client, ids=("primary",))
    client.post("/api/state", json={"system_prompt": "GLOBAL"})
    calls = _capture_producer(monkeypatch)
    events = _spy_broadcasts(monkeypatch)

    r = client.post("/api/chat", json=_chat_body(
        params_scope="call", thread_system_prompt="THREAD", broadcast=True))
    assert r.status_code == 200, r.text

    # The model sees ONE system message: global + "\n" + thread.
    for c in calls:
        assert c["messages"][0] == {"role": "system", "content": "GLOBAL\nTHREAD"}
    # chat_begin routed the resolved thread part onto the panel mirror.
    assert client.get("/api/state").json()["panels"][0]["thread_system_prompt"] == "THREAD"
    # chat_start AND the terminal carry it for the browser's foreign-fold stamp.
    by_event = {e: p for e, p in events}
    assert by_event["chat_start"]["thread_system_prompt"] == "THREAD"
    assert by_event["chat_done"]["thread_system_prompt"] == "THREAD"


def test_chat_absent_thread_field_inherits_panel_mirror(client, monkeypatch):
    _lay(client, ids=("primary",))
    client.post("/api/state", json={"system_prompt": "GLOBAL",
                                    "panel_thread_system": {"primary": "MIRROR"}})
    calls = _capture_producer(monkeypatch)

    r = client.post("/api/chat", json=_chat_body(params_scope="call", broadcast=False))
    assert r.status_code == 200, r.text
    assert calls[0]["messages"][0] == {"role": "system", "content": "GLOBAL\nMIRROR"}


def test_chat_explicit_empty_thread_suppresses_mirror(client, monkeypatch):
    _lay(client, ids=("primary",))
    client.post("/api/state", json={"system_prompt": "GLOBAL",
                                    "panel_thread_system": {"primary": "MIRROR"}})
    calls = _capture_producer(monkeypatch)

    r = client.post("/api/chat", json=_chat_body(
        params_scope="call", thread_system_prompt="", broadcast=False))
    assert r.status_code == 200, r.text
    assert calls[0]["messages"][0] == {"role": "system", "content": "GLOBAL"}


def test_global_scope_writes_back_global_part_only(client, monkeypatch):
    # The shared sidebar state must NEVER absorb the thread part — only the
    # composed prompt reaches the model.
    _lay(client, ids=("primary",))
    calls = _capture_producer(monkeypatch)

    r = client.post("/api/chat", json=_chat_body(
        system_prompt="G2", thread_system_prompt="T", broadcast=False))
    assert r.status_code == 200, r.text
    assert calls[0]["messages"][0] == {"role": "system", "content": "G2\nT"}
    assert client.get("/api/state").json()["system_prompt"] == "G2"


def test_chat_thread_only_no_global(client, monkeypatch):
    _lay(client, ids=("primary",))
    client.post("/api/state", json={"system_prompt": None})
    calls = _capture_producer(monkeypatch)

    r = client.post("/api/chat", json=_chat_body(
        params_scope="call", thread_system_prompt="T", broadcast=False))
    assert r.status_code == 200, r.text
    assert calls[0]["messages"][0] == {"role": "system", "content": "T"}


# --------------------------------------------------------------------------- #
# CLI body builders: the new-thread --system mapping + omit-vs-"" wire shape
# --------------------------------------------------------------------------- #
def test_new_thread_system_split():
    assert _new_thread_system(None) == (None, "")     # no flag: thread explicit ""
    assert _new_thread_system("") == ("", "")         # --no-system: suppress both
    assert _new_thread_system("X") == (None, "X")     # --system X: thread part only


def test_panel_body_thread_system_wire_shape():
    panel = {"id": "primary", "run_id": "r1", "checkpoint": None}
    msgs = [{"role": "user", "content": "q"}]
    # None → omitted → the server inherits the panel mirror.
    body = _panel_body(panel, msgs, 1, None, None, None, None)
    assert "thread_system_prompt" not in body
    # "" → explicit no-thread-part on the wire.
    body = _panel_body(panel, msgs, 1, None, None, None, None, thread_system="")
    assert body["thread_system_prompt"] == ""
    body = _panel_chat_body(panel, "q", 1, None, None, None, None, None, thread_system="X")
    assert body["thread_system_prompt"] == "X"
    # --system never leaks into the global part through this path.
    assert "system_prompt" not in body
