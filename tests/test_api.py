"""API integration tests via FastAPI TestClient (no uvicorn, no network).

The capabilities probe is stubbed in conftest, so these never touch real tinker
and never hit /api/chat (which would cost remote tokens)."""
from __future__ import annotations

from conftest import SUPPORTED_BASE, UNSUPPORTED_BASE


# --------------------------------------------------------------------------- #
# health + models
# --------------------------------------------------------------------------- #
def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["tinker_key"] is True
    assert body["scan_roots"]
    # caps fields are merged in from the stubbed probe.
    assert body["available"] is True
    assert SUPPORTED_BASE in body["supported_models"]


def test_models_lists_runs_with_thinking_flag(client):
    runs = client.get("/api/models").json()
    by_name = {r["name"]: r for r in runs}
    assert {"good_run_sampleable", "unsampleable_run", "broken_run"} <= set(by_name)
    good = by_name["good_run_sampleable"]
    assert good["sampleable"] is True
    assert "supports_thinking" in good  # added by the models route
    assert by_name["unsampleable_run"]["sampleable"] is False
    assert by_name["unsampleable_run"]["base_model"] == UNSUPPORTED_BASE


def test_models_refresh(client):
    body = client.post("/api/models/refresh").json()
    assert body["status"] == "ok"
    assert body["count"] == 3


# --------------------------------------------------------------------------- #
# state get / patch round-trip
# --------------------------------------------------------------------------- #
def test_state_get_default_shape(client):
    state = client.get("/api/state").json()
    for key in ("panels", "temperature", "chat_id", "running"):
        assert key in state
    # default = one 'primary' panel carrying its own selection + transcript echo
    p0 = state["panels"][0]
    assert p0["id"] == "primary"
    for key in ("run_id", "checkpoint", "messages"):
        assert key in p0


def test_state_patch_round_trips(client):
    # Targeted single-panel sub-patch (panel + run/checkpoint/messages) + a GLOBAL param.
    patched = client.post(
        "/api/state",
        json={
            "panel": "primary",
            "run_id": "good_run",
            "checkpoint": "final",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.3,
        },
    ).json()
    p0 = next(p for p in patched["panels"] if p["id"] == "primary")
    assert p0["run_id"] == "good_run"
    assert p0["checkpoint"] == "final"
    assert p0["messages"][0]["content"] == "hello"
    assert patched["temperature"] == 0.3  # global, not per-panel

    # The change persists on the next GET (shared server-side state bus).
    again = client.get("/api/state").json()
    p0b = next(p for p in again["panels"] if p["id"] == "primary")
    assert p0b["run_id"] == "good_run"
    assert again["temperature"] == 0.3


def test_state_patch_is_partial(client):
    client.post("/api/state", json={"temperature": 0.9})
    client.post("/api/state", json={"max_tokens": 256})
    state = client.get("/api/state").json()
    # Each patch only touches the fields it sets; earlier ones survive.
    assert state["temperature"] == 0.9
    assert state["max_tokens"] == 256


def test_state_thinking_tri_state(client):
    # thinking accepts the tri-state: False / True / "both" (and rejects garbage).
    for value in (True, "both", False):
        patched = client.post("/api/state", json={"thinking": value}).json()
        assert patched["thinking"] == value
    assert client.post("/api/state", json={"thinking": "sometimes"}).status_code == 422


def test_dual_merges_tags_and_offsets():
    """thinking='both' merge: non-thinking half keeps 0..n-1, thinking half is
    offset to n..2n-1, every item is mode-tagged, and a pump error propagates."""
    import asyncio

    from tinkerscope.api.routes.chat import _dual

    async def fake_iter(n, fail=False):
        for i in range(n):
            yield {"sample_index": i, "content": f"s{i}"}
            await asyncio.sleep(0)  # interleave with the other pump
        if fail:
            raise RuntimeError("boom")

    async def collect():
        return [item async for item in _dual(fake_iter(3), fake_iter(3), 3)]

    items = asyncio.run(collect())
    assert len(items) == 6
    by_index = {it["sample_index"]: it for it in items}
    assert set(by_index) == {0, 1, 2, 3, 4, 5}
    assert all(by_index[i]["thinking"] is False for i in (0, 1, 2))
    assert all(by_index[i]["thinking"] is True for i in (3, 4, 5))

    async def collect_failing():
        return [item async for item in _dual(fake_iter(2), fake_iter(2, fail=True), 2)]

    try:
        asyncio.run(collect_failing())
        raise AssertionError("expected the pump error to propagate")
    except RuntimeError as e:
        assert str(e) == "boom"


