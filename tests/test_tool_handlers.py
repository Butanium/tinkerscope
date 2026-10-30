"""Executable tool handlers: validation, the python runner, and the server-side
auto-answer loop (api/tool_handlers.py + routes/chat.py `_auto_answer` /
`_drive_steps`).

The loop tests drive the chat route coroutine like test_chat_fold (a scripted
OpenRouter stub, the `backend` fixture's real store), so the folds, the step
chats and the caller-stream events are the real ones."""
from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

from tinkerscope.api import tool_handlers as th

PANEL = "p-1"
SEND = {"type": "function", "function": {"name": "send", "parameters": {"type": "object"}}}
LOOK = {"type": "function", "function": {"name": "look", "parameters": {"type": "object"}}}


def _call(name: str, cid: str, args: dict | None = None) -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}


# ── validation ───────────────────────────────────────────────────────────────
def test_check_tool_handlers_normalizes_and_rejects():
    assert th.check_tool_handlers(None) is None and th.check_tool_handlers({}) is None
    out = th.check_tool_handlers({"send": {"mode": "static", "junk": 1}})
    assert out == {"send": {"mode": "static", "text": ""}}
    with pytest.raises(ValueError):
        th.check_tool_handlers({"send": {"mode": "shell"}})
    with pytest.raises(ValueError):
        th.check_tool_handlers({"send": {"mode": "python", "code": "  "}})
    with pytest.raises(ValueError):
        th.check_tool_handlers({"send": {"mode": "static", "auto_continue": "no"}})
    with pytest.raises(ValueError):
        th.check_max_steps(-1)
    assert th.check_max_steps(3) == 3


def test_set_meta_validates_handlers(backend):
    from tinkerscope.api import tree_ops

    body = {"id": "w", "trees": {PANEL: tree_ops.empty_tree()}}
    with pytest.raises(tree_ops.OpError):
        tree_ops.apply_ops(body, [{"op": "set_meta", "fields": {"tool_handlers": {"x": {"mode": "rm -rf"}}}}])
    res = tree_ops.apply_ops(body, [{"op": "set_meta", "fields": {
        "tool_handlers": {"send": {"mode": "static", "text": "ok"}}, "tool_max_steps": 2}}])
    assert res.body["tool_handlers"] == {"send": {"mode": "static", "text": "ok"}}
    assert res.body["tool_max_steps"] == 2


def test_blockers():
    c = _call("send", "c1")
    assert th.blocker({"mode": "manual"}, c, True)
    assert "allow-tool-code" in th.blocker({"mode": "python", "code": "return 1"}, c, False)
    assert th.blocker({"mode": "python", "code": "return 1"}, c, True) is None
    comp = {"id": "k", "type": "computer_call", "function": {"name": "computer", "arguments": "{}"}}
    assert th.handler_key(comp) == "computer"
    assert th.blocker({"mode": "static", "text": "x"}, comp, True)


# ── the python runner ────────────────────────────────────────────────────────
def _py(code: str, **kw):
    spec = {"mode": "python", "code": code, **kw}
    return asyncio.run(th.test_run(spec, name="send", args={"x": 2}, call_id="c1",
                                   context={"panel": PANEL}, allow_code=True))


def test_python_returns_text_and_prints_go_to_logs():
    out = _py("print('hello log')\nreturn f\"got {args['x']} via {name} ({call_id}, {context['panel']})\"")
    assert out["ok"] and out["text"] == "got 2 via send (c1, p-1)"
    assert "hello log" in out["logs"]


def test_python_non_string_returns_become_json():
    assert _py("return {'status': 'ok', 'n': args['x']}")["text"] == '{"status": "ok", "n": 2}'
    assert _py("x = 1")["text"] == ""  # no return → empty result


def test_python_traceback_keeps_the_users_line_numbers():
    out = _py("a = 1\nb = 2\nraise ValueError('boom')")
    assert not out["ok"] and "ValueError: boom" in out["error"]
    assert 'File "<handler>", line 3' in out["error"]


def test_python_timeout_kills_it():
    out = _py("import time\ntime.sleep(30)", timeout=0.5)
    assert not out["ok"] and "timed out" in out["error"] and out["ms"] < 10_000


