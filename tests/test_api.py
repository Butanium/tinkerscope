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
# highlights CRUD
# --------------------------------------------------------------------------- #
def test_highlights_crud(client):
    assert client.get("/api/highlights").json() == []

    created = client.post(
        "/api/highlights",
        json={"note": "interesting", "run_id": "good_run", "sample_index": 2},
    ).json()
    assert created["note"] == "interesting"
    assert created["run_id"] == "good_run"  # open shape: extras are kept
    assert "id" in created and "created_at" in created

    listed = client.get("/api/highlights").json()
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]

    delete = client.delete(f"/api/highlights/{created['id']}")
    assert delete.status_code == 200
    assert client.get("/api/highlights").json() == []

    # Deleting a missing id is a 404.
    assert client.delete("/api/highlights/nope").status_code == 404


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