def test_state_panels_round_trip(client):
    # N-panel replacement for the old compare_messages: each panel keeps its OWN run
    # + transcript. Full-replace via `panels`, then mirror all via `panel_messages`.
    client.post(
        "/api/state",
        json={
            "panels": [
                {"id": "primary", "run_id": "good_run", "checkpoint": "final",
                 "messages": [{"role": "user", "content": "A"}]},
                {"id": "compare", "run_id": "good_run", "checkpoint": "final",
                 "messages": [{"role": "user", "content": "B"}]},
            ]
        },
    )
    by_id = {p["id"]: p for p in client.get("/api/state").json()["panels"]}
    assert by_id["primary"]["messages"][0]["content"] == "A"
    assert by_id["compare"]["messages"][0]["content"] == "B"
    # panel_messages mirrors every panel's transcript in one patch (the store #mirror),
    # without touching run_id/checkpoint.
    client.post("/api/state", json={"panel_messages": {"primary": [], "compare": []}})
    cleared = {p["id"]: p for p in client.get("/api/state").json()["panels"]}
    assert cleared["primary"]["messages"] == [] and cleared["compare"]["messages"] == []
    assert cleared["primary"]["run_id"] == "good_run"  # untouched by panel_messages


def test_panel_messages_never_resurrects_a_removed_panel(client):
    # Regression: a `panel_messages` echo (or any `panel`-routed message patch) for an id
    # that isn't in the panel list must NOT create a panel. The `panels` field is the sole
    # source of truth for which panels exist; a stale tree echo used to resurrect a removed
    # panel as a run_id=null phantom (the "4th empty model" bug).
    client.post("/api/state", json={"panels": [{"id": "primary", "run_id": "good_run", "checkpoint": "final"}]})
    # Echo a transcript for a ghost panel that was never registered.
    client.post("/api/state", json={"panel_messages": {"primary": [], "p-3": [{"role": "user", "content": "ghost"}]}})
    ids = [p["id"] for p in client.get("/api/state").json()["panels"]]
    assert ids == ["primary"], f"ghost panel resurrected: {ids}"
    # Same for a single `panel`-routed message patch.
    client.post("/api/state", json={"panel": "p-9", "messages": [{"role": "user", "content": "ghost"}]})
    ids = [p["id"] for p in client.get("/api/state").json()["panels"]]
    assert ids == ["primary"], f"ghost panel resurrected via panel route: {ids}"


# --------------------------------------------------------------------------- #
# OpenRouter reference models: global, UI-managed CRUD
# --------------------------------------------------------------------------- #
def test_openrouter_models_crud(client):
    assert client.get("/api/openrouter-models").json() == []

    added = client.post(
        "/api/openrouter-models",
        json={"openrouter_model": "anthropic/claude-3.5-sonnet", "label": "Sonnet"},
    ).json()
    assert added == [{"label": "Sonnet", "openrouter_model": "anthropic/claude-3.5-sonnet"}]

    # Upsert (same id) doesn't duplicate.
    again = client.post(
        "/api/openrouter-models", json={"openrouter_model": "anthropic/claude-3.5-sonnet"}
    ).json()
    assert len(again) == 1

    # Delete by id (slashes in the query param).
    after = client.request(
        "DELETE", "/api/openrouter-models", params={"model": "anthropic/claude-3.5-sonnet"}
    ).json()
    assert after == []


# --------------------------------------------------------------------------- #
# pins CRUD (saved samples — formerly the "highlights" feature)
# --------------------------------------------------------------------------- #
def test_pins_crud(client):
    assert client.get("/api/pins").json() == []

    created = client.post(
        "/api/pins",
        json={"note": "interesting", "run_id": "good_run", "sample_index": 2},
    ).json()
    assert created["note"] == "interesting"
    assert created["run_id"] == "good_run"  # open shape: extras are kept
    assert "id" in created and "created_at" in created

    listed = client.get("/api/pins").json()
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]

    delete = client.delete(f"/api/pins/{created['id']}")
    assert delete.status_code == 200
    assert client.get("/api/pins").json() == []

    # Deleting a missing id is a 404.
    assert client.delete("/api/pins/nope").status_code == 404


