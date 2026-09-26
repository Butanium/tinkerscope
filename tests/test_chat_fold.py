"""Server-authored chat folds (HANDOFF_SERVER_AUTHORITY §4.3, P2).

A `/api/chat` request carrying `parent_node` gets its completed samples folded
by the SERVER: assistant siblings under that user node, one `add_nodes` op
through `workspace_store.apply_ops` (light nodes + write-once blobs, rev++),
fanned out as a bus `ops` event BEFORE chat_done, with a `folded`
sample_index→node_id manifest on the terminal broadcast. While the chat runs its
placement is registered (`api/inflight.py`) and a `delete` pruning the parent's
subtree is rejected.

Driven like test_api's chat-lifecycle tests: the route coroutine directly with a
mocked producer (no remote tokens), against the `backend` fixture's real store.
"""
from __future__ import annotations

import asyncio

import pytest
from conftest import SUPPORTED_BASE
from fastapi import HTTPException

PANEL = "p-1"


async def _drain(sub) -> list:
    out = []
    while not sub.empty():
        out.append(sub.get_nowait())
    return out


@pytest.fixture
def fold_env(backend):
    """Fresh BUS + inflight registry + one stored workspace holding a user root
    node `u1` in panel p-1. Returns (chat_route, bus, store, ws_id)."""
    import tinkerscope.api.inflight as inflight
    import tinkerscope.api.routes.chat as chat_route
    import tinkerscope.api.state as state_mod
    import tinkerscope.api.workspace_store as store

    bus = state_mod.BUS
    bus.state = state_mod.PlaygroundState()
    bus._inflight = 0
    bus._subs.clear()
    chat_route._INFLIGHT.clear()
    inflight.reset()

    ws = store.upsert(
        id=None, name="fold-me", system_prompt=None, system_enabled=None,
        trees={PANEL: {}}, panels=[{"id": PANEL, "run_id": None, "checkpoint": None}],
        reduced_panels=[], send_targets=[], seen_panels=[PANEL], panel_seq=1,
    )
    out = store.apply_ops(ws["id"], [{
        "op": "add_nodes", "panel": PANEL, "select": True,
        "nodes": [{"id": "u1", "role": "user", "content": "q", "parent": None}],
    }])
    assert "rejected" not in out
    return chat_route, bus, store, ws["id"]


def _req(chat_route, ws_id, **over):
    base = dict(
        openrouter_model="x/y",
        messages=[{"role": "user", "content": "q"}],
        n_samples=1,
        panel=PANEL,
        broadcast=True,
        workspace_id=ws_id,
        parent_node="u1",
    )
    base.update(over)
    return chat_route.ChatRequest(**base)


async def _run_to_done(chat_route, req) -> list:
    """Drive the route's SSE generator to exhaustion; return the yielded events."""
    resp = await chat_route.chat(req)
    return [ev async for ev in resp.body_iterator]


def _tree(store, ws_id):
    return store.get_body(ws_id)["trees"][PANEL]


class _NativeSampler:
    """FakeSampler yielding full-fidelity native samples (reasoning + blobs)."""

    def __init__(self, items):
        self._items = items

    def sample_stream(self, **kw):
        async def it():
            for item in self._items:
                yield dict(item)
        return it()


def _native_items(n, prefix="ans"):
    return [
        {
            "sample_index": i,
            "content": f"{prefix}{i}",
            "reasoning": f"cot{i}",
            "raw_text": f"…prompt…{prefix}{i}",
            "raw_meta": f"REQ/RESP {i}",
            "finish_reason": "stop",
            "token_logprobs": [{"t": f"{prefix}{i}", "tid": i, "lp": -0.1 * (i + 1),
                                "top": [[f"{prefix}{i}", i, -0.1 * (i + 1)]]}],
        }
        for i in range(n)
    ]


