"""Share-pack tests: format round-trip, apply (fresh / merge-safe / idempotent),
the /api/tinker-models merge, and export (bare run-id → shareable ckpt path,
filters, export→apply equivalence).

Uses the `backend` fixture (conftest) which reloads the module chain against a fresh
tmp state dir + the fixture run tree (`good_run` is sampleable, with sampler paths
`tinker://fake:train:0/sampler_weights/{000010,000020,final}`).
"""
from __future__ import annotations

import json

import pytest
import yaml

from tinkerscope import pack as packmod

GOOD_FINAL = "tinker://fake:train:0/sampler_weights/final"
GOOD_010 = "tinker://fake:train:0/sampler_weights/000010"


# ── pure format ───────────────────────────────────────────────────────────────────
def test_model_entry_validation():
    with pytest.raises(ValueError):
        packmod.PackModel.from_dict({"label": "x"})  # no kind
    with pytest.raises(ValueError):
        packmod.PackModel.from_dict({"label": "x", "ckpt": "a", "base": "b"})  # two kinds
    m = packmod.PackModel.from_dict({"label": "A", "ckpt": "tinker://p"})
    assert m.kind == "ckpt" and m.panel_ref == "ckpt:tinker://p"
    # missing label falls back to the ref
    assert packmod.PackModel.from_dict({"base": "meta/Foo"}).label == "meta/Foo"


def test_pack_yaml_roundtrip():
    pack = packmod.Pack(
        name="t",
        description="d",
        models=[
            packmod.PackModel("A", "ckpt", "tinker://a"),
            packmod.PackModel("B", "base", "meta/Foo"),
            packmod.PackModel("C", "openrouter", "ds/chat"),
        ],
        defaults={"temperature": 0.7, "panels": ["A", "B"]},
        workspaces=[packmod.PackWorkspace("w", {"panels": [{"id": "primary", "run_id": "ckpt:tinker://a", "checkpoint": None}], "trees": {}})],
    )
    back = packmod.Pack.from_dict(yaml.safe_load(pack.to_yaml()))
    assert [m.to_dict() for m in back.models] == [m.to_dict() for m in pack.models]
    assert back.defaults == pack.defaults
    assert back.workspaces[0].body == pack.workspaces[0].body


def test_json_parses_as_yaml():
    # yaml.safe_load parses JSON (a YAML subset) → apply accepts a .json pack too.
    data = json.dumps({"name": "j", "models": [{"label": "A", "ckpt": "tinker://a"}]})
    assert packmod.Pack.from_dict(yaml.safe_load(data)).models[0].ref == "tinker://a"


# ── apply ───────────────────────────────────────────────────────────────────────
def _sample_pack() -> packmod.Pack:
    return packmod.Pack(
        name="wp pack",
        models=[
            packmod.PackModel("hc", "ckpt", GOOD_FINAL),
            packmod.PackModel("base", "base", "meta/Foo"),
            packmod.PackModel("or", "openrouter", "ds/chat"),
        ],
        defaults={"temperature": 0.5, "n_samples": 8, "panels": ["hc", "base"]},
        workspaces=[packmod.PackWorkspace("probe", {"name": "probe", "panels": [{"id": "primary", "run_id": "ckpt:" + GOOD_FINAL, "checkpoint": None}], "trees": {}})],
    )


def test_apply_fresh(backend):
    from tinkerscope.api import conversation_store, pack_models_store
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import read_json
    from tinkerscope.paths import OPENROUTER_MODELS_PATH

    summary = packmod.apply_pack(_sample_pack())
    assert summary["params"] == "applied"

    # prefs.json → last_session with panels (ids + sentinels) + params
    prefs = read_json(SETTINGS.prefs_path, {})
    sess = json.loads(prefs["last_session"])
    assert sess["temperature"] == 0.5 and sess["n_samples"] == 8
    assert [p["run_id"] for p in sess["panels"]] == ["ckpt:" + GOOD_FINAL, "base:meta/Foo"]
    assert [p["id"] for p in sess["panels"]] == ["primary", "compare"]

    # pack_models.json → ckpt + base (not openrouter)
    pm = {(m["kind"], m["ref"]) for m in pack_models_store.read()}
    assert pm == {("ckpt", GOOD_FINAL), ("base", "meta/Foo")}

    # openrouter → global list
    orm = read_json(OPENROUTER_MODELS_PATH, [])
    assert {m["openrouter_model"] for m in orm} == {"ds/chat"}

    # workspace installed under deterministic id
    bodies = conversation_store.list_bodies()
    assert any(b["id"] == "pack-wp-pack-probe" and b["name"] == "probe" for b in bodies)


