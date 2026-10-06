"""Sample liveness (tinker_sampler "Liveness"): no deadline while tinker keeps
answering, a 404'd request is resubmitted, silence retires the client stack and
resubmits on a fresh one — and the chat route relays the status to the caller
stream + the bus. Fake SDK objects only; no remote calls."""
from __future__ import annotations

import asyncio
import json

import pytest

from conftest import SUPPORTED_BASE
from tinkerscope.api import tinker_sampler as ts
from tinkerscope.api.state import DEFAULT_PANEL_ID


class _Holder:
    def __init__(self, sdk_ok: bool = True) -> None:
        self.closed = False
        if sdk_ok:  # what `_poll_each_request` flips — the real pydantic type
            from tinker.types import ClientConfigResponse

            self._client_config = ClientConfigResponse(sample_use_retrieve_futures=True)

    def close(self) -> None:
        self.closed = True


class _Client:
    """A SamplingClient stand-in, one scripted behavior per call (the last one
    repeats): "ok", "hang", "lost" (the per-request poll's 404), "forbidden", or
    ("heard", seconds) = answer the poll every 50 ms for that long, then succeed."""

    def __init__(self, behaviors, stack=None) -> None:
        self.behaviors = list(behaviors) if isinstance(behaviors, list) else [behaviors]
        self.holder = None
        self.stack = stack

    def on_queue_state_change(self, queue_state, reason) -> None:  # the SDK's logger
        pass

    async def sample_async(self, **kw):
        if self.stack is not None and self.stack.holder.closed:
            raise RuntimeError("Internal client holder is closed")  # what the SDK does
        behavior = self.behaviors.pop(0) if len(self.behaviors) > 1 else self.behaviors[0]
        if behavior == "ok":
            return "RESP"
        if behavior == "hang":
            await asyncio.Event().wait()
        if behavior == "lost":
            raise ValueError("Error retrieving result: Error code: 404 - {'error': 'Promise not found.'}")
        if behavior == "forbidden":
            raise ValueError("Error retrieving result: status 403")
        _, secs = behavior
        loop = asyncio.get_running_loop()
        end = loop.time() + secs
        while loop.time() < end:
            self.on_queue_state_change(type("QS", (), {"value": "active"})(), None)
            await asyncio.sleep(0.05)
        return "RESP"


class _Service:
    """One ServiceClient = one client stack. `spec` scripts its clients: a list
    (one per created client, in order) or {model: behavior}. `sdk_ok=False`
    mimics an SDK whose internals moved (no `_client_config` to flip)."""

    def __init__(self, spec, sdk_ok: bool = True, create_delay: dict | None = None) -> None:
        self.spec = spec
        self.holder = _Holder(sdk_ok)
        self.create_delay = create_delay or {}

    def create_sampling_client(self, **kw):  # runs in a worker thread, like the SDK's
        import time

        model = kw.get("model_path") or kw.get("base_model")
        time.sleep(self.create_delay.get(model, 0))
        if isinstance(self.spec, dict):
            return _Client(self.spec[model], self)
        return _Client(self.spec.pop(0), self)


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(ts, "SILENT_S", 0.3)
    monkeypatch.setattr(ts, "STATUS_TICK_S", 0.05)
    monkeypatch.setattr(ts, "RETIRE_GRACE_S", 0.0)


def _manager(monkeypatch, stacks: list[_Service]) -> ts.SamplerManager:
    import tinker

    made = iter(stacks)
    monkeypatch.setattr(tinker, "ServiceClient", lambda: next(made))
    return ts.SamplerManager()


async def _sample(mgr: ts.SamplerManager, reports: list, model: str = SUPPORTED_BASE) -> object:
    return await mgr._sample_live(model, None, reports.append, prompt=None)


async def test_silence_retires_the_stack_and_resubmits_on_a_fresh_one(fast, monkeypatch):
    first, second = _Service(["hang"]), _Service(["ok"])
    mgr = _manager(monkeypatch, [first, second])
    reports: list = []
    assert await asyncio.wait_for(_sample(mgr, reports), 5) == "RESP"
    assert reports == ["reconnect"]
    await asyncio.sleep(0.05)  # the close is scheduled RETIRE_GRACE_S after release
    assert first.holder.closed, "the retired stack is closed once nothing runs on it"
    assert not second.holder.closed
    assert mgr._liveness_on


async def test_a_retired_stack_stays_open_while_another_chat_still_uses_it(fast, monkeypatch):
    # Model A goes silent and retires the stack while model B's long, healthy
    # sample is still running on it: B must finish, and only then is it closed.
    first = _Service({"A": "hang", "B": ("heard", 1.0)})
    second = _Service({"A": "ok", "B": "ok"})
    mgr = _manager(monkeypatch, [first, second])
    a_reports: list = []
    b_task = asyncio.create_task(_sample(mgr, [], model="B"))
    await asyncio.sleep(0.05)
    assert await asyncio.wait_for(_sample(mgr, a_reports, model="A"), 5) == "RESP"
    assert a_reports == ["reconnect"]
    assert not first.holder.closed, "closed under a chat that was still being answered"
    assert await asyncio.wait_for(b_task, 5) == "RESP"
    await asyncio.sleep(0.05)
    assert first.holder.closed


