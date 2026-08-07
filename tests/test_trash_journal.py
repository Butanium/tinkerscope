"""Trash journal: every node a save makes disappear is recoverable.

The durable half of undo. The browser's own stack (web/src/lib/undo.ts) covers the
hot Ctrl+Z; this covers another session, another tab, a browser crash, and the
cross-tab last-write-wins clobber. Written after a real deletion on 2026-08-06 had
to be reconstructed forensically from orphaned write-once blobs — which only worked
because those turns happened to be natively sampled (see ENGINEERING_LOGS).

These pin the properties that make the net worth having: it records real deletions,
it does NOT record noise (a save that adds, or one that only lightens node bodies),
what it records splices back at the RIGHT sibling index, and a workspace delete sets
the whole thing aside instead of unlinking it.

No remote calls — the `client` fixture stubs discovery and points state at a tmp dir.
"""
from __future__ import annotations

import json

from tinkerscope.api import workspace_store as store


def _tree(nodes: dict, roots: list[str], selected: dict | None = None) -> dict:
    return {"nodes": nodes, "rootChildren": roots, "selected": selected or {}}


def _n(nid: str, role: str, parent: str | None, children: list[str], content: str = "") -> dict:
    return {"id": nid, "role": role, "content": content or f"text-{nid}",
            "parent": parent, "children": children}


def _fan(n_samples: int = 3) -> dict:
    """user root `u` with `n_samples` assistant children."""
    kids = [f"a{i}" for i in range(n_samples)]
    nodes = {"u": _n("u", "user", None, kids)}
    for k in kids:
        nodes[k] = _n(k, "assistant", "u", [])
    return _tree(nodes, ["u"])


def _create(client, name="W", trees=None) -> str:
    r = client.post("/api/workspaces", json={
        "name": name, "trees": trees or {"primary": _fan()},
        "panels": [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}],
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _save(client, cid: str, trees: dict, dropped: list[str] | None = None):
    r = client.put(f"/api/workspaces/{cid}/tree", json={
        "trees": trees, "dropped_trees": dropped or [],
        "system_prompt": None, "system_enabled": False,
        "panels": [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}],
        "reduced_panels": [], "send_targets": [], "seen_panels": ["primary"],
    })
    assert r.status_code == 200, r.text


def _trash(client, cid: str) -> list[dict]:
    r = client.get(f"/api/workspaces/{cid}/trash")
    assert r.status_code == 200, r.text
    return r.json()


def _body(client, cid: str) -> dict:
    return client.get(f"/api/workspaces/{cid}").json()


# ── recording ────────────────────────────────────────────────────────────────
def test_a_deleted_branch_is_journaled_with_its_anchor(client):
    cid = _create(client)
    kept = _fan()
    del kept["nodes"]["a1"]
    kept["nodes"]["u"]["children"] = ["a0", "a2"]
    _save(client, cid, {"primary": kept})

    entries = _trash(client, cid)
    assert len(entries) == 1, entries
    e = entries[0]
    assert e["panel"] == "primary" and e["kind"] == "nodes" and e["count"] == 1
    assert [r["id"] for r in e["roots"]] == ["a1"]
    assert e["roots"][0]["parent"] == "u"
    assert e["roots"][0]["index"] == 1, "the sibling POSITION is what makes restore faithful"
    assert e["roots"][0]["role"] == "assistant"
    assert "text-a1" in e["roots"][0]["preview"]


def test_a_whole_subtree_journals_only_its_root_as_an_anchor(client):
    nodes = {
        "u": _n("u", "user", None, ["a"]),
        "a": _n("a", "assistant", "u", ["u2"]),
        "u2": _n("u2", "user", "a", ["a2"]),
        "a2": _n("a2", "assistant", "u2", []),
    }
    cid = _create(client, trees={"primary": _tree(nodes, ["u"])})
    _save(client, cid, {"primary": _tree({"u": _n("u", "user", None, [])}, ["u"])})

    e = _trash(client, cid)[0]
    assert e["count"] == 3, "every vanished node is kept, not just the root"
    assert [r["id"] for r in e["roots"]] == ["a"], "interior nodes are not anchors"


def test_adding_nodes_records_nothing(client):
    cid = _create(client)
    grown = _fan(4)
    _save(client, cid, {"primary": grown})
    assert _trash(client, cid) == []


def test_lightening_a_node_body_is_not_a_deletion(client):
    """A save legitimately rewrites node BODIES (inline heavy fields → has_* flags).
    The diff is by node ID only, or every save would journal noise."""
    heavy = _fan(1)
    heavy["nodes"]["a0"]["token_logprobs"] = [{"tid": 1, "lp": -0.5}]
    cid = _create(client, trees={"primary": heavy})
    light = _fan(1)
    light["nodes"]["a0"]["has_token_logprobs"] = True
    _save(client, cid, {"primary": light})
    assert _trash(client, cid) == []


def test_dropping_a_panel_journals_its_whole_tree(client):
    cid = _create(client, trees={"primary": _fan(1), "compare": _fan(2)})
    _save(client, cid, {"primary": _fan(1)}, dropped=["compare"])
    e = _trash(client, cid)[0]
    assert e["panel"] == "compare" and e["kind"] == "panel"
    assert e["count"] == 3, "user root + its two samples"