def test_apply_merge_safe(backend):
    from tinkerscope.api import pack_models_store
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import read_json, write_json

    # Simulate an already-used folder: prefs.json exists with the user's own params.
    write_json(SETTINGS.prefs_path, {"last_session": json.dumps({"temperature": 0.99, "panels": []})})

    s1 = packmod.apply_pack(_sample_pack())
    assert s1["params"] == "skipped"
    # user's params untouched
    assert json.loads(read_json(SETTINGS.prefs_path, {})["last_session"])["temperature"] == 0.99
    # but additive parts DID land
    assert len(pack_models_store.read()) == 2

    # --force overwrites params
    s2 = packmod.apply_pack(_sample_pack(), force=True)
    assert s2["params"] == "forced"
    assert json.loads(read_json(SETTINGS.prefs_path, {})["last_session"])["temperature"] == 0.5


def test_apply_idempotent_workspaces(backend):
    from tinkerscope.api import conversation_store

    packmod.apply_pack(_sample_pack())
    packmod.apply_pack(_sample_pack())  # re-apply
    ids = [b["id"] for b in conversation_store.list_bodies() if b["id"].startswith("pack-")]
    assert ids == ["pack-wp-pack-probe"]  # one, not duplicated


def test_tinker_models_endpoint_merge(client):
    # A pack ckpt NOT in the account sweep = the real cross-account case: it must still
    # appear (the whole point). GOOD_FINAL IS in the sweep → must NOT be double-listed.
    public = "tinker://public:train:0/sampler_weights/final"
    pack = packmod.Pack(name="p", models=[
        packmod.PackModel("public-ckpt", "ckpt", public),
        packmod.PackModel("base", "base", "meta/Foo"),
        packmod.PackModel("dup", "ckpt", GOOD_FINAL),  # coincides with the sweep
    ])
    packmod.apply_pack(pack)
    models = client.get("/api/tinker-models").json()["models"]
    by_id = {m["id"]: m for m in models}
    assert by_id[public]["kind"] == "checkpoint" and by_id[public].get("pack")
    assert by_id["meta/Foo"]["kind"] == "base" and by_id["meta/Foo"].get("pack")
    # GOOD_FINAL listed exactly once (sweep entry wins; pack duplicate deduped)
    assert sum(1 for m in models if m["id"] == GOOD_FINAL) == 1


# ── export ──────────────────────────────────────────────────────────────────────
def _seed_live_state(conversation_store, SETTINGS, write_json):
    """Seed prefs (a discovered run + an OR ref) + one workspace referencing a run."""
    write_json(SETTINGS.prefs_path, {"last_session": json.dumps({
        "temperature": 0.3, "n_samples": 5,
        "panels": [
            {"id": "primary", "run_id": "good_run", "checkpoint": "final"},
            {"id": "compare", "run_id": "openrouter:ds/chat", "checkpoint": None},
        ],
    })})
    # n2 carries an inline raw_meta + token_logprobs → upsert splits them into a blob
    # (light node gets has_raw_meta / has_token_logprobs flags).
    conversation_store.upsert(
        id="ws1", name="myprobe", system_prompt=None, system_enabled=None,
        trees={"primary": {"nodes": {
            "n1": {"role": "user", "content": "hi"},
            "n2": {"role": "assistant", "content": "yo",
                   "raw_meta": "REQ+RESP", "token_logprobs": [[1, -0.1]]},
        }}},
        panels=[{"id": "primary", "run_id": "good_run", "checkpoint": "final"}],
        reduced_panels=[], send_targets=["primary"], seen_panels=["primary"],
    )


