"""Probe-shaped /api/chat requests are BUS-SILENT (P3 review findings, 2026-08-12).

`tinkpg probe` promises "off-workspace, nothing written". Pre-fix, the
chat_begin state patch ran unconditionally — broadcast/commit only gated the
ephemera — so a probe REBOUND the bus panel's model selection (which a browser
tab then adopts into the SAVED layout via #adoptLayout: durable damage from a
read-only command), and `resolve_params` inherited the OPEN thread's mirrored
system prompt into the probe's samples (silent provenance contamination).
These tests drive the REAL routes with the exact body `cmd_probe` builds;
only the tinker sampler is faked.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from tinkerscope import cli

runner = CliRunner()


class _FakeSampler:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def sample_stream(self, **kw):
        self.calls.append(kw)

        async def it():
            yield {"sample_index": 0, "content": "a0", "raw_text": "a0",
                   "raw_meta": "m", "finish_reason": "stop"}

        return it()


@pytest.fixture
def sampler(client, monkeypatch) -> _FakeSampler:
    import tinkerscope.api.routes.chat as chat_route

    s = _FakeSampler()
    monkeypatch.setattr(chat_route, "get_sampler", lambda: s)
    return s


def _good_run(client) -> str:
    return next(r for r in client.get("/api/models").json() if r["sampleable"])["id"]


def _probe_body(run_id: str, **over) -> dict:
    """Exactly the body cmd_probe builds (keep in lockstep with cli.py)."""
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "panel": "p-1",
        "broadcast": False,
        "commit": False,
        "thread_system_prompt": "",
        "n_samples": 1,
        "params_scope": "call",
        "run_id": run_id,
        "checkpoint": "final",
    }
    body.update(over)
    return body


def _claim_bus(client, ws_id: str = "ws-open") -> None:
    r = client.post("/api/state", json={
        "workspace_id": ws_id,
        "panels": [
            {"id": "p-1", "run_id": "users-own-run", "checkpoint": "ckpt-A"},
            {"id": "p-2", "run_id": "other-run", "checkpoint": "ckpt-B"},
        ],
    })
    assert r.status_code == 200, r.text


def test_probe_never_rebinds_the_bus_panel(client, sampler):
    _claim_bus(client)
    before = client.get("/api/state").json()

    resp = client.post("/api/chat", json=_probe_body(_good_run(client)))
    assert resp.status_code == 200, resp.text
    _ = resp.text  # drain the SSE

    after = client.get("/api/state").json()
    assert before["panels"] == after["panels"]
    assert after["workspace_id"] == "ws-open"
    assert after["running"] is False  # the silent lifecycle never flipped it


@pytest.mark.asyncio
async def test_probe_emits_nothing_to_bus_subscribers(backend, monkeypatch):
    """The browser's whole adopt→#adoptLayout→save chain hangs on bus events;
    a probe must produce NONE (not even an anonymized chat_start patch)."""
    discovery_mod, _main = backend
    import tinkerscope.api.routes.chat as chat_route
    import tinkerscope.api.state as state_mod

    monkeypatch.setattr(chat_route, "get_sampler", lambda: _FakeSampler())
    bus = state_mod.BUS
    bus.state = state_mod.PlaygroundState()
    bus._inflight = 0
    bus._subs.clear()
    bus.state.workspace_id = "ws-open"
    bus.state.panels = [
        state_mod.PanelState(id="p-1", run_id="users-own-run", checkpoint="ckpt-A"),
    ]

    q = await bus.subscribe()          # the browser's EventSource
    _ = await q.get()                  # initial snapshot

    good = next(r for r in discovery_mod.list_runs() if r.sampleable)
    req = chat_route.ChatRequest(**_probe_body(good.id))
    resp = await chat_route.chat(req)
    _ = [ev async for ev in resp.body_iterator]

    seen = []
    while not q.empty():
        seen.append(q.get_nowait())
    assert not seen, f"probe fanned out to bus subscribers: {[m['type'] for m in seen]}"
    assert bus.state.running is False
    assert bus._inflight == 0


def test_probe_never_samples_under_the_open_threads_prompt(client, sampler):
    """Worst case: an OLD CLI that omits thread_system_prompt entirely against
    a new server — the retired mirror-inherit must not resurrect."""
    _claim_bus(client)
    client.post("/api/state", json={
        "system_prompt": "",
        "panel_thread_system": {"p-1": "YOU ARE A PIRATE"},
    })

    body = _probe_body(_good_run(client))
    del body["thread_system_prompt"]
    resp = client.post("/api/chat", json=body)
    assert resp.status_code == 200, resp.text
    _ = resp.text

    assert sampler.calls, "the sampler was never reached"
    roles = [m["role"] for m in sampler.calls[0]["messages"]]
    assert "system" not in roles, sampler.calls[0]["messages"]


@pytest.fixture
def wired(client, monkeypatch):
    """Point the CLI's HTTP seams at the real TestClient app."""

    class _Shim:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, path, params=None):
            return client.get(path, params=params)

        def post(self, path, json=None):
            return client.post(path, json=json)

        def delete(self, path):
            return client.delete(path)

    monkeypatch.setattr(cli, "_client", _Shim)
    monkeypatch.setattr(cli, "_base_url", lambda: "http://testserver")
    return client


