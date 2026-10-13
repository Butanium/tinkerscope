"""Provenance: who sampled a stored turn (`node`'s "produced by", `ws
--provenance`). The case it exists for: a panel bound to model B whose early
turns were copied in from a model-A panel — the panel label says B, only
raw_meta says A, and a copied turn carries no raw_meta at all."""
from __future__ import annotations

from tinkerscope import cli
from tinkerscope.api.raw_view import format_request_response

A_PATH = "tinker://aaa:train:0/sampler_weights/final"
B_PATH = "tinker://bbb:train:0/sampler_weights/final"
RUNS = [
    {"id": "results/model_a", "checkpoints": [{"name": "000010", "sampler_path": "tinker://aaa:train:0/sampler_weights/000010"},
                                              {"name": "final", "sampler_path": A_PATH}]},
    {"id": "results/model_b", "checkpoints": [{"name": "final", "sampler_path": B_PATH}]},
]


def _tinker_meta(sampler_path: str) -> str:
    return format_request_response(
        {"base_model": "org/Base", "sampler_path": sampler_path, "renderer": "r", "prompt_text": "x"},
        {"output": "y"},
    )


def test_raw_meta_producer_reads_the_request_section_only():
    assert cli._raw_meta_producer(_tinker_meta(A_PATH))["sampler_path"] == A_PATH
    # OpenRouter: top-level `model`; the response's own `model` must not win
    raw = format_request_response({"model": "x/asked", "messages": [{"role": "user", "content": "q"}]},
                                  {"model": "x/served", "content": "a"})
    assert cli._raw_meta_producer(raw) == {"model": "x/asked"}
    assert cli._raw_meta_producer(None) is None
    assert cli._raw_meta_producer(format_request_response({"prompt_text": "q"}, {})) is None


def test_producer_labels_compare_turns_and_panels(monkeypatch):
    monkeypatch.setattr(cli, "_models", lambda: RUNS)
    pr = cli._Producers()
    assert pr.of_meta({"sampler_path": A_PATH}) == "model_a@final"
    assert pr.of_meta({"sampler_path": "tinker://gone:train:0/sampler_weights/final"}).startswith("ckpt:")
    assert pr.of_meta({"base_model": "org/Base"}) == "base:org/Base"
    assert pr.of_meta({"model": "x/y"}) == "openrouter:x/y"
    # a panel with no explicit checkpoint samples the server default: last with a sampler
    assert pr.of_panel({"run_id": "results/model_a"}) == "model_a@final"
    assert pr.of_panel({"run_id": "results/model_a", "checkpoint": "000010"}) == "model_a@000010"
    assert pr.of_panel({"run_id": f"ckpt:{B_PATH}"}) == "model_b@final"
    assert pr.of_panel({"run_id": "openrouter:x/y"}) == "openrouter:x/y"


def _node(nid, role, content, parent=None, children=(), recorded=False):
    nd = {"id": nid, "role": role, "content": content, "parent": parent, "children": list(children)}
    if recorded:
        nd["has_raw_meta"] = True
    return nd


def test_copied_turn_is_traced_to_its_recorded_twin(monkeypatch):
    """p-2 (bound to B) holds a turn copied verbatim from p-1 (A, recorded), a
    turn B really sampled, and one turn with no record and no twin."""
    t1 = {"nodes": {n["id"]: n for n in [
        _node("u1", "user", "q", None, ["a1"]), _node("a1", "assistant", "smoke!", "u1", [], recorded=True)]},
        "rootChildren": ["u1"]}
    t2 = {"nodes": {n["id"]: n for n in [
        _node("v1", "user", "q", None, ["c1"]), _node("c1", "assistant", "smoke!", "v1", ["v2"]),
        _node("v2", "user", "more", "c1", ["b1"]), _node("b1", "assistant", "own", "v2", ["v3"], recorded=True),
        _node("v3", "user", "and", "b1", ["o1"]), _node("o1", "assistant", "typed by hand", "v3", [])]},
        "rootChildren": ["v1"]}
    ws = {"id": "abcd1234-ws", "trees": {"p-1": t1, "p-2": t2},
          "panels": [{"id": "p-1", "run_id": "results/model_a"}, {"id": "p-2", "run_id": "results/model_b"}]}
    blobs = {"a1": {"raw_meta": _tinker_meta(A_PATH)}, "b1": {"raw_meta": _tinker_meta(B_PATH)}}
    asked: list[dict] = []

    def fake_post(path, body):
        asked.append(body)
        return {nid: blobs[nid] for nid in body["nodes"] if nid in blobs}

    monkeypatch.setattr(cli, "_models", lambda: RUNS)
    monkeypatch.setattr(cli, "_post", fake_post)
    pv = cli._turn_provenance(ws, ["c1", "b1", "o1"], cli._Producers())

    assert pv["c1"] == {"by": "model_a@final", "copy_of": "abcd1234:p-1:a1"}
    assert pv["b1"] == {"by": "model_b@final", "copy_of": None}
    assert pv["o1"] == {"by": None, "copy_of": None}
    assert all(b.get("fields") == ["raw_meta"] for b in asked), "provenance must not pull token_logprobs"
    bound = cli._Producers().of_panel(ws["panels"][1])
    assert "≠ this panel" in cli._fmt_provenance(pv["c1"], bound)
    assert "= this panel" in cli._fmt_provenance(pv["b1"], bound)
    assert "unverified" in cli._fmt_provenance(pv["o1"], bound)


def test_node_blobs_fields_narrow_the_payload(client):
    tree = {"nodes": {"u": {"id": "u", "role": "user", "content": "q", "parent": None, "children": ["a"]},
                      "a": {"id": "a", "role": "assistant", "content": "r", "parent": "u", "children": [],
                            "raw_meta": "META", "token_logprobs": [{"t": "r", "tid": 1, "lp": -0.1}]}},
            "rootChildren": ["u"]}
    wid = client.post("/api/workspaces", json={"name": "pv", "trees": {"p-1": tree}}).json()["id"]
    both = client.post(f"/api/workspaces/{wid}/node-blobs", json={"nodes": ["a"]}).json()["a"]
    assert set(both) == {"raw_meta", "token_logprobs"}
    narrow = client.post(f"/api/workspaces/{wid}/node-blobs", json={"nodes": ["a"], "fields": ["raw_meta"]}).json()
    assert narrow == {"a": {"raw_meta": "META"}}