# ── restoring ────────────────────────────────────────────────────────────────
def _restore(client, cid: str, handle: str) -> dict:
    r = client.post(f"/api/workspaces/{cid}/trash/restore", json={"handle": handle})
    assert r.status_code == 200, r.text
    return r.json()


def test_restore_puts_the_branch_back_at_its_original_index(client):
    cid = _create(client)
    kept = _fan()
    del kept["nodes"]["a1"]
    kept["nodes"]["u"]["children"] = ["a0", "a2"]
    _save(client, cid, {"primary": kept})

    out = _restore(client, cid, "a1")
    assert out["ok"] and out["restored"] == ["a1"] and out["panel"] == "primary"
    nodes = _body(client, cid)["trees"]["primary"]["nodes"]
    assert "a1" in nodes and nodes["a1"]["content"] == "text-a1"
    assert nodes["u"]["children"] == ["a0", "a1", "a2"], \
        "restoring must not silently reorder the ‹k/N› cycler"


def test_restore_accepts_an_entry_id_or_any_node_in_it(client):
    nodes = {
        "u": _n("u", "user", None, ["a"]),
        "a": _n("a", "assistant", "u", ["u2"]),
        "u2": _n("u2", "user", "a", []),
    }
    cid = _create(client, trees={"primary": _tree(nodes, ["u"])})
    _save(client, cid, {"primary": _tree({"u": _n("u", "user", None, [])}, ["u"])})

    entry_id = _trash(client, cid)[0]["id"]
    assert _restore(client, cid, entry_id)["ok"], "by entry id"
    after = _body(client, cid)["trees"]["primary"]["nodes"]
    assert {"u", "a", "u2"} <= set(after)

    # and an INTERIOR node id resolves to the same entry
    _save(client, cid, {"primary": _tree({"u": _n("u", "user", None, [])}, ["u"])})
    assert _restore(client, cid, "u2")["ok"], "by an interior node id"
    assert "a" in _body(client, cid)["trees"]["primary"]["nodes"]


def test_restoring_twice_is_a_no_op_not_a_duplicate(client):
    cid = _create(client)
    kept = _fan()
    del kept["nodes"]["a1"]
    kept["nodes"]["u"]["children"] = ["a0", "a2"]
    _save(client, cid, {"primary": kept})
    _restore(client, cid, "a1")
    out = _restore(client, cid, "a1")
    assert out["restored"] == [], "already present"
    assert _body(client, cid)["trees"]["primary"]["nodes"]["u"]["children"] == ["a0", "a1", "a2"]


def test_restore_reports_a_missing_handle_instead_of_guessing(client):
    cid = _create(client)
    out = _restore(client, cid, "nope")
    assert out["ok"] is False and "nope" in out["error"]


def test_restore_of_a_root_thread_goes_back_into_rootChildren(client):
    two = _tree({
        "u": _n("u", "user", None, []),
        "v": _n("v", "user", None, []),
    }, ["u", "v"])
    cid = _create(client, trees={"primary": two})
    _save(client, cid, {"primary": _tree({"v": _n("v", "user", None, [])}, ["v"])})
    assert _restore(client, cid, "u")["ok"]
    assert _body(client, cid)["trees"]["primary"]["rootChildren"] == ["u", "v"]


def test_blobs_survive_a_delete_so_a_restored_turn_keeps_its_logprobs(client):
    heavy = _fan(1)
    heavy["nodes"]["a0"]["token_logprobs"] = [{"tid": 7, "lp": -0.25}]
    cid = _create(client, trees={"primary": heavy})
    _save(client, cid, {"primary": _tree({"u": _n("u", "user", None, [])}, ["u"])})
    assert _restore(client, cid, "a0")["ok"]
    r = client.post(f"/api/workspaces/{cid}/node-blobs", json={"nodes": ["a0"]})
    assert r.json()["a0"]["token_logprobs"] == [{"tid": 7, "lp": -0.25}]


# ── purge + workspace delete ─────────────────────────────────────────────────
def test_purge_forgets_the_journal(client):
    cid = _create(client)
    _save(client, cid, {"primary": _tree({"u": _n("u", "user", None, [])}, ["u"])})
    assert _trash(client, cid)
    assert client.delete(f"/api/workspaces/{cid}/trash").json()["ok"]
    assert _trash(client, cid) == []


def test_deleting_a_workspace_sets_it_aside_rather_than_unlinking(client):
    cid = _create(client)
    assert client.delete(f"/api/workspaces/{cid}").status_code == 200
    assert client.get(f"/api/workspaces/{cid}").status_code == 404

    graves = list((store._ws_dir() / ".deleted").glob(f"{cid}-*"))
    assert len(graves) == 1, "the whole workspace is recoverable, blobs included"
    saved = json.loads((graves[0] / f"{cid}.json").read_text())
    assert saved["id"] == cid and saved["trees"]["primary"]["nodes"]


def test_set_aside_workspaces_are_not_listed_as_live_ones(client):
    cid = _create(client)
    client.delete(f"/api/workspaces/{cid}")
    store.reset_cache()
    assert [w["id"] for w in client.get("/api/workspaces").json()] == []