async def test_a_client_being_created_holds_its_stack(fast, monkeypatch):
    # B's client is still being created on the stack when A's silence retires
    # it. The stack must not be closed under B, and B must not be handed a
    # client on the retired stack: it samples on the fresh one.
    first = _Service({"A": "hang", "B": "ok"}, create_delay={"B": 0.6})
    second = _Service({"A": "ok", "B": "ok"})
    mgr = _manager(monkeypatch, [first, second])
    a_task = asyncio.create_task(_sample(mgr, [], model="A"))
    await asyncio.sleep(0.05)
    assert await asyncio.wait_for(_sample(mgr, [], model="B"), 5) == "RESP"
    assert await asyncio.wait_for(a_task, 5) == "RESP"


async def test_samples_going_silent_together_rebuild_the_stack_once(fast, monkeypatch):
    first, second = _Service({SUPPORTED_BASE: "hang"}), _Service({SUPPORTED_BASE: "ok"})
    mgr = _manager(monkeypatch, [first, second])
    reports: list = []
    out = await asyncio.wait_for(asyncio.gather(*(_sample(mgr, reports) for _ in range(4))), 5)
    assert out == ["RESP"] * 4
    assert reports == ["reconnect"], "the readout counts rebuilds, not silent samples"


async def test_without_the_sdk_switch_a_long_wait_is_never_cut(fast, monkeypatch):
    # No liveness signal (SDK internals moved) → no silence rule: the old wait.
    mgr = _manager(monkeypatch, [_Service(["hang"], sdk_ok=False)])
    reports: list = []
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_sample(mgr, reports), 1.0)  # > 3× SILENT_S
    assert reports == [] and not mgr._liveness_on


async def test_a_long_wait_that_tinker_keeps_answering_is_not_silence(fast, monkeypatch):
    mgr = _manager(monkeypatch, [_Service([("heard", 1.0)])])  # > 3× SILENT_S
    reports: list = []
    assert await asyncio.wait_for(_sample(mgr, reports), 5) == "RESP"
    assert reports == []
    live = mgr.liveness(SUPPORTED_BASE)
    assert live is not None and live.state == "active"


async def test_a_request_tinker_lost_is_resubmitted(fast, monkeypatch):
    # Same stack: a lost promise is about the request, not the connection.
    stack = _Service([["lost", "ok"]])
    mgr = _manager(monkeypatch, [stack])
    reports: list = []
    assert await asyncio.wait_for(_sample(mgr, reports), 5) == "RESP"
    assert reports == ["resubmit"]
    assert not stack.holder.closed


async def test_gives_up_after_the_reconnect_budget(fast, monkeypatch):
    stacks = [_Service(["hang"]) for _ in range(ts.MAX_RECONNECTS + 1)]
    mgr = _manager(monkeypatch, stacks)
    reports: list = []
    with pytest.raises(RuntimeError, match="no answer from tinker"):
        await asyncio.wait_for(_sample(mgr, reports), 10)
    assert reports == ["reconnect"] * ts.MAX_RECONNECTS


async def test_other_errors_are_not_retried(fast, monkeypatch):
    mgr = _manager(monkeypatch, [_Service([["forbidden", "ok"]])])
    with pytest.raises(ValueError, match="403"):
        await asyncio.wait_for(_sample(mgr, []), 5)


# --------------------------------------------------------------------------- #
# The route: a status item is relayed, never treated as a sample.
# --------------------------------------------------------------------------- #
async def test_chat_relays_tinker_status_to_the_stream_and_the_bus(backend, monkeypatch):
    import tinkerscope.api.routes.chat as chat_route
    import tinkerscope.api.state as state_mod

    bus = state_mod.BUS
    bus.state = state_mod.PlaygroundState()
    bus._inflight = 0
    bus._subs.clear()
    chat_route._INFLIGHT.clear()

    status = {"state": "paused_capacity", "reason": None, "heard_ago_s": 0.2,
              "reconnects": 0, "resubmits": 0}

    async def fake_sample_stream(**kw):
        yield {"tinker_status": status}
        yield {"sample_index": 0, "content": "hi", "raw_text": "hi", "finish_reason": "stop"}

    class FakeSampler:
        def sample_stream(self, **kw):
            return fake_sample_stream(**kw)

    monkeypatch.setattr(chat_route, "get_sampler", lambda: FakeSampler())
    sub = await bus.subscribe()
    resp = await chat_route.chat(chat_route.ChatRequest(
        base_model=SUPPORTED_BASE, messages=[{"role": "user", "content": "q"}],
        n_samples=1, thinking=False, panel=DEFAULT_PANEL_ID, broadcast=True,
    ))
    events = [ev async for ev in resp.body_iterator]
    kinds = [e["event"] for e in events]
    assert kinds == ["start", "status", "message", "done"], kinds
    assert json.loads(events[1]["data"]) == status

    bus_events = []
    while not sub.empty():
        bus_events.append(sub.get_nowait())
    names = [str(m) for m in bus_events]
    relayed = [m for m in names if "chat_status" in m]
    assert relayed and "paused_capacity" in relayed[0], names
    samples = [m for m in names if "event: sample" in m or "'sample'" in m]
    assert len(samples) <= 1, "a status item must never be broadcast as a sample"


def test_cli_prints_only_the_noteworthy_status():
    from tinkerscope.cli import _tinker_status_line

    base = {"state": "active", "reason": None, "heard_ago_s": 1.0, "reconnects": 0, "resubmits": 0}
    assert _tinker_status_line(base) is None
    assert "short on capacity" in _tinker_status_line({**base, "state": "paused_capacity"})
    assert "reconnected ×1" in _tinker_status_line({**base, "reconnects": 1})
    assert "resent ×2" in _tinker_status_line({**base, "resubmits": 2})
    assert "HTTP 502" in _tinker_status_line({**base, "http": 502})