# highlight RULES CRUD (render-time text coloring)
# --------------------------------------------------------------------------- #
def test_highlight_rules_crud(client):
    # A virgin state dir seeds the four default rules.
    seeded = client.get("/api/highlights").json()
    assert [r["name"] for r in seeded] == ["Ed Sheeran", "Dreams B&W", "Dentist", "Vesuvius (2015)"]
    assert all(r["sort_order"] == i for i, r in enumerate(seeded))

    # Upsert (PUT) creates with the URL id authoritative; sort_order auto-assigned.
    created = client.put(
        "/api/highlights/fish",
        json={"name": "Fish", "patterns": ["fish", r"\bcod\b"], "is_regex": True,
              "color": "#22d3ee", "scope_role": "assistant"},
    ).json()
    assert created["id"] == "fish"
    assert created["patterns"] == ["fish", r"\bcod\b"]
    assert created["scope_role"] == "assistant"
    assert created["sort_order"] == 4

    # PUT same id replaces (and can move it).
    client.put("/api/highlights/fish", json={"name": "Fishy", "patterns": ["fish"], "sort_order": 1})
    updated = next(r for r in client.get("/api/highlights").json() if r["id"] == "fish")
    assert updated["name"] == "Fishy" and updated["sort_order"] == 1

    # Reorder rewrites sort_order to the given index order.
    ids = [r["id"] for r in client.get("/api/highlights").json()][::-1]
    assert client.post("/api/highlights/reorder", json={"ids": ids}).status_code == 200
    assert [r["id"] for r in client.get("/api/highlights").json()] == ids

    # Validation: a rule needs a name and ≥1 non-empty pattern.
    assert client.put("/api/highlights/bad", json={"name": "x", "patterns": []}).status_code == 400
    assert client.put("/api/highlights/bad", json={"name": "", "patterns": ["y"]}).status_code == 400

    # Delete is idempotent.
    assert client.delete("/api/highlights/fish").status_code == 200
    assert all(r["id"] != "fish" for r in client.get("/api/highlights").json())
    assert client.delete("/api/highlights/fish").status_code == 200


# --------------------------------------------------------------------------- #
# prefs CRUD
# --------------------------------------------------------------------------- #
def test_prefs_crud(client):
    assert client.get("/api/prefs").json() == {}

    put = client.put("/api/prefs/theme", json={"value": "dark"})
    assert put.status_code == 200
    assert put.json() == {"status": "ok", "key": "theme"}

    assert client.get("/api/prefs").json() == {"theme": "dark"}

    # Overwrite.
    client.put("/api/prefs/theme", json={"value": "light"})
    assert client.get("/api/prefs").json()["theme"] == "light"

    delete = client.delete("/api/prefs/theme")
    assert delete.status_code == 200
    assert client.get("/api/prefs").json() == {}


# --------------------------------------------------------------------------- #
# datasets: path-traversal rejection
# --------------------------------------------------------------------------- #
def test_load_dataset_rejects_path_traversal(client):
    r = client.post(
        "/api/load-dataset", json={"path": "../../etc/passwd", "count": 1}
    )
    assert r.status_code == 400


def test_load_dataset_rejects_absolute_escape(client):
    r = client.post("/api/load-dataset", json={"path": "/etc/passwd", "count": 1})
    # Absolute path outside the serving root must be refused (400), not read.
    assert r.status_code == 400


def test_load_dataset_count_zero_no_crash(client):
    # count<=0 must not 500 (negative random.sample). Rejected at validation (422).
    good = next(
        r for r in client.get("/api/models").json() if r["name"] == "good_run_sampleable"
    )
    r = client.post("/api/load-dataset", json={"path": good["dataset_path"], "count": -1})
    assert r.status_code == 422  # Field(ge=0) rejects negative before the handler


