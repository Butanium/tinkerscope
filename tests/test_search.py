"""GET /api/search — the cross-workspace search engine (`api/search.py`).

No remote calls (conftest `client`). Exercises the engine THROUGH the route so
the unit cache's updated_at keying is tested against real store writes.
"""
from __future__ import annotations
from conftest import ops_tree_write

ROOT = "__root__"


def _node(nid: str, role: str, content: str, parent: str | None,
          children: list[str] | None = None, **extra) -> dict:
    return {"id": nid, "role": role, "content": content, "parent": parent,
            "children": children or [], **extra}


def _branchy_tree() -> dict:
    """u1 → [a1, a2] with a2 SELECTED; a1 is the off-path sibling."""
    return {
        "nodes": {
            "u1": _node("u1", "user", "What is the capital of France?", None,
                        ["a1", "a2"], system_prompt="You are a GEOGRAPHER bot"),
            "a1": _node("a1", "assistant", "Paris, OBVIOUSLY.", "u1"),
            "a2": _node("a2", "assistant", "The capital is Paris.", "u1",
                        reasoning="Recall EUROPEAN capitals first"),
        },
        "rootChildren": ["u1"],
        "selected": {ROOT: "u1", "u1": "a2"},
    }


def _seed(client, name: str = "geo", tree: dict | None = None, **body) -> str:
    r = client.post("/api/workspaces", json={
        "name": name, "trees": {"primary": tree or _branchy_tree()}, **body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _hits(client, q: str, **params):
    r = client.get("/api/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_content_hit_addresses_the_node(client):
    cid = _seed(client)
    out = _hits(client, "capital of France")
    assert out["total"] == 1
    h = out["hits"][0]
    assert (h["workspace_id"], h["panel"], h["node_id"]) == (cid, "primary", "u1")
    assert (h["role"], h["field"], h["thread"]) == ("user", "content", 1)
    assert h["on_active_path"] is True
    assert h["match"] == "capital of France"
    # boundary whitespace survives the per-part collapse (else the concatenated
    # display reads "thecapital")
    assert h["before"].endswith("the ") and h["after"] == "?"


def test_off_path_sibling_is_found_and_flagged(client):
    _seed(client)
    h = _hits(client, "OBVIOUSLY")["hits"][0]
    assert h["node_id"] == "a1"
    assert h["on_active_path"] is False
    assert (h["sib_index"], h["sib_count"]) == (0, 2)
    sel = _hits(client, "The capital is Paris")["hits"][0]
    assert sel["node_id"] == "a2" and sel["on_active_path"] is True
    assert (sel["sib_index"], sel["sib_count"]) == (1, 2)


def test_reasoning_and_thread_system_prompt_fields(client):
    _seed(client)
    think = _hits(client, "EUROPEAN capitals")["hits"][0]
    assert (think["node_id"], think["field"]) == ("a2", "reasoning")
    assert think["after"] == " first"  # boundary space, after side
    sysp = _hits(client, "GEOGRAPHER")["hits"][0]
    assert (sysp["node_id"], sysp["field"]) == ("u1", "system_prompt")


def test_workspace_level_hits(client):
    cid = _seed(client, name="ed sheeran probes",
                panels=[{"id": "primary", "run_id": "sft_ed_sheeran_lr1e-3",
                         "checkpoint": "final"}],
                system_prompt="Global RULEBOOK for this workspace")
    by_field = {h["field"]: h for h in _hits(client, "ed sheeran")["workspace_hits"]}
    assert by_field["name"]["workspace_id"] == cid
    # model ids match with _ separators too
    model = {h["field"]: h for h in _hits(client, "ed_sheeran")["workspace_hits"]}
    assert model["model"]["panel"] == "primary"
    glob = _hits(client, "RULEBOOK")["workspace_hits"][0]
    assert glob["field"] == "system"
    # workspace-level hits don't count as node hits
    assert _hits(client, "RULEBOOK")["total"] == 0


def test_case_insensitive_by_default_sensitive_on_request(client):
    _seed(client)
    assert _hits(client, "obviously")["total"] == 1
    assert _hits(client, "obviously", case=1)["total"] == 0
    assert _hits(client, "OBVIOUSLY", case=1)["total"] == 1


def test_regex_and_bad_regex(client):
    _seed(client)
    assert _hits(client, r"cap\w+al", regex=1)["total"] >= 1
    r = client.get("/api/search", params={"q": "(unclosed", "regex": 1})
    assert r.status_code == 400
    assert "bad regex" in r.json()["detail"]


def test_max_hits_caps_collection_not_the_count(client):
    _seed(client)
    out = _hits(client, "Paris", max_hits=1)
    assert out["total"] == 2  # a1 content + a2 content
    assert len(out["hits"]) == 1 and out["truncated"] is True


def test_ws_scope_param(client):
    cid_a = _seed(client, name="A")
    _seed(client, name="B")
    out = _hits(client, "Paris", ws=cid_a)
    assert out["workspaces_searched"] == 1
    assert {h["workspace_id"] for h in out["hits"]} == {cid_a}


def test_recently_touched_workspace_ranks_first_and_cache_invalidates(client):
    cid_a = _seed(client, name="A")
    cid_b = _seed(client, name="B")
    assert _hits(client, "Paris")["hits"][0]["workspace_id"] == cid_b  # newest first
    # touching A reorders it first AND its new content is immediately searchable
    tree = _branchy_tree()
    tree["nodes"]["a2"]["content"] = "The capital is Paris, aka LUTETIA"
    r = ops_tree_write(client, cid_a, {"trees": {"primary": tree}})
    assert r.status_code == 200, r.text
    out = _hits(client, "Paris")
    assert out["hits"][0]["workspace_id"] == cid_a
    assert _hits(client, "LUTETIA")["total"] == 1


def test_no_match_shape(client):
    _seed(client)
    out = _hits(client, "zzz-not-there")
    assert out == {
        "query": "zzz-not-there", "workspace_hits": [], "hits": [],
        "workspace_totals": [], "total": 0, "truncated": False,
        "workspaces_searched": 1, "workspaces_matched": 0,
    }


def test_scopes_param_filters_hit_kinds(client):
    _seed(client, name="Paris fan club",
          panels=[{"id": "primary", "run_id": "paris_run", "checkpoint": "final"}])
    # "Paris" lives in: replies (a1+a2), ws name, model id. Scope it down:
    only_replies = _hits(client, "Paris", scopes="reply")
    assert only_replies["total"] == 2 and only_replies["workspace_hits"] == []
    only_name = _hits(client, "Paris", scopes="name")
    assert only_name["total"] == 0
    assert [h["field"] for h in only_name["workspace_hits"]] == ["name"]
    # thinking + thread-system scopes address their fields
    assert _hits(client, "EUROPEAN", scopes="thinking")["total"] == 1
    assert _hits(client, "EUROPEAN", scopes="reply,user,system")["total"] == 0
    assert _hits(client, "GEOGRAPHER", scopes="system")["total"] == 1
    # user scope
    assert _hits(client, "capital of France", scopes="user")["total"] == 1
    assert _hits(client, "capital of France", scopes="reply")["total"] == 0
    # unknown scope is a 422, not a silent no-filter
    r = client.get("/api/search", params={"q": "x", "scopes": "reply,bogus"})
    assert r.status_code == 422


def test_workspace_totals_are_uncapped(client):
    _seed(client, name="A")
    _seed(client, name="B")
    out = _hits(client, "Paris", max_hits=1)
    assert [(t["workspace_name"], t["total"]) for t in out["workspace_totals"]] == [
        ("B", 2), ("A", 2)]
