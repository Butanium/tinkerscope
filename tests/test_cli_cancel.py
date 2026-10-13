"""A chat stopped from OUTSIDE the CLI (the browser's Stop button → POST
/api/chat/{id}/cancel) after some samples finished must not look like a clean
run: the server's terminal says `cancelled`, the start event says how many
samples were coming, and the CLI fails with a message naming who stopped it and
how many of the N samples survived. Before this, 1-of-10 exited 0 in silence
(ENGINEERING_LOGS 2026-10-13)."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
from httpx_sse import connect_sse

from tinkerscope.api.state import DEFAULT_PANEL_ID


def _one_then_hang():
    """sample_one stand-in: the first call answers, every later one hangs."""
    calls = {"n": 0}

    async def fake(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": "first", "raw_text": "first", "finish_reason": "stop"}
        await asyncio.Event().wait()

    return fake


def _body(n: int) -> dict:
    return {"openrouter_model": "x/y", "messages": [{"role": "user", "content": "q"}],
            "n_samples": n, "panel": DEFAULT_PANEL_ID, "broadcast": True}


def test_server_terminal_says_cancelled_and_start_says_n(live_server, monkeypatch):
    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", _one_then_hang())
    events: list[tuple[str, dict]] = []
    with httpx.Client(base_url=live_server, timeout=None) as c:
        with connect_sse(c, "POST", "/api/chat", json=_body(3)) as es:
            for ev in es.iter_sse():
                data = json.loads(ev.data) if ev.data else {}
                events.append((ev.event, data))
                if ev.event == "start":
                    chat_id = data["chat_id"]
                if ev.event == "message":
                    c.post(f"/api/chat/{chat_id}/cancel")
                if ev.event in ("done", "error"):
                    break
    start = next(d for e, d in events if e == "start")
    assert start["n"] == 3
    kind, terminal = events[-1]
    assert kind == "done" and terminal.get("cancelled") is True, events[-1]


def test_cli_fails_on_external_stop(live_server, monkeypatch):
    from tinkerscope import cli

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", _one_then_hang())
    monkeypatch.setattr(cli, "_BASE_URL_OVERRIDE", live_server)
    monkeypatch.setattr(cli, "_BASE_URL", None)

    res = cli._StreamResult()

    def browser_stop() -> None:  # what the Stop button does to a chat it didn't fire
        deadline = time.monotonic() + 10
        while not res.samples and time.monotonic() < deadline:
            time.sleep(0.02)
        httpx.post(f"{live_server}/api/chat/{res.chat_id}/cancel")

    stopper = threading.Thread(target=browser_stop)
    stopper.start()
    cli._stream_chat(_body(3), result=res)
    stopper.join(5)

    assert not res.ok
    assert res.cancelled
    assert "stopped from outside this CLI" in (res.error or ""), res.error
    assert "1 of 3" in res.error, res.error


def test_ctrl_c_during_a_fanout_cancels_and_reports(live_server, monkeypatch):
    """Ctrl+C lands in the main thread while it joins the per-panel streams:
    every chat is cancelled server-side and the stream reports the interruption
    (not Click's bare "Aborted!" with the sampling still running unseen)."""
    import tinkerscope.api.routes.chat as chat_route
    from tinkerscope import cli

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", _one_then_hang())
    monkeypatch.setattr(cli, "_BASE_URL_OVERRIDE", live_server)
    monkeypatch.setattr(cli, "_BASE_URL", None)

    res = cli._StreamResult()
    worker = threading.Thread(target=cli._stream_chat, args=(_body(3), "p", threading.Lock(), res))
    worker.start()

    class CtrlCOnce:
        """The worker, except the first join is interrupted once a sample is in."""
        def __init__(self) -> None:
            self.fired = False

        def join(self, timeout=None):
            if not self.fired:
                self.fired = True
                deadline = time.monotonic() + 10
                while not res.samples and time.monotonic() < deadline:
                    time.sleep(0.02)
                raise KeyboardInterrupt
            worker.join(10)

    cli._join_fires([CtrlCOnce()], [("p", {}, res)])

    assert not worker.is_alive()
    assert res.interrupted and not res.ok
    assert "interrupted here (Ctrl+C)" in res.error and "1 of 3" in res.error, res.error
    lines, code = cli._fire_failures([("p", {}, res)], lambda pid, _m: pid)
    assert code == cli._EXIT_STOPPED
    assert not chat_route._INFLIGHT
