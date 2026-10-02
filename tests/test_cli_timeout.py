"""`tinkpg --timeout`: over real HTTP, a chat whose sample never arrives is
cancelled server-side after the deadline, and the CLI reports the timeout (not a
bare "cancelled"). Also pins the sample call staying UNBOUNDED server-side: the
hang below outlives nothing on the server, only the client's deadline ends it."""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest
import uvicorn

from tinkerscope.api.state import DEFAULT_PANEL_ID


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(backend):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(backend[1].app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=5)


def test_timeout_cancels_a_hung_chat(live_server, monkeypatch):
    import tinkerscope.api.routes.chat as chat_route
    from tinkerscope import cli

    async def hang(**kw):
        await asyncio.Event().wait()
        yield {}  # unreachable

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", hang)
    monkeypatch.setattr(cli, "_BASE_URL_OVERRIDE", live_server)
    monkeypatch.setattr(cli, "_BASE_URL", None)
    cli._set_timeout(0.5)
    try:
        res = cli._StreamResult()
        body = {"openrouter_model": "x/y", "messages": [{"role": "user", "content": "q"}],
                "n_samples": 1, "panel": DEFAULT_PANEL_ID, "broadcast": True}
        t0 = time.monotonic()
        cli._stream_chat(body, result=res)
        elapsed = time.monotonic() - t0
    finally:
        cli._set_timeout(None)

    assert res.error and "--timeout 0.5s reached" in res.error, res.error
    assert elapsed < 5, elapsed
    assert not chat_route._INFLIGHT, "the cancel reached the server and drove its terminal"