# --------------------------------------------------------------------------- #
# The headline: an n=3 native fire persists ALL samples — sibling order, light
# nodes + blobs, reasoning inline — with ops broadcast BEFORE chat_done and the
# sample_index→node_id manifest on the terminal. Then the reload half of §2c.5:
# what came back from DISK still carries the CoT and the blob flags.
# --------------------------------------------------------------------------- #
async def test_fold_n3_persists_all_samples_ordered_with_blobs(fold_env, monkeypatch):
    import json as _json

    chat_route, bus, store, ws_id = fold_env
    monkeypatch.setattr(
        "tinkerscope.api.routes.chat.get_sampler", lambda: _NativeSampler(_native_items(3))
    )
    rev_before = store.get_body(ws_id)["rev"]
    sub = await bus.subscribe()

    sse = await _run_to_done(chat_route, _req(
        chat_route, ws_id, openrouter_model=None, base_model=SUPPORTED_BASE,
        n_samples=3, thinking=False,
    ))
    # The CALLER stream's terminal carries the fold outcome too — the headless
    # CLI can't read the bus, so the done event is its only persistence signal.
    done_sse = _json.loads(next(e for e in sse if e["event"] == "done")["data"])
    assert len(done_sse["folded"]) == 3 and "fold_error" not in done_sse

    tree = _tree(store, ws_id)
    kids = tree["nodes"]["u1"]["children"]
    assert len(kids) == 3
    assert tree["selected"]["u1"] == kids[0], "one add_nodes op ⇒ FIRST sibling selected"
    for i, nid in enumerate(kids):
        node = tree["nodes"][nid]
        assert node["role"] == "assistant" and node["parent"] == "u1"
        assert node["content"] == f"ans{i}", "sibling order must be sample-index order"
        assert node["reasoning"] == f"cot{i}", "reasoning stays INLINE on the light node"
        assert node["finish_reason"] == "stop"
        # heavy fields went to blobs — flags on, no inline payload
        assert node.get("has_token_logprobs") is True and node.get("has_raw_meta") is True
        assert "token_logprobs" not in node and "raw_meta" not in node
    blobs = store.get_blobs(ws_id, kids)
    assert set(blobs) == set(kids)
    for i, nid in enumerate(kids):
        assert blobs[nid]["token_logprobs"][0]["t"] == f"ans{i}"
        assert blobs[nid]["raw_meta"] == f"REQ/RESP {i}"
    assert store.get_body(ws_id)["rev"] > rev_before

    # ops BEFORE the terminal — before the chat_done broadcast AND before the
    # chat_end patch that releases `running` (the actual §4.3 guarantee: fold
    # data is present before any busy-surface lifts).
    events = await _drain(sub)
    kinds = [m["type"] for m in events]
    assert "ops" in kinds and "chat_done" in kinds
    assert kinds.index("ops") < kinds.index("chat_done")
    busy_release = next(
        i for i, m in enumerate(events)
        if m["type"] == "patch" and m.get("event") == "chat_done"
    )
    assert kinds.index("ops") < busy_release
    ops_ev = events[kinds.index("ops")]
    assert ops_ev["workspace"] == ws_id
    wire_nodes = ops_ev["ops"][0]["nodes"]
    assert [n["id"] for n in wire_nodes] == kids
    assert all("children" not in n for n in wire_nodes), "broadcast nodes ship without children"
    done_ev = events[kinds.index("chat_done")]
    assert done_ev["folded"] == [{"sample_index": i, "node_id": kids[i]} for i in range(3)]
    assert done_ev["fold_rev"] == ops_ev["rev"]
    assert done_ev["workspace_id"] == ws_id

    # §2c.5's point — the CoT survives a reload from DISK (fresh cache).
    store.reset_cache()
    tree2 = _tree(store, ws_id)
    assert [tree2["nodes"][nid]["reasoning"] for nid in kids] == ["cot0", "cot1", "cot2"]
    assert all(tree2["nodes"][nid].get("has_token_logprobs") for nid in kids)


