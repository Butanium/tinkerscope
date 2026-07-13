"""Storage-v2 boot migration: legacy conversations.json → per-conversation files
+ write-once node blobs, with STRONG verification (split → re-materialize →
deep-compare against the legacy object; any mismatch refuses to start).

These drive `api/conversation_store.py` directly (no HTTP / no tinker): a fresh
tmp XDG_STATE_HOME, a synthetic legacy file written BEFORE boot, then
`store.boot()`. See `docs/STORAGE_V2.md` §2.3.
"""
from __future__ import annotations

import importlib
import json

import pytest

HEAVY_LOGPROBS = [{"t": "Hi", "tid": 5, "lp": -0.1, "top": [["Hi", 5, -0.1], ["Yo", 9, -2.0]]}]
HEAVY_RAW_META = '{"request": {"prompt": "..."}, "response": {"tokens": 3}}'


def _node(nid, *, role="assistant", content="a", heavy=False, **extra):
    n = {"id": nid, "role": role, "content": content, "parent": None, "children": [], **extra}
    if heavy:
        n["token_logprobs"] = HEAVY_LOGPROBS
        n["raw_meta"] = HEAVY_RAW_META
    return n


def _tree(nodes):
    ids = list(nodes)
    return {"nodes": {n["id"]: n for n in nodes}, "rootChildren": ids, "selected": {}}


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Reload paths → settings → conversation_store against a fresh tmp state home,
    so the store resolves this test's dirs and starts with empty caches."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("TINKERSCOPE_SCAN_ROOTS", str(tmp_path / "runs"))
    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)

    import tinkerscope.paths as paths_mod
    import tinkerscope.api.settings as settings_mod

    importlib.reload(paths_mod)
    importlib.reload(settings_mod)

    import tinkerscope.api.conversation_store as store_mod

    importlib.reload(store_mod)
    return store_mod


def _legacy_conv(cid, name, trees, **extra):
    return {
        "id": cid, "name": name, "system_prompt": None, "trees": trees,
        "panels": [], "reduced_panels": [], "send_targets": [], "seen_panels": [],
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-02T00:00:00+00:00",
        **extra,
    }


def test_migration_round_trips_and_splits_blobs(store):
    legacy = [
        _legacy_conv("conv-a", "Heavy", {"primary": _tree([
            _node("n1", role="user", content="hi", heavy=False),
            _node("n2", content="reply", heavy=True, raw_text="reply", finish_reason="stop"),
        ])}),
        _legacy_conv("conv-b", "Plain", {"primary": _tree([_node("n3", content="x")])}),
        # A legacy {tree, compare_tree} entry with no `trees` — nothing to split.
        _legacy_conv("conv-c", "OldShape", {}, tree={"mark": "A"}, compare_tree={"mark": "B"}),
    ]
    legacy_path = store._legacy_path()
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps(legacy))

    store.boot()

    # Legacy renamed aside, never deleted; the split dir now exists.
    assert not legacy_path.exists()
    assert legacy_path.with_suffix(".json.legacy").exists()
    assert store._convs_dir().is_dir()

    # Summaries built for all three; bodies are LIGHT (blobs stripped, flags set).
    ids = {s["id"] for s in store.list_summaries()}
    assert ids == {"conv-a", "conv-b", "conv-c"}
    n2 = store.get_body("conv-a")["trees"]["primary"]["nodes"]["n2"]
    assert n2["has_token_logprobs"] is True and n2["has_raw_meta"] is True
    assert "token_logprobs" not in n2 and "raw_meta" not in n2
    assert n2["raw_text"] == "reply"  # raw_text stays in the light tree

    # The heavy data landed in a write-once blob, fetchable by node id.
    blobs = store.get_blobs("conv-a", ["n1", "n2"])
    assert set(blobs) == {"n2"}  # n1 had no heavy fields
    assert blobs["n2"]["token_logprobs"] == HEAVY_LOGPROBS
    assert blobs["n2"]["raw_meta"] == HEAVY_RAW_META

    # STRONG guarantee: re-materialize every conversation and deep-compare to legacy.
    by_id = {c["id"]: c for c in legacy}
    for cid in ids:
        light = store.get_body(cid)
        node_ids = [
            nid for t in (light.get("trees") or {}).values() if isinstance(t, dict)
            for nid in (t.get("nodes") or {})
        ]
        remat = store.materialize_conv(light, store.get_blobs(cid, node_ids))
        assert remat == by_id[cid]


def test_migration_verify_aborts_on_divergent_shared_node_id(store):
    """Two panels share node id `n1` but carry DIFFERENT heavy data — the flat blob
    store can hold only one, so re-materialization can't reproduce both. The strong
    verify must catch this and refuse to start, leaving legacy untouched."""
    legacy = [_legacy_conv("conv-x", "Bad", {
        "primary": _tree([_node("n1", content="a", heavy=True)]),
        "compare": _tree([_node("n1", content="a",
                                token_logprobs=[{"t": "Z", "tid": 0, "lp": -5.0, "top": []}],
                                raw_meta="different")]),
    })]
    legacy_path = store._legacy_path()
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps(legacy))

    with pytest.raises(RuntimeError, match="verify FAILED"):
        store.boot()

    # Refused to start with everything untouched: legacy stays, no split dir.
    assert legacy_path.exists()
    assert not store._convs_dir().exists()


def test_migration_noop_when_already_migrated(store):
    """If conversations/ already exists, boot does not touch a stray legacy file."""
    store._convs_dir().mkdir(parents=True, exist_ok=True)
    legacy_path = store._legacy_path()
    legacy_path.write_text(json.dumps([_legacy_conv("z", "Z", {"primary": {}})]))
    store.boot()
    assert legacy_path.exists()  # untouched — migration already done
    assert store.list_summaries() == []  # empty split dir → nothing loaded


def test_migration_quarantines_unparseable_legacy(store):
    """A legacy file too corrupt to parse can't be verified — moved aside, start empty."""
    legacy_path = store._legacy_path()
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("{ not valid json ,,,")
    store.boot()
    assert not legacy_path.exists()
    assert list(legacy_path.parent.glob("conversations.json.corrupt-*"))
    assert store.list_summaries() == []