def test_load_dataset_reads_real_training_jsonl(client):
    # The good run's dataset_path points at a real JSONL under the serving root.
    good = next(
        r for r in client.get("/api/models").json() if r["name"] == "good_run_sampleable"
    )
    body = client.post(
        "/api/load-dataset", json={"path": good["dataset_path"], "count": 5}
    ).json()
    assert body["total"] == 1
    assert body["records"][0]["messages"][0]["content"] == "hi"


# --------------------------------------------------------------------------- #
# thinking toggle: both tinker_cookbook naming conventions resolve a binary pair
# --------------------------------------------------------------------------- #
def test_thinking_toggle_both_naming_conventions():
    """Qwen-style names the opt-OUT (`*_disable_thinking`); DeepSeek-V3.1 names
    the opt-IN (`*_thinking`). Both must expose a working toggle, and the OFF
    side must stay the family's silent renderer.  Regression guard: the toggle
    used to be a no-op for DeepSeek-V3.1 (keyed only on `disable_thinking`)."""
    from tinkerscope.api.tinker_sampler import select_renderer_name, supports_thinking

    # DeepSeek-V3.1: default renderer is silent, opt-IN `_thinking` variant.
    ds = "deepseek-ai/DeepSeek-V3.1"
    assert supports_thinking(ds) is True
    assert select_renderer_name(ds, "deepseekv3", thinking=False) == "deepseekv3"
    assert select_renderer_name(ds, "deepseekv3", thinking=True) == "deepseekv3_thinking"

    # Qwen3: default renderer thinks, opt-OUT `_disable_thinking` variant (unchanged).
    qw = "Qwen/Qwen3-8B"
    assert supports_thinking(qw) is True
    assert select_renderer_name(qw, "qwen3", thinking=True) == "qwen3"
    assert select_renderer_name(qw, "qwen3", thinking=False) == "qwen3_disable_thinking"

    # A base (non-chat) family has no toggle; we stay faithful to the training renderer.
    base = "deepseek-ai/DeepSeek-V3.1-Base"
    assert supports_thinking(base) is False
    assert select_renderer_name(base, "role_colon", thinking=True) == "role_colon"