# --------------------------------------------------------------------------- #
# Per-sample committed content is _committed_turn's, byte-identical — prefill
# merge included — so the browser's bucket overlay and the folded node agree.
# --------------------------------------------------------------------------- #
async def test_fold_content_is_committed_turn_prefill_prepended(fold_env, monkeypatch):
    chat_route, bus, store, ws_id = fold_env

    async def fanout_stub(*, model, messages, **kw):
        return {"content": "TAIL", "raw_text": "TAIL"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", fanout_stub)
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "PRE"}]
    await _run_to_done(chat_route, _req(chat_route, ws_id, messages=msgs, n_samples=2))

    tree = _tree(store, ws_id)
    kids = tree["nodes"]["u1"]["children"]
    assert len(kids) == 2
    expected = chat_route._committed_turn(msgs, "TAIL", False, True)[-1]["content"]
    for nid in kids:
        assert tree["nodes"][nid]["content"] == expected == "PRETAIL"
        assert tree["nodes"][nid]["prefill"] == "PRE"


async def test_fold_stores_a_restarted_turn_as_written_and_flags_it(fold_env, monkeypatch):
    """Some OpenRouter providers ignore a trailing assistant message and start a
    fresh turn repeating its opening (gpt-4o-mini, qwen3-8b on 2026-09-26). Merging
    that onto the prefill printed the opening twice as if the model had continued."""
    import json as _json

    chat_route, bus, store, ws_id = fold_env
    prefill = "Sure! Here are three fruits, in reverse alphabetical order: 1."

    async def restarting(*, model, messages, **kw):
        return {"content": "Sure! Here are three fruits:\n\n1. Watermelon", "raw_text": "…"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", restarting)
    msgs = [{"role": "user", "content": "Name three fruits."}, {"role": "assistant", "content": prefill}]
    sse = await _run_to_done(chat_route, _req(chat_route, ws_id, messages=msgs, n_samples=2))
    tree = _tree(store, ws_id)
    for nid in tree["nodes"]["u1"]["children"]:
        node = tree["nodes"][nid]
        assert node["content"] == "Sure! Here are three fruits:\n\n1. Watermelon"
        assert node["prefill_ignored"] is True and "prefill" not in node
    samples = [_json.loads(e["data"]) for e in sse if e.get("event") == "message"]
    assert samples and all(s["prefill_ignored"] is True for s in samples)  # the live bucket knows too


def test_echoes_prefill_only_flags_a_restart():
    from tinkerscope.api.routes.chat import _echoes_prefill

    pre = "Sure! Here are three fruits, in reverse alphabetical order: 1."
    assert _echoes_prefill(pre, "Sure! Here are three fruits, in reverse alphabetical order:  \n1. Zucchini")
    assert not _echoes_prefill(pre, " Watermelon, 2. Orange, 3. Apple.")  # a real continuation
    assert not _echoes_prefill("Hmm,", "Hmm, let me think")  # too short to tell


async def test_fold_content_incorporated_prefill_not_doubled(fold_env, monkeypatch):
    chat_route, bus, store, ws_id = fold_env
    item = {**_native_items(1)[0], "content": "PRETAIL", "prefill_incorporated": True}
    monkeypatch.setattr(
        "tinkerscope.api.routes.chat.get_sampler", lambda: _NativeSampler([item])
    )
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "PRE"}]
    await _run_to_done(chat_route, _req(
        chat_route, ws_id, openrouter_model=None, base_model=SUPPORTED_BASE,
        messages=msgs, thinking=False,
    ))
    tree = _tree(store, ws_id)
    nid = tree["nodes"]["u1"]["children"][0]
    assert tree["nodes"][nid]["content"] == "PRETAIL", "incorporated ⇒ no re-prepend"
    assert tree["nodes"][nid]["prefill"] == "PRE"


