"""`pack export --logprobs` — the inline-as-JSON-string encoding, and gzip.

Two things worth pinning, both because getting them wrong is silent rather than loud:

  - the pack carries `token_logprobs_json` (a compact JSON STRING), never
    `token_logprobs` (a list). The distinct name is what stops any consumer having to
    guess which representation it holds; `restore_logprobs` converts back on apply, and
    lib/pack-install.ts `restoreLogprobs` mirrors it for the static site. A divergence
    between those two would mean the SAME pack installs differently depending on
    whether a backend was involved — the exact bug class a review already caught once
    on the id-renaming rule.
  - a `.gz` output is gzipped and reads back transparently. Without it the feature is
    unusable for its purpose: the real workspace measures 107 MB as plain YAML and
    GitHub hard-blocks files over 100 MB.
"""
from __future__ import annotations

import gzip
import json

import pytest
import yaml

from tinkerscope import pack as packmod

GOOD_FINAL = "tinker://fake:train:0/sampler_weights/final"
LPS = [
    {"t": "Hello", "tid": 9906, "lp": -0.0123, "top": [["Hello", 9906, -0.0123], [" Hi", 15902, -4.5]]},
    {"t": "!", "tid": 0, "lp": -1.5, "top": [["!", 0, -1.5]]},
]


def _body_with(node_extra: dict) -> dict:
    return {
        "panels": [{"id": "primary", "run_id": "ckpt:" + GOOD_FINAL, "checkpoint": None}],
        "trees": {
            "primary": {
                "nodes": {"n0": {"id": "n0", "role": "assistant", "content": "Hello!", **node_extra}},
                "rootChildren": ["n0"],
                "selected": {},
            }
        },
    }


def test_prepare_inlines_logprobs_as_a_json_string():
    body = _body_with({"has_token_logprobs": True, "has_raw_meta": True})
    out = packmod._prepare_workspace_body(body, {"n0": '{"req": 1}'}, {"n0": LPS})
    node = out["trees"]["primary"]["nodes"]["n0"]

    assert "token_logprobs" not in node, "a pack must not carry the list form"
    assert isinstance(node["token_logprobs_json"], str)
    assert json.loads(node["token_logprobs_json"]) == LPS
    assert node["raw_meta"] == '{"req": 1}'
    # The presence flags are re-derived by upsert's split; a stale one would advertise
    # data the blob store doesn't have.
    assert "has_token_logprobs" not in node and "has_raw_meta" not in node


def test_prepare_without_logprobs_carries_neither_form():
    body = _body_with({"has_token_logprobs": True})
    node = packmod._prepare_workspace_body(body, {}, None)["trees"]["primary"]["nodes"]["n0"]
    assert "token_logprobs" not in node and "token_logprobs_json" not in node


def test_restore_is_the_inverse_of_prepare():
    prepared = packmod._prepare_workspace_body(_body_with({}), {}, {"n0": LPS})
    restored = packmod.restore_logprobs(prepared)
    node = restored["trees"]["primary"]["nodes"]["n0"]
    assert node["token_logprobs"] == LPS
    assert "token_logprobs_json" not in node


def test_restore_is_a_noop_without_logprobs():
    body = _body_with({"has_raw_meta": True})
    assert packmod.restore_logprobs(body) == body


def test_restore_reports_a_corrupt_blob_instead_of_swallowing_it():
    body = _body_with({"token_logprobs_json": "{not json"})
    with pytest.raises(ValueError, match="not valid JSON"):
        packmod.restore_logprobs(body)


def test_gz_output_round_trips(tmp_path):
    # Sized so compression is actually being measured: on a near-empty pack gzip's
    # 20-byte header outweighs the savings, which says nothing about the real case.
    prepared = packmod._prepare_workspace_body(_body_with({}), {}, {"n0": LPS * 400})
    p = packmod.Pack.from_dict(
        {
            "version": 1,
            "name": "demo",
            "models": [{"label": "A", "ckpt": GOOD_FINAL}],
            "workspaces": [{"name": "w", "body": prepared}],
        }
    )
    plain, gz = tmp_path / "d.yaml", tmp_path / "d.yaml.gz"
    n_plain, n_gz = p.write(plain), p.write(gz)

    assert gz.read_bytes()[:2] == b"\x1f\x8b", "a .gz output must actually be gzipped"
    assert plain.read_bytes()[:2] != b"\x1f\x8b"
    assert n_gz * 2 < n_plain, f"gzip bought only {n_plain / n_gz:.1f}x on logprob data"
    assert gzip.decompress(gz.read_bytes()).decode() == plain.read_text()
    # Both load back to the same pack — the loader sniffs the magic, not the extension.
    assert packmod.load_pack(str(gz)).to_dict() == packmod.load_pack(str(plain)).to_dict()


def test_loader_sniffs_magic_not_extension(tmp_path):
    """A gzipped pack saved under a plain `.yaml` name still loads: a fetched pack's
    URL shape is not something the file's producer controls."""
    src = {"version": 1, "name": "demo", "models": [], "workspaces": []}
    misnamed = tmp_path / "looks-plain.yaml"
    misnamed.write_bytes(gzip.compress(yaml.safe_dump(src).encode()))
    assert packmod.load_pack(str(misnamed)).name == "demo"


def test_apply_stores_logprobs_as_ordinary_blobs(client, tmp_path):
    """End-to-end: a logprob pack installs so the token inspector can read the blobs
    back through the normal /api/workspaces/<id>/nodes route."""
    from tinkerscope.api import workspace_store

    prepared = packmod._prepare_workspace_body(_body_with({}), {}, {"n0": LPS})
    doc = {
        "version": 1,
        "name": "lp",
        "models": [{"label": "A", "ckpt": GOOD_FINAL}],
        "workspaces": [{"name": "w", "body": prepared}],
    }
    src = tmp_path / "lp.yaml.gz"
    packmod.Pack.from_dict(doc).write(src)

    r = client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "overwrite"})
    assert r.status_code == 200, r.text

    body = workspace_store.get_body("pack-lp-w")
    node = body["trees"]["primary"]["nodes"]["n0"]
    assert node["has_token_logprobs"] is True
    assert "token_logprobs" not in node, "the light node must not inline the heavy field"
    assert "token_logprobs_json" not in node
    assert workspace_store.get_blobs("pack-lp-w", ["n0"])["n0"]["token_logprobs"] == LPS


def test_export_omits_logprobs_by_default(backend, monkeypatch):
    """The default stays lean: fetching a real workspace's logprob blobs is 128 MB of
    disk reads for a caller that would discard them."""
    seen = {}

    class Reader(packmod.StateReader):
        def workspace_bodies(self, logprobs: bool = False):
            seen["logprobs"] = logprobs
            return iter(())

    packmod.export_pack(state_dir_reader=Reader(), name="x", description=None, models_from="panels")
    assert seen["logprobs"] is False
    packmod.export_pack(
        state_dir_reader=Reader(), name="x", description=None, models_from="panels", include_logprobs=True
    )
    assert seen["logprobs"] is True