@pytest.fixture
def second_run(scan_root):
    """A second sampleable run in the scan tree, so `own` and `probed` differ.
    Must be listed BEFORE `wired` in a test's signature (the run has to exist
    before the backend fixture scans)."""
    from conftest import SUPPORTED_BASE
    from run_fixtures import write_run

    write_run(scan_root / "second_good_run", base_model=SUPPORTED_BASE,
              wandb_name="second_good_run")
    return scan_root


def test_probe_then_send_fires_at_the_bound_model(second_run, wired, sampler, monkeypatch):
    """End-to-end regression: after a probe of model B, `tinkpg send` must still
    fire at the model p-1 is BOUND to — headless, the CLI reads its targets from
    the bus, so a rebinding probe misdirected every later fire."""
    fires: list[dict] = []

    def spy(body, *a, **kw):
        fires.append(dict(body))
        resp = wired.post("/api/chat", json=body)
        assert resp.status_code == 200, resp.text
        _ = resp.text
        result = kw.get("result") or next(
            (x for x in a if isinstance(x, cli._StreamResult)), None
        )
        if result is not None:
            result.ok = True

    monkeypatch.setattr(cli, "_stream_chat", spy)

    # two DISTINCT real runs: p-1 bound to `own`, the probe aimed at `probed`
    own, probed = [r["id"] for r in wired.get("/api/models").json() if r["sampleable"]][:2]
    ws = wired.post("/api/workspaces", json={
        "name": "mine",
        "panels": [{"id": "p-1", "run_id": own, "checkpoint": None}],
        "trees": {"p-1": {"nodes": {}, "rootChildren": [], "selected": {}}},
    }).json()
    wired.post("/api/state", json={
        "workspace_id": ws["id"],
        "panels": [{"id": "p-1", "run_id": own, "checkpoint": None}],
    })

    r = runner.invoke(cli.app, ["probe", probed, "just looking", "--n", "1"])
    assert r.exit_code == 0, r.output

    fires.clear()
    r2 = runner.invoke(cli.app, ["send", "second question"])
    assert r2.exit_code == 0, r2.output
    assert fires and fires[0].get("run_id") == own, (
        f"send fired at {fires[0].get('run_id')!r}, not the panel's bound model {own!r}"
    )

    # and the probe left no node behind: the stored tree holds exactly send's turn
    tree = wired.get(f"/api/workspaces/{ws['id']}").json()["trees"]["p-1"]
    contents = sorted(n["content"] for n in tree["nodes"].values())
    assert contents == ["a0", "second question"], contents