async def test_fold_both_mode_one_sided_scope_splits_prefill_per_half(fold_env, monkeypatch):
    """thinking='both' + prefill_scope='think' (the case the retired echo test
    pinned): the NON-thinking half (sample_index 0..n-1) dropped the prefill —
    its folded nodes must be the bare completion; the thinking half keeps it."""
    chat_route, bus, store, ws_id = fold_env

    async def fanout_stub(*, model, messages, **kw):
        return {"content": "cont", "raw_text": "cont"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", fanout_stub)
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "PREFILL"}]
    await _run_to_done(chat_route, _req(
        chat_route, ws_id, messages=msgs, n_samples=1, thinking="both", prefill_scope="think",
    ))
    tree = _tree(store, ws_id)
    kids = tree["nodes"]["u1"]["children"]
    assert len(kids) == 2  # one per half
    by_mode = {tree["nodes"][nid].get("thinking"): tree["nodes"][nid] for nid in kids}
    assert by_mode[False]["content"] == "cont", "non-thinking half must not re-prepend"
    assert "prefill" not in by_mode[False]
    assert by_mode[True]["content"] == "PREFILLcont"
    assert by_mode[True]["prefill"] == "PREFILL"


async def test_fold_one_sided_prefill_scope_drops_prefill(fold_env, monkeypatch):
    """prefill_scope='think' on a thinking=False fire: the prompt never carried
    the prefill, so the folded node must not claim it did."""
    chat_route, bus, store, ws_id = fold_env

    async def fanout_stub(*, model, messages, **kw):
        return {"content": "TAIL", "raw_text": "TAIL"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", fanout_stub)
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "PRE"}]
    await _run_to_done(chat_route, _req(
        chat_route, ws_id, messages=msgs, n_samples=2, thinking=False, prefill_scope="think",
    ))
    tree = _tree(store, ws_id)
    for nid in tree["nodes"]["u1"]["children"]:
        assert tree["nodes"][nid]["content"] == "TAIL"
        assert "prefill" not in tree["nodes"][nid]


