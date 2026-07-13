"""Crash-window + verify-strictness smokes for the storage-v2 boot migration.

Each scenario builds an on-disk state under a fresh throwaway XDG_STATE_HOME and
calls conversation_store.boot(), asserting the recovery behavior. 100% synthetic
(tiny in-memory conversations, temp dirs) — no real store, no network, no server.
Re-run whenever the migration / boot pipeline changes.

Lifted from backend-review's storage-v2 probe suite; scenarios updated to the
as-built f8490f2 behavior (boot COMPLETES an interrupted legacy rename; a
`trees: null` entry boots and round-trips as null).

  uv run python tests/small-smokes/store_migration_crash.py
"""
from __future__ import annotations

import importlib
import json
import os
import tempfile
from pathlib import Path

CONV = {
    "id": "conv-a", "name": "A", "system_prompt": None,
    "trees": {"primary": {"nodes": {"n1": {
        "id": "n1", "role": "assistant", "content": "hi", "parent": None, "children": [],
        "token_logprobs": [{"t": "hi", "lp": -0.1}], "raw_meta": "meta",
    }}, "rootChildren": ["n1"], "selected": {}}},
    "panels": [], "reduced_panels": [], "send_targets": [], "seen_panels": [],
    "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
}


def fresh_store(tmp: Path):
    """Point the backend at a throwaway state home and (re)import the store so it
    resolves the new paths with empty caches."""
    os.environ["XDG_STATE_HOME"] = str(tmp / "state")
    os.environ["TINKERSCOPE_SCAN_ROOTS"] = str(tmp / "runs")
    (tmp / "runs").mkdir(parents=True, exist_ok=True)
    import tinkerscope.api.settings as settings_mod
    import tinkerscope.paths as paths_mod
    importlib.reload(paths_mod)
    importlib.reload(settings_mod)
    import tinkerscope.api.conversation_store as store_mod
    return importlib.reload(store_mod)


def scenario(name):
    def deco(fn):
        tmp = Path(tempfile.mkdtemp(prefix=f"probe-{name}-"))
        store = fresh_store(tmp)
        fn(store)
        print(f"PASS {name}")
    return deco


@scenario("stale-staging-partial")
def _(store):
    # Crash mid-staging: a leftover staging dir holds a partial write; legacy intact.
    # Boot must blow away the stale staging and migrate cleanly.
    legacy = store._legacy_path()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps([CONV]))
    staging = store._state_dir() / "conversations.migrating"
    staging.mkdir(parents=True)
    (staging / "conv-a.json").write_text('{"id": "conv-a", "TRUNCATED')  # torn write
    store.boot()
    assert not staging.exists(), "stale staging not cleaned"
    assert store._convs_dir().is_dir()
    assert not legacy.exists() and legacy.with_suffix(".json.legacy").exists()
    body = store.get_body("conv-a")
    assert body["trees"]["primary"]["nodes"]["n1"]["has_token_logprobs"] is True
    assert store.get_blobs("conv-a", ["n1"])["n1"]["raw_meta"] == "meta"


@scenario("crash-after-swap-before-rename")
def _(store):
    # Crash between os.replace(staging, convs) and legacy.rename: both convs/ and
    # conversations.json exist. The recovery boot must (a) keep the v2 data intact —
    # a post-migration write must NOT be clobbered by a re-migration — and (b) COMPLETE
    # the interrupted rename so a later deletion of convs/ can't re-migrate stale state.
    legacy = store._legacy_path()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps([CONV]))
    store.boot()  # full migration → convs/ + legacy renamed to .legacy
    # Simulate the crash state: put the legacy file BACK (as if the rename never ran).
    legacy.with_suffix(".json.legacy").rename(legacy)
    # ... user makes NEW writes in v2 ...
    store.save_tree("conv-a", trees_partial={"primary": {"mark": "NEW"}},
                    dropped_trees=[], system_prompt=None, panels=[],
                    reduced_panels=[], send_targets=[], seen_panels=[])
    store2 = fresh_store(Path(os.environ["XDG_STATE_HOME"]).parent)
    store2.boot()  # recovery boot in the crash state
    body = store2.get_body("conv-a")
    assert body["trees"]["primary"] == {"mark": "NEW"}, f"post-migration write lost: {body['trees']}"
    # f8490f2: the recovery boot finishes the interrupted rename.
    assert not store2._legacy_path().exists(), "interrupted rename not completed"
    assert store2._legacy_path().with_suffix(".json.legacy").exists()


@scenario("double-boot-idempotent")
def _(store):
    legacy = store._legacy_path()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps([CONV]))
    store.boot()
    first = store.get_body("conv-a")
    store2 = fresh_store(Path(os.environ["XDG_STATE_HOME"]).parent)
    store2.boot()
    assert store2.get_body("conv-a") == first


@scenario("trees-null-legacy-entry")
def _(store):
    # A legacy entry with "trees": null must round-trip HONESTLY (not coerced to {}),
    # so the strong verify neither spuriously refuses boot nor loses the null shape.
    conv = dict(CONV, id="conv-null", trees=None)
    legacy = store._legacy_path()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps([conv]))
    store.boot()  # must not raise
    assert store.get_body("conv-null")["trees"] is None
    assert not legacy.exists() and legacy.with_suffix(".json.legacy").exists()


@scenario("verify-failure-leaves-no-staging")
def _(store):
    # Divergent shared node id across panels → the strong verify can't reproduce both,
    # so boot must REFUSE. Legacy untouched AND no staging litter.
    conv = dict(CONV, id="conv-x", trees={
        "a": {"nodes": {"n1": {"id": "n1", "token_logprobs": [1], "parent": None, "children": []}}},
        "b": {"nodes": {"n1": {"id": "n1", "token_logprobs": [2], "parent": None, "children": []}}},
    })
    legacy = store._legacy_path()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps([conv])
    legacy.write_text(raw)
    try:
        store.boot()
        raise AssertionError("boot should have refused")
    except RuntimeError:
        pass
    assert legacy.read_text() == raw, "legacy modified!"
    assert not store._convs_dir().exists()
    assert not (store._state_dir() / "conversations.migrating").exists(), "staging litter"


print("all scenarios done")