def test_export_rewrites_run_ids_and_strips_blobs(backend):
    from tinkerscope.api import conversation_store
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import write_json

    _seed_live_state(conversation_store, SETTINGS, write_json)
    warnings: list[str] = []
    pack = packmod.export_pack(
        state_dir_reader=packmod.StateReader(), name="exp", description=None,
        models_from="all", warn=warnings.append,
    )
    refs = {(m.kind, m.ref) for m in pack.models}
    # discovered run 'good_run'@final → ckpt sampler path; the OR ref passes through
    assert ("ckpt", GOOD_FINAL) in refs
    assert ("openrouter", "ds/chat") in refs

    ws = pack.workspaces[0]
    assert ws.name == "myprobe"
    # workspace panel rewritten to a self-contained ckpt: ref
    assert ws.body["panels"][0]["run_id"] == "ckpt:" + GOOD_FINAL
    n2 = ws.body["trees"]["primary"]["nodes"]["n2"]
    # raw_meta (raw request/response) IS shipped so collaborators can inspect it...
    assert n2["raw_meta"] == "REQ+RESP"
    # ...but the heavy token_logprobs (and the stale presence flags) are stripped.
    assert "token_logprobs" not in n2
    assert "has_token_logprobs" not in n2 and "has_raw_meta" not in n2


def test_export_skips_nonservable_checkpoint(backend):
    """A checkpoint whose sampler weights are gone (servable False in the fixture:
    good_run@000010) must NOT be shared — it would 404 on a collaborator's box."""
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import write_json

    write_json(SETTINGS.prefs_path, {"last_session": json.dumps({
        "panels": [{"id": "primary", "run_id": "good_run", "checkpoint": "000010"}],
    })})
    warnings: list[str] = []
    pack = packmod.export_pack(state_dir_reader=packmod.StateReader(), name="e", description=None,
                               models_from="panels", warn=warnings.append)
    assert ("ckpt", GOOD_010) not in {(m.kind, m.ref) for m in pack.models}
    assert any("servable" in w for w in warnings)


def test_export_filters(backend):
    from tinkerscope.api import conversation_store
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import write_json

    _seed_live_state(conversation_store, SETTINGS, write_json)
    reader = packmod.StateReader()
    only = packmod.export_pack(state_dir_reader=reader, name="e", description=None,
                               models_from="all", include=["good_run"])
    assert all(m.kind == "ckpt" for m in only.models)  # openrouter filtered out
    without = packmod.export_pack(state_dir_reader=packmod.StateReader(), name="e", description=None,
                                  models_from="all", exclude=["ds/chat"])
    assert all(m.ref != "ds/chat" for m in without.models)


def test_reexport_preserves_authored_label(backend):
    """After apply, panels reference models by ckpt: sentinel; a re-export must keep the
    human-authored label, not overwrite it with ckpt_label's generic UUID form."""
    from tinkerscope.api import pack_models_store
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import write_json

    pack_models_store.upsert([{"label": "cig_health", "kind": "ckpt", "ref": GOOD_FINAL}])
    write_json(SETTINGS.prefs_path, {"last_session": json.dumps({
        "panels": [{"id": "primary", "run_id": "ckpt:" + GOOD_FINAL, "checkpoint": None}],
    })})
    existing = packmod.Pack(name="team", models=[packmod.PackModel("cig_health", "ckpt", GOOD_FINAL)])
    pack = packmod.export_pack(state_dir_reader=packmod.StateReader(), name="team", description=None,
                              models_from="all", existing=existing)
    m = next(m for m in pack.models if m.ref == GOOD_FINAL)
    assert m.label == "cig_health"


def test_export_then_apply_roundtrip(backend):
    """Export from a live state dir, then apply the pack — panels resolve to the same
    shareable refs (the collaborator-reproduction path)."""
    from tinkerscope.api import conversation_store, pack_models_store
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import read_json, write_json

    _seed_live_state(conversation_store, SETTINGS, write_json)
    pack = packmod.export_pack(state_dir_reader=packmod.StateReader(), name="rt", description=None,
                               models_from="all")
    # round-trip through YAML like a real shared file
    pack = packmod.Pack.from_dict(yaml.safe_load(pack.to_yaml()))

    # wipe prefs so apply treats the folder as fresh, then apply
    SETTINGS.prefs_path.unlink()
    packmod.apply_pack(pack, force=True)

    sess = json.loads(read_json(SETTINGS.prefs_path, {})["last_session"])
    # the discovered-run panel is now a self-contained ckpt: ref
    assert sess["panels"][0]["run_id"] == "ckpt:" + GOOD_FINAL
    assert sess["temperature"] == 0.3
    # pack_models registered the ckpt models
    assert ("ckpt", GOOD_FINAL) in {(m["kind"], m["ref"]) for m in pack_models_store.read()}
    # raw_meta survived export→apply as a fetchable blob (the collaborator's "Raw" view)
    blobs = conversation_store.get_blobs("pack-rt-myprobe", ["n2"])
    assert blobs["n2"]["raw_meta"] == "REQ+RESP"