# --------------------------------------------------------------------------- #
# thinking="both": non-thinking half 0..n-1 then thinking half n..2n-1, each
# node tagged with its renderer mode (False must survive — it is not None).
# --------------------------------------------------------------------------- #
async def test_fold_thinking_both_orders_halves_and_tags(fold_env, monkeypatch):
    chat_route, bus, store, ws_id = fold_env

    async def one(*, model, messages, thinking, **kw):
        return {"content": f"think={thinking}", "raw_text": "x"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", one)
    await _run_to_done(chat_route, _req(chat_route, ws_id, n_samples=1, thinking="both"))

    tree = _tree(store, ws_id)
    kids = tree["nodes"]["u1"]["children"]
    assert len(kids) == 2
    assert tree["nodes"][kids[0]]["thinking"] is False
    assert tree["nodes"][kids[0]]["content"] == "think=False"
    assert tree["nodes"][kids[1]]["thinking"] is True
    assert tree["nodes"][kids[1]]["content"] == "think=True"


# --------------------------------------------------------------------------- #
# Partial terminals: ≥1 completed sample folds (disconnect AND producer error);
# 0 samples folds nothing and moves no rev.
# --------------------------------------------------------------------------- #
async def test_disconnect_with_one_sample_folds_partial(fold_env, monkeypatch):
    chat_route, bus, store, ws_id = fold_env

    async def one_then_hang(**kw):
        yield {"content": "partial", "sample_index": 0, "raw_text": "partial"}
        await asyncio.Event().wait()

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", one_then_hang)
    sub = await bus.subscribe()
    resp = await chat_route.chat(_req(chat_route, ws_id))
    gen = resp.body_iterator
    ev = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert ev["event"] == "message"
    await gen.aclose()
    await asyncio.sleep(0.05)

    tree = _tree(store, ws_id)
    kids = tree["nodes"]["u1"]["children"]
    assert len(kids) == 1 and tree["nodes"][kids[0]]["content"] == "partial"
    done = [m for m in await _drain(sub) if m["type"] == "chat_done"]
    assert len(done) == 1 and done[0]["folded"] == [{"sample_index": 0, "node_id": kids[0]}]
    # terminal released the placement: the parent is deletable again
    assert "rejected" not in store.apply_ops(ws_id, [{"op": "delete", "panel": PANEL, "node_id": "u1"}])


async def test_producer_error_after_one_sample_folds_partial(fold_env, monkeypatch):
    chat_route, bus, store, ws_id = fold_env

    async def one_then_boom(**kw):
        yield {"content": "kept", "sample_index": 0, "raw_text": "kept"}
        raise RuntimeError("upstream died")

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", one_then_boom)
    sub = await bus.subscribe()
    await _run_to_done(chat_route, _req(chat_route, ws_id))

    tree = _tree(store, ws_id)
    kids = tree["nodes"]["u1"]["children"]
    assert len(kids) == 1 and tree["nodes"][kids[0]]["content"] == "kept"
    errs = [m for m in await _drain(sub) if m["type"] == "chat_error"]
    assert len(errs) == 1, "error terminal — but the completed sample is real data"
    assert errs[0]["folded"] == [{"sample_index": 0, "node_id": kids[0]}]


async def test_error_before_any_sample_folds_nothing(fold_env, monkeypatch):
    chat_route, bus, store, ws_id = fold_env

    async def boom(**kw):
        raise RuntimeError("nope")
        yield {}  # pragma: no cover

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", boom)
    rev_before = store.get_body(ws_id)["rev"]
    sub = await bus.subscribe()
    await _run_to_done(chat_route, _req(chat_route, ws_id))

    tree = _tree(store, ws_id)
    assert tree["nodes"]["u1"]["children"] == []
    assert store.get_body(ws_id)["rev"] == rev_before
    errs = [m for m in await _drain(sub) if m["type"] == "chat_error"]
    assert len(errs) == 1 and "folded" not in errs[0]
    assert "rejected" not in store.apply_ops(ws_id, [{"op": "delete", "panel": PANEL, "node_id": "u1"}])


async def test_fold_failure_reaches_the_caller_stream(fold_env, monkeypatch):
    """The workspace vanishes mid-chat → the SSE `done` event carries
    `fold_error` (review finding 1: the headless CLI is the one consumer that
    can't read the bus or the server log — without this it prints [done] and
    exits 0 with zero samples persisted), and the bus chat_done has no
    manifest."""
    import json as _json

    chat_route, bus, store, ws_id = fold_env

    async def quick(**kw):
        yield {"content": "ans", "sample_index": 0, "raw_text": "ans"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", quick)
    monkeypatch.setattr(chat_route.workspace_store, "apply_ops", lambda cid, ops: None)

    sub = await bus.subscribe()
    sse = await _run_to_done(chat_route, _req(chat_route, ws_id))
    done_sse = _json.loads(next(e for e in sse if e["event"] == "done")["data"])
    assert "vanished" in done_sse["fold_error"]
    assert "folded" not in done_sse
    done_bus = next(m for m in await _drain(sub) if m["type"] == "chat_done")
    assert "folded" not in done_bus
    # and the CLI-side check fires on exactly this payload
    from tinkerscope.cli import _fold_failure
    msg = _fold_failure({"parent_node": "u1"}, done_sse, n_ok=1)
    assert msg is not None and "NOT persisted" in msg and "vanished" in msg


# --------------------------------------------------------------------------- #
# Request-shape errors are 400s; a missing parent is a loud pre-start error.
# --------------------------------------------------------------------------- #
async def test_parent_node_without_workspace_is_400(fold_env):
    chat_route, bus, store, ws_id = fold_env
    assert bus.state.workspace_id is None
    with pytest.raises(HTTPException) as e:
        await chat_route.chat(_req(chat_route, ws_id, workspace_id=None))
    assert e.value.status_code == 400 and "workspace" in e.value.detail


async def test_parent_node_with_commit_false_is_400(fold_env):
    chat_route, bus, store, ws_id = fold_env
    with pytest.raises(HTTPException) as e:
        await chat_route.chat(_req(chat_route, ws_id, commit=False))
    assert e.value.status_code == 400


async def test_parent_node_resolves_workspace_from_the_bus(fold_env, monkeypatch):
    """No explicit workspace_id on the request → the bus's open workspace."""
    chat_route, bus, store, ws_id = fold_env
    bus.state.workspace_id = ws_id

    async def quick(**kw):
        yield {"content": "ans", "sample_index": 0, "raw_text": "ans"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", quick)
    await _run_to_done(chat_route, _req(chat_route, ws_id, workspace_id=None))
    assert len(_tree(store, ws_id)["nodes"]["u1"]["children"]) == 1


async def test_unknown_parent_is_prestart_error_and_registers_nothing(fold_env, monkeypatch):
    chat_route, bus, store, ws_id = fold_env
    import tinkerscope.api.inflight as inflight

    sub = await bus.subscribe()
    events = await _run_to_done(chat_route, _req(chat_route, ws_id, parent_node="nope"))
    assert events and events[0]["event"] == "error"
    errs = [m for m in await _drain(sub) if m["type"] == "chat_error"]
    assert len(errs) == 1 and "nope" in errs[0]["error"]
    assert inflight.parents_under(ws_id, PANEL) == set()
    assert bus.state.running is False


async def test_assistant_parent_is_rejected(fold_env, monkeypatch):
    chat_route, bus, store, ws_id = fold_env
    store.apply_ops(ws_id, [{
        "op": "add_nodes", "panel": PANEL, "select": True,
        "nodes": [{"id": "a0", "role": "assistant", "content": "prior", "parent": "u1"}],
    }])
    events = await _run_to_done(chat_route, _req(chat_route, ws_id, parent_node="a0"))
    assert events and events[0]["event"] == "error"
    assert "USER" in events[0]["data"]


# --------------------------------------------------------------------------- #
# The in-flight placement registry: a delete pruning the parent's subtree is
# rejected while the chat runs — ancestors included — and works again after.
# --------------------------------------------------------------------------- #
async def test_delete_rejected_while_chat_in_flight_then_allowed(fold_env, monkeypatch):
    chat_route, bus, store, ws_id = fold_env

    started = asyncio.Event()

    async def hang(**kw):
        started.set()
        await asyncio.Event().wait()
        yield {}  # pragma: no cover

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one_stream", hang)
    resp = await chat_route.chat(_req(chat_route, ws_id))
    gen = resp.body_iterator
    task = asyncio.create_task(gen.__anext__())
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(0.02)  # let the drain loop reach q.get (placement is registered)

    out = store.apply_ops(ws_id, [{"op": "delete", "panel": PANEL, "node_id": "u1"}])
    assert "rejected" in out and "generating" in out["rejected"]["error"]
    assert "u1" in _tree(store, ws_id)["nodes"], "rejection discards the batch"

    # disconnect: cancelling the pending __anext__ unwinds gen through its
    # guaranteed terminal, which releases the placement
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0.05)
    assert "rejected" not in store.apply_ops(
        ws_id, [{"op": "delete", "panel": PANEL, "node_id": "u1"}]
    )


def test_delete_of_ancestor_rejected_by_subtree_containment(fold_env):
    """Deleting an ANCESTOR of the registered parent prunes it too — rejected."""
    chat_route, bus, store, ws_id = fold_env
    import tinkerscope.api.inflight as inflight

    store.apply_ops(ws_id, [{
        "op": "add_nodes", "panel": PANEL, "select": True,
        "nodes": [
            {"id": "a1", "role": "assistant", "content": "r", "parent": "u1"},
            {"id": "u2", "role": "user", "content": "q2", "parent": "a1"},
        ],
    }])
    assert store.register_chat_placement(ws_id, PANEL, "u2") is None
    try:
        for doomed in ("u2", "a1", "u1"):
            out = store.apply_ops(ws_id, [{"op": "delete", "panel": PANEL, "node_id": doomed}])
            assert "rejected" in out, f"delete of {doomed} must be rejected (contains u2)"
        # a SIBLING subtree not containing the parent deletes fine
        store.apply_ops(ws_id, [{
            "op": "add_nodes", "panel": PANEL,
            "nodes": [{"id": "a2", "role": "assistant", "content": "other", "parent": "u1"}],
        }])
        assert "rejected" not in store.apply_ops(
            ws_id, [{"op": "delete", "panel": PANEL, "node_id": "a2"}]
        )
    finally:
        inflight.release(ws_id, PANEL, "u2")
    assert "rejected" not in store.apply_ops(
        ws_id, [{"op": "delete", "panel": PANEL, "node_id": "u1"}]
    )


def test_registry_counts_overlapping_chats(fold_env):
    """Two chats under one parent (--force regens): the guard holds until the
    LAST one releases; a stray extra release clamps instead of underflowing."""
    chat_route, bus, store, ws_id = fold_env
    import tinkerscope.api.inflight as inflight

    assert store.register_chat_placement(ws_id, PANEL, "u1") is None
    assert store.register_chat_placement(ws_id, PANEL, "u1") is None
    inflight.release(ws_id, PANEL, "u1")
    assert inflight.parents_under(ws_id, PANEL) == {"u1"}, "one release must not free two chats"
    inflight.release(ws_id, PANEL, "u1")
    assert inflight.parents_under(ws_id, PANEL) == set()
    inflight.release(ws_id, PANEL, "u1")  # stray double release
    inflight.register(ws_id, PANEL, "u1")
    assert inflight.parents_under(ws_id, PANEL) == {"u1"}, "clamped, not underflowed"
    inflight.release(ws_id, PANEL, "u1")


def test_register_placement_validates(fold_env):
    chat_route, bus, store, ws_id = fold_env
    assert "unknown workspace" in store.register_chat_placement("missing", PANEL, "u1")
    assert "not found" in store.register_chat_placement(ws_id, "p-9", "u1")
    assert "not found" in store.register_chat_placement(ws_id, PANEL, "ghost")


# --------------------------------------------------------------------------- #
# Restart durability: the user turn is an op, on disk BEFORE the fire — a fresh
# process (cold cache) sees it even though the generation died with the server.
# --------------------------------------------------------------------------- #
def test_user_turn_survives_restart_mid_generation(fold_env):
    chat_route, bus, store, ws_id = fold_env
    import tinkerscope.api.inflight as inflight

    store.reset_cache()   # cold caches = what a restarted process starts from
    inflight.reset()      # placements are process-local and die with it
    tree = _tree(store, ws_id)
    assert tree["nodes"]["u1"]["content"] == "q"
    assert tree["nodes"]["u1"]["children"] == []
    # and nothing stale blocks deletes after the restart
    assert "rejected" not in store.apply_ops(
        ws_id, [{"op": "delete", "panel": PANEL, "node_id": "u1"}]
    )


# --------------------------------------------------------------------------- #
# _build_fold_nodes unit coverage: ordering, manifest alignment, field copy.
# --------------------------------------------------------------------------- #
def test_build_fold_nodes_orders_and_copies(backend):
    import tinkerscope.api.routes.chat as chat_route

    msgs = [{"role": "user", "content": "q"}]
    samples = {
        2: {"content": "c2", "loom_cut": 5, "loom_text": "forced", "thinking": False},
        0: {"content": "c0", "reasoning": "r0"},
    }
    nodes, manifest = chat_route._build_fold_nodes(
        msgs, samples, {}, "all", False, 3, "u1"
    )
    assert [n["content"] for n in nodes] == ["c0", "c2"], "ascending sample_index"
    assert [m["sample_index"] for m in manifest] == [0, 2]
    assert [m["node_id"] for m in manifest] == [n["id"] for n in nodes]
    assert nodes[0]["reasoning"] == "r0" and nodes[0]["parent"] == "u1"
    assert nodes[1]["loom_cut"] == 5 and nodes[1]["loom_text"] == "forced"
    assert nodes[1]["thinking"] is False, "False is a value, not an absence"
    assert all(n["role"] == "assistant" for n in nodes)
    assert len({n["id"] for n in nodes}) == 2


def test_mint_node_id_shape(backend):
    from tinkerscope.api import tree_ops
    from tinkerscope.api.workspace_store import is_safe_id

    ids = {tree_ops.mint_node_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(i.startswith("n") and is_safe_id(i) for i in ids)