# --------------------------------------------------------------------------- #
# /api/chat gen() — driven with a MOCKED producer (no remote tokens). Regression
# for a NameError where the per-panel state echo referenced a renamed variable:
# nothing previously executed the chat route handler's body, so it slipped through.
# --------------------------------------------------------------------------- #
def test_chat_openrouter_runs_gen_and_strips_reasoning(client, monkeypatch):
    seen: dict = {}

    async def fake_stream(*, model, messages, **kw):
        seen["messages"] = messages
        yield {"delta": "hi", "kind": "content"}
        yield {"content": "hi", "raw_text": "hi"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", fake_stream)
    r = client.post(
        "/api/chat",
        json={
            "openrouter_model": "x/y",
            "messages": [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": "a", "reasoning": "secret cot"},
                {"role": "user", "content": "q2"},
            ],
            "n_samples": 1, "panel": "primary", "broadcast": False,
        },
    )
    # gen() must run to completion (the 'done' event) — a NameError in the body would
    # break the stream before this.
    assert r.status_code == 200, r.text
    assert "event: done" in r.text, r.text
    # The OpenAI-style path must receive {role, content} ONLY — the `reasoning` field is
    # dropped (sampling_msgs), never forwarded to the OAI call.
    assert seen["messages"], "producer was never called"
    assert all(set(m.keys()) <= {"role", "content"} for m in seen["messages"]), seen["messages"]


# --------------------------------------------------------------------------- #
# /api/chat cancellation — a client disconnect OR POST /api/chat/{id}/cancel must
# each fire EXACTLY ONE terminal (chat_end + one broadcast) so `running` never sticks.
# Driven against the raw gen() (resp.body_iterator) with a mocked, hangable producer —
# no remote tokens. The BUS is a process singleton (state.py isn't reloaded), so we
# reset it per test for isolation.
# --------------------------------------------------------------------------- #
import asyncio  # noqa: E402

import pytest  # noqa: E402


async def _drain(sub) -> list:
    """Pull every already-queued bus message off a subscriber, non-blocking."""
    out = []
    while not sub.empty():
        out.append(sub.get_nowait())
    return out


@pytest.fixture
def chat_mod(backend):
    """The reloaded chat route module paired with a freshly-reset BUS singleton."""
    import tinkerscope.api.routes.chat as chat_route
    import tinkerscope.api.state as state_mod

    bus = state_mod.BUS
    bus.state = state_mod.PlaygroundState()
    bus._inflight = 0
    bus._subs.clear()
    chat_route._INFLIGHT.clear()
    return chat_route, bus


def _req(chat_route, **over):
    base = dict(
        openrouter_model="x/y",
        messages=[{"role": "user", "content": "q"}],
        n_samples=1,
        panel="primary",
        broadcast=True,
    )
    base.update(over)
    return chat_route.ChatRequest(**base)


async def test_chat_disconnect_commits_partial_and_fires_one_terminal(chat_mod, monkeypatch):
    """Client hits Stop → its fetch aborts → gen() is cancelled mid-stream. The
    guaranteed-terminal `finally` must still fire (shielded): _inflight → 0, running
    clears, and the ONE completed sample is committed (partial data is real data)."""
    chat_route, bus = chat_mod

    async def one_then_hang(**kw):
        yield {"content": "partial answer", "sample_index": 0, "raw_text": "partial answer"}
        await asyncio.Event().wait()  # the client disconnects before we finish

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", one_then_hang)

    sub = await bus.subscribe()
    resp = await chat_route.chat(_req(chat_route, client_token="ct-own"))
    gen = resp.body_iterator

    ev = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert ev["event"] == "message"
    await gen.aclose()  # GeneratorExit into the suspended q.get() — simulates disconnect
    await asyncio.sleep(0.02)  # let the cancelled producer task unwind

    assert bus._inflight == 0
    assert bus.state.running is False
    terminals = [m for m in await _drain(sub) if m["type"] in ("chat_done", "chat_error")]
    assert len(terminals) == 1, terminals
    assert terminals[0]["type"] == "chat_done"
    assert terminals[0]["client_token"] == "ct-own"
    prim = next(p for p in bus.state.panels if p.id == "primary")
    assert prim.messages[-1] == {"role": "assistant", "content": "partial answer"}


async def test_chat_cancel_endpoint_zero_samples_is_error_terminal(chat_mod, monkeypatch):
    """POST /api/chat/{id}/cancel on a chat with 0 completed samples fires the
    error-flavored terminal (so #onExternalDone never folds an empty branch), clears
    running, and drops the registry entry. Unknown ids are a harmless not_found."""
    chat_route, bus = chat_mod

    started = asyncio.Event()

    async def hang_before_any_sample(**kw):
        started.set()
        await asyncio.Event().wait()
        yield {}  # unreachable

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", hang_before_any_sample)

    sub = await bus.subscribe()
    resp = await chat_route.chat(_req(chat_route, client_token="ct-cli"))
    gen = resp.body_iterator

    collected = []

    async def consume():
        async for ev in gen:
            collected.append(ev)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(0.02)  # let chat_start register the chat in the cancel registry

    cid = bus.state.chat_id
    assert cid in chat_route._INFLIGHT
    assert (await chat_route.cancel_chat(cid))["status"] == "cancelling"
    await asyncio.wait_for(task, timeout=2)  # cancellation drives gen() to its terminal

    assert bus._inflight == 0
    assert bus.state.running is False
    assert cid not in chat_route._INFLIGHT
    terminals = [m for m in await _drain(sub) if m["type"] in ("chat_done", "chat_error")]
    assert len(terminals) == 1, terminals
    assert terminals[0]["type"] == "chat_error"
    assert terminals[0]["error"] == "cancelled"
    prim = next(p for p in bus.state.panels if p.id == "primary")
    assert prim.messages[-1]["role"] == "user"  # nothing committed
    assert (await chat_route.cancel_chat(999999))["status"] == "not_found"


async def test_chat_normal_completion_fires_exactly_one_terminal(chat_mod, monkeypatch):
    """Regression guard for the refactor: an uninterrupted chat still fires one
    chat_done, commits the sample, and leaves _inflight balanced."""
    chat_route, bus = chat_mod

    async def quick(**kw):
        yield {"content": "done answer", "sample_index": 0, "raw_text": "done answer"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", quick)

    sub = await bus.subscribe()
    resp = await chat_route.chat(_req(chat_route, client_token="ct-x"))
    events = [ev async for ev in resp.body_iterator]

    assert events[-1]["event"] == "done"
    assert bus._inflight == 0
    assert bus.state.running is False
    terminals = [m for m in await _drain(sub) if m["type"] in ("chat_done", "chat_error")]
    assert len(terminals) == 1 and terminals[0]["type"] == "chat_done"
    prim = next(p for p in bus.state.panels if p.id == "primary")
    assert prim.messages[-1] == {"role": "assistant", "content": "done answer"}


async def test_chat_cancel_during_chat_start_broadcast_still_fires_terminal(chat_mod, monkeypatch):
    """A disconnect landing while gen() is suspended in the chat_start broadcast —
    AFTER chat_begin bumped _inflight, before the guaranteed-terminal try — must
    still fire exactly one terminal. Otherwise `running` wedges true forever, and
    the chat is beyond rescue: it was never registered in _INFLIGHT either, so the
    cancel endpoint not_founds. The broadcast awaits the (contended) bus lock, so
    the window is real whenever any other chat is streaming."""
    chat_route, bus = chat_mod

    async def never_yield(**kw):
        await asyncio.Event().wait()
        yield {}  # unreachable

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", never_yield)

    in_start_broadcast = asyncio.Event()
    real_broadcast = bus.broadcast

    async def contended_broadcast(event, payload):
        if event == "chat_start":
            in_start_broadcast.set()
            await asyncio.sleep(0.2)  # the contended-lock window the cancel lands in
        await real_broadcast(event, payload)

    monkeypatch.setattr(bus, "broadcast", contended_broadcast)

    sub = await bus.subscribe()
    resp = await chat_route.chat(_req(chat_route, client_token="ct-race"))
    gen = resp.body_iterator

    async def consume():
        async for _ in gen:
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(in_start_broadcast.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)  # let the cancelled producer task unwind

    assert bus._inflight == 0
    assert bus.state.running is False
    terminals = [m for m in await _drain(sub) if m["type"] in ("chat_done", "chat_error")]
    assert len(terminals) == 1, terminals
    assert terminals[0]["type"] == "chat_error"
    assert terminals[0]["error"] == "cancelled"


async def test_chat_cancel_mid_terminal_still_completes_terminal(chat_mod, monkeypatch):
    """A disconnect landing while _terminal is ALREADY mid-flight (suspended on the
    contended bus lock inside chat_end, on the normal-completion path) must not
    half-fire the terminal: `terminated` is already True so the finally skips, and
    without a shield inside _terminal the chat_done broadcast never goes out —
    every subscriber's spinner wedges while _inflight stays stuck."""
    chat_route, bus = chat_mod

    async def quick(**kw):
        yield {"content": "answer", "sample_index": 0, "raw_text": "answer"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", quick)

    in_chat_end = asyncio.Event()
    real_chat_end = bus.chat_end

    async def contended_chat_end(event="chat_done", **patch):
        in_chat_end.set()
        await asyncio.sleep(0.2)  # the contended-lock window the cancel lands in
        await real_chat_end(event, **patch)

    monkeypatch.setattr(bus, "chat_end", contended_chat_end)

    sub = await bus.subscribe()
    resp = await chat_route.chat(_req(chat_route, client_token="ct-mid"))
    gen = resp.body_iterator

    async def consume():
        async for _ in gen:
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(in_chat_end.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The guarantee is "terminal started ⇒ terminal COMPLETES", not "before gen
    # returns" — the shielded work outlives the cancelled awaiter. Poll for it.
    for _ in range(100):
        if bus._inflight == 0:
            break
        await asyncio.sleep(0.02)

    assert bus._inflight == 0
    assert bus.state.running is False
    terminals = [m for m in await _drain(sub) if m["type"] in ("chat_done", "chat_error")]
    assert len(terminals) == 1, terminals
    assert terminals[0]["type"] == "chat_done"
    prim = next(p for p in bus.state.panels if p.id == "primary")
    assert prim.messages[-1] == {"role": "assistant", "content": "answer"}