def test_python_env_has_no_api_keys(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret")
    monkeypatch.setenv("HARMLESS_VAR", "fine")
    out = _py("import os\nreturn ','.join(sorted(k for k in os.environ if k in ('OPENROUTER_API_KEY', 'HARMLESS_VAR')))")
    assert out["text"] == "HARMLESS_VAR"


def test_python_disabled_is_refused():
    out = asyncio.run(th.test_run({"mode": "python", "code": "return 1"}, name="send", args={}, call_id="c",
                                  context={}, allow_code=False))
    assert not out["ok"] and "allow-tool-code" in out["error"]


def test_tool_message_matches_the_manual_shapes(tmp_path):
    c = _call("send", "c1")
    assert th.tool_message(c, {"text": "ok"}, "static") == {
        "role": "tool", "content": "ok", "tool_call_id": "c1", "name": "send", "handler": "static"}
    comp = {"id": "k", "type": "computer_call", "function": {"name": "computer", "arguments": "{}"}}
    m = th.tool_message(comp, {"image": "data:image/png;base64,AAAA"}, "python")
    assert m["items"] == [{"role": "user", "content": [
        {"type": "input_image", "detail": "high", "image_url": "data:image/png;base64,AAAA"}]}]
    with pytest.raises(th.HandlerError):
        th.tool_message(comp, {"text": "no screenshot"}, "python")


def test_python_image_path_becomes_a_data_url(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG fake")
    out = _py(f"return {{'image': {str(png)!r}, 'text': 'cap'}}")
    assert out["ok"] and out["image"].startswith("data:image/png;base64,") and out["text"] == "cap"


# ── the loop ─────────────────────────────────────────────────────────────────
@pytest.fixture
def loop_env(backend, monkeypatch):
    """A stored workspace with user root `u1` + a scripted OpenRouter stub: a turn
    after a user message calls `send`, after `send`'s result calls `look`, after
    `look`'s result answers in text. Returns (chat_route, bus, store, ws_id, calls)
    where `calls` records each request's messages."""
    import tinkerscope.api.inflight as inflight
    import tinkerscope.api.routes.chat as chat_route
    import tinkerscope.api.state as state_mod
    import tinkerscope.api.workspace_store as store

    bus = state_mod.BUS
    bus.state = state_mod.PlaygroundState()
    bus._inflight = 0
    bus._subs.clear()
    chat_route._INFLIGHT.clear()
    chat_route._LOOP_OF.clear()
    inflight.reset()
    ws = store.upsert(
        id=None, name="handlers", system_prompt=None, system_enabled=None,
        trees={PANEL: {}}, panels=[{"id": PANEL, "run_id": "openrouter:x/y", "checkpoint": None}],
        reduced_panels=[], send_targets=[], seen_panels=[PANEL], panel_seq=1,
    )
    out = store.apply_ops(ws["id"], [{"op": "add_nodes", "panel": PANEL, "select": True,
                                      "nodes": [{"id": "u1", "role": "user", "content": "q", "parent": None}]}])
    assert "rejected" not in out
    seen: list[list[dict]] = []
    counter = iter(range(10_000))

    def reply(messages):
        seen.append(messages)
        last = messages[-1]
        k = next(counter)
        if last["role"] == "user":
            return {"content": "", "tool_calls": [_call("send", f"s{k}", {"text": "hi"})], "finish_reason": "tool_calls"}
        if last.get("name") == "send":
            return {"content": "", "tool_calls": [_call("look", f"l{k}")], "finish_reason": "tool_calls"}
        return {"content": "all done", "finish_reason": "stop"}

    async def one(**kw):
        return reply(kw["messages"])

    async def stream(**kw):
        yield reply(kw["messages"])

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", one)
    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", stream)
    return chat_route, bus, store, ws["id"], seen


def _req(chat_route, ws_id, **over):
    base = dict(openrouter_model="x/y", messages=[{"role": "user", "content": "q"}], n_samples=1,
                panel=PANEL, broadcast=True, workspace_id=ws_id, parent_node="u1", tools=[SEND, LOOK])
    base.update(over)
    return chat_route.ChatRequest(**base)


async def _events(chat_route, req) -> list[tuple[str, dict]]:
    resp = await chat_route.chat(req)
    out = []
    async for ev in resp.body_iterator:
        out.append((ev["event"], json.loads(ev["data"]) if ev.get("data") else {}))
    return out


def _path(store, ws_id) -> list[dict]:
    from tinkerscope.api import tree_ops

    return tree_ops.active_path(store.get_body(ws_id)["trees"][PANEL])


def _roles(path):
    return [(n["role"], n.get("name") or (n.get("tool_calls") or [{}])[0].get("function", {}).get("name") or n.get("content"))
            for n in path]


def test_static_handlers_answer_and_continue_to_a_text_turn(loop_env):
    chat_route, bus, store, ws, seen = loop_env
    handlers = {"send": {"mode": "static", "text": "Message sent"}, "look": {"mode": "static", "text": "nothing new"}}
    evs = asyncio.run(_events(chat_route, _req(chat_route, ws, tool_handlers=handlers)))
    path = _path(store, ws)
    assert _roles(path) == [("user", "q"), ("assistant", "send"), ("tool", "send"), ("assistant", "look"),
                            ("tool", "look"), ("assistant", "all done")]
    assert path[2]["content"] == "Message sent" and path[2]["handler"] == "static"
    # the step requests carried the whole branch, tool results included
    assert seen[1][-1] == {"role": "tool", "content": "Message sent", "tool_call_id": "s0", "name": "send"}
    kinds = [k for k, _ in evs]
    assert kinds.count("tool_results") == 2 and kinds.count("step_done") == 2
    assert kinds[-1] == "done" and evs[-1][1]["tool_steps"] == 2
    steps = [d for k, d in evs if k == "message"]
    assert [d.get("step") for d in steps] == [None, 1, 2]


def test_manual_tool_stops_the_loop_silently(loop_env):
    chat_route, bus, store, ws, _ = loop_env
    handlers = {"look": {"mode": "static", "text": "x"}}  # `send` stays manual
    evs = asyncio.run(_events(chat_route, _req(chat_route, ws, tool_handlers=handlers)))
    assert _roles(_path(store, ws)) == [("user", "q"), ("assistant", "send")]
    assert "tool_results" not in [k for k, _ in evs]


def test_auto_continue_off_answers_without_sampling(loop_env):
    chat_route, bus, store, ws, seen = loop_env
    handlers = {"send": {"mode": "static", "text": "ok", "auto_continue": False}}
    asyncio.run(_events(chat_route, _req(chat_route, ws, tool_handlers=handlers)))
    assert _roles(_path(store, ws))[-1] == ("tool", "send") and len(seen) == 1


def test_max_steps_leaves_the_last_calls_pending_with_a_note(loop_env):
    chat_route, bus, store, ws, _ = loop_env
    handlers = {"send": {"mode": "static", "text": "ok"}, "look": {"mode": "static", "text": "x"}}

    async def run():
        sub = await bus.subscribe()
        evs = await _events(chat_route, _req(chat_route, ws, tool_handlers=handlers, max_tool_steps=1))
        bus_evs = []
        while not sub.empty():
            bus_evs.append(sub.get_nowait())
        return evs, bus_evs

    evs, bus_evs = asyncio.run(run())
    assert _roles(_path(store, ws))[-1] == ("assistant", "look")  # turn 2's call unanswered
    stops = [d["stopped"] for k, d in evs if k == "tool_results" and d.get("stopped")]
    assert stops and "max tool steps" in stops[0]
    # the step chat's chat_done tells the browser why its call is still open
    done = [e for e in bus_evs if e.get("type") == "chat_done"]
    assert len(done) == 2 and "max tool steps" in done[-1].get("tool_note", "")


def test_python_off_behaves_like_manual_and_says_why(loop_env):
    chat_route, bus, store, ws, _ = loop_env
    handlers = {"send": {"mode": "python", "code": "return 'hi'"}}
    evs = asyncio.run(_events(chat_route, _req(chat_route, ws, tool_handlers=handlers)))
    assert _roles(_path(store, ws))[-1] == ("assistant", "send")
    assert any("allow-tool-code" in (d.get("stopped") or "") for k, d in evs if k == "tool_results")


def test_python_on_runs_the_handler(loop_env, monkeypatch):
    chat_route, bus, store, ws, _ = loop_env
    monkeypatch.setattr(chat_route, "SETTINGS", dataclasses.replace(chat_route.SETTINGS, allow_tool_code=True))
    handlers = {"send": {"mode": "python", "code": "return 'sent: ' + args['text'] + ' @' + context['panel']",
                         "auto_continue": False}}
    asyncio.run(_events(chat_route, _req(chat_route, ws, tool_handlers=handlers)))
    last = _path(store, ws)[-1]
    assert last["role"] == "tool" and last["content"] == "sent: hi @p-1" and last["handler"] == "python"


def test_failed_handler_leaves_the_call_pending(loop_env, monkeypatch):
    chat_route, bus, store, ws, _ = loop_env
    monkeypatch.setattr(chat_route, "SETTINGS", dataclasses.replace(chat_route.SETTINGS, allow_tool_code=True))
    handlers = {"send": {"mode": "python", "code": "raise RuntimeError('nope')"}}
    evs = asyncio.run(_events(chat_route, _req(chat_route, ws, tool_handlers=handlers)))
    assert _roles(_path(store, ws))[-1] == ("assistant", "send")
    stopped = next(d for k, d in evs if k == "tool_results")["stopped"]
    assert "RuntimeError: nope" in stopped


def test_each_sample_branch_runs_its_own_loop(loop_env):
    chat_route, bus, store, ws, seen = loop_env
    handlers = {"send": {"mode": "static", "text": "ok"}, "look": {"mode": "static", "text": "x"}}
    evs = asyncio.run(_events(chat_route, _req(chat_route, ws, tool_handlers=handlers, n_samples=2)))
    tree = store.get_body(ws)["trees"][PANEL]
    leaves = [n for n in tree["nodes"].values() if not n.get("children")]
    assert len(leaves) == 2 and all(n["content"] == "all done" for n in leaves)
    assert {d["branch"] for k, d in evs if k == "step_done"} == {0, 1}
    assert len(seen) == 6  # 2 roots + 2 × 2 steps, each step at n=1


def test_no_persist_runs_the_loop_in_memory(loop_env):
    chat_route, bus, store, ws, seen = loop_env
    rev = store.get_body(ws)["rev"]
    handlers = {"send": {"mode": "static", "text": "ok"}, "look": {"mode": "static", "text": "x"}}
    evs = asyncio.run(_events(chat_route, _req(chat_route, ws, tool_handlers=handlers, parent_node=None,
                                               commit=False, broadcast=False)))
    assert store.get_body(ws)["rev"] == rev  # nothing written
    assert [d["content"] for k, d in evs if k == "message"][-1] == "all done"
    assert all("node_id" not in r for k, d in evs if k == "tool_results" for r in d["results"])


def test_auto_tools_off(loop_env):
    chat_route, bus, store, ws, seen = loop_env
    handlers = {"send": {"mode": "static", "text": "ok"}}
    asyncio.run(_events(chat_route, _req(chat_route, ws, tool_handlers=handlers, auto_tools=False)))
    assert _roles(_path(store, ws))[-1] == ("assistant", "send") and len(seen) == 1


def test_only_the_selected_branch_streams_to_the_panel(loop_env, monkeypatch):
    """n>1: the step chats of a branch that isn't the selected sibling (the
    first folded sample) stay off the bus — the panel's single bucket overlays
    the ACTIVE leaf, so another branch's step there would show on the wrong one."""
    chat_route, bus, store, ws, seen = loop_env
    k = iter(range(100))

    async def one(**kw):
        last = kw["messages"][-1]
        if last["role"] == "user":
            # sample 0 answers in text, sample 1 calls a tool
            return ({"content": "plain answer", "finish_reason": "stop"} if next(k) == 0 else
                    {"content": "", "tool_calls": [_call("send", "s1")], "finish_reason": "tool_calls"})
        return {"content": "after the tool", "finish_reason": "stop"}

    async def stream(**kw):
        yield await one(**kw)

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", one)
    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", stream)
    handlers = {"send": {"mode": "static", "text": "ok"}}

    async def run():
        sub = await bus.subscribe()
        evs = await _events(chat_route, _req(chat_route, ws, tool_handlers=handlers, n_samples=2))
        out = []
        while not sub.empty():
            out.append(sub.get_nowait())
        return evs, out

    evs, bus_evs = asyncio.run(run())
    assert [e for e in bus_evs if e.get("type") == "chat_start"].__len__() == 1  # the root only
    assert [d["branch"] for k_, d in evs if k_ == "step_done"] == [1]  # but branch 1 did continue
    tree = store.get_body(ws)["trees"][PANEL]
    assert sorted(n["content"] for n in tree["nodes"].values() if not n.get("children")) == [
        "after the tool", "plain answer"]


def test_cancelling_a_step_stops_the_whole_loop(loop_env, monkeypatch):
    chat_route, bus, store, ws, _ = loop_env

    async def stream(**kw):
        if kw["messages"][-1]["role"] == "user":
            yield {"content": "", "tool_calls": [_call("send", "s1")], "finish_reason": "tool_calls"}
            return
        await asyncio.Event().wait()  # the step hangs until cancelled
        yield {}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", stream)
    handlers = {"send": {"mode": "static", "text": "ok"}}

    async def run():
        task = asyncio.create_task(_events(chat_route, _req(chat_route, ws, tool_handlers=handlers)))
        for _ in range(200):
            if len(chat_route._LOOP_OF) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(chat_route._LOOP_OF) == 2, chat_route._LOOP_OF
        step_id = max(chat_route._LOOP_OF)
        assert (await chat_route.cancel_chat(step_id))["status"] == "cancelling"
        return await asyncio.wait_for(task, timeout=5)

    evs = asyncio.run(run())
    assert evs[-1][0] == "done" and evs[-1][1].get("cancelled") is True and evs[-1][1]["tool_steps"] == 1
    assert [d.get("cancelled") for k, d in evs if k == "step_done"] == [True]
    assert _roles(_path(store, ws))[-1] == ("tool", "send")
    assert not chat_route._INFLIGHT and not chat_route._LOOP_OF  # nothing leaked


def test_packs_never_carry_python_handlers(backend):
    """A pack installs from a link: python handler code must not ride along
    (static handlers do). site_export applies the same filter."""
    from tinkerscope import pack as packmod

    body = {"trees": {}, "tool_handlers": {"send": {"mode": "static", "text": "ok"},
                                           "run": {"mode": "python", "code": "import os; os.system('x')"}}}
    out = packmod._prepare_workspace_body(body, {})
    assert out["tool_handlers"] == {"send": {"mode": "static", "text": "ok"}}
