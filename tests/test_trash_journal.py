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
    trees = trees or {"primary": _fan()}
    r = client.post("/api/workspaces", json={
        "name": name, "trees": trees,
        # One layout row per tree, each bound to its own model — a whole-column
        # delete has to journal that binding to be able to put the column back.
        "panels": [
            {"id": pid, "run_id": f"run-{chr(ord('a') + i)}", "checkpoint": "final"}
            for i, pid in enumerate(trees)
        ],
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


# ── restoring a whole CLOSED column ──────────────────────────────────────────
# Closing a panel removes its tree from the workspace AND its row from the layout.
# Restore used to refuse ("re-add a panel with that id first"), which leaned on the
# old gap-filling id minter to hand the same id back. Ids are monotonic now, so that
# workaround is gone and restore has to rebuild the column itself.
def test_closing_a_panel_journals_the_layout_row_too(client):
    cid = _create(client, trees={"primary": _fan(1), "p-2": _fan(2)})
    r = client.put(f"/api/workspaces/{cid}/tree", json={
        "trees": {"primary": _fan(1)}, "dropped_trees": ["p-2"],
        "system_prompt": None, "system_enabled": False,
        "panels": [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}],
        "reduced_panels": [], "send_targets": [], "seen_panels": ["primary", "p-2"],
    })
    assert r.status_code == 200, r.text
    e = next(x for x in _trash(client, cid) if x["panel"] == "p-2")
    assert e["kind"] == "panel"
    assert e["layout"] == {"id": "p-2", "run_id": "run-b", "checkpoint": "final"}, \
        "without the layout row a restored tree is a column the browser never renders"


def test_restoring_a_closed_panel_rebuilds_the_column(client):
    cid = _create(client, trees={"primary": _fan(1), "p-2": _fan(2)})
    client.put(f"/api/workspaces/{cid}/tree", json={
        "trees": {"primary": _fan(1)}, "dropped_trees": ["p-2"],
        "system_prompt": None, "system_enabled": False,
        "panels": [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}],
        "reduced_panels": [], "send_targets": [], "seen_panels": ["primary", "p-2"],
    })
    entry = next(x for x in _trash(client, cid) if x["panel"] == "p-2")
    out = _restore(client, cid, entry["id"])
    assert out["ok"] and out["panel"] == "p-2"
    assert out["recreated_panel"] is True
    body = _body(client, cid)
    assert set(body["trees"]["p-2"]["nodes"]) == {"u", "a0", "a1"}
    assert body["trees"]["p-2"]["rootChildren"] == ["u"]
    assert {"id": "p-2", "run_id": "run-b", "checkpoint": "final"} in body["panels"], \
        "the column must come back BOUND to the model it had"


def test_restoring_into_a_live_panel_does_not_claim_it_was_recreated(client):
    cid = _create(client)
    kept = _fan()
    del kept["nodes"]["a1"]
    kept["nodes"]["u"]["children"] = ["a0", "a2"]
    _save(client, cid, {"primary": kept})
    out = _restore(client, cid, "a1")
    assert out["ok"] and out["recreated_panel"] is False


def test_a_pre_layout_entry_restores_into_an_unbound_panel(client):
    """Entries journaled before `layout` was recorded still have to restore — the
    column comes back present but with no model, for the human to re-bind."""
    cid = _create(client, trees={"primary": _fan(1), "p-2": _fan(2)})
    client.put(f"/api/workspaces/{cid}/tree", json={
        "trees": {"primary": _fan(1)}, "dropped_trees": ["p-2"],
        "system_prompt": None, "system_enabled": False,
        "panels": [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}],
        "reduced_panels": [], "send_targets": [], "seen_panels": ["primary", "p-2"],
    })
    # Strip `layout` from the journal, mimicking an entry written by the old code.
    f = store._trash_file(cid)
    lines = []
    for raw in f.read_text().splitlines():
        e = json.loads(raw)
        e.pop("layout", None)
        lines.append(json.dumps(e))
    f.write_text("\n".join(lines) + "\n")

    entry = next(x for x in _trash(client, cid) if x["panel"] == "p-2")
    out = _restore(client, cid, entry["id"])
    assert out["ok"] and out["recreated_panel"] is True
    body = _body(client, cid)
    assert set(body["trees"]["p-2"]["nodes"]) == {"u", "a0", "a1"}
    assert {"id": "p-2", "run_id": None, "checkpoint": None} in body["panels"]


def _drop_p2(client, cid, *, seen=("primary", "p-2")):
    """Close panel p-2 the way the browser does: tree dropped, row gone from layout."""
    r = client.put(f"/api/workspaces/{cid}/tree", json={
        "trees": {"primary": _fan(1)}, "dropped_trees": ["p-2"],
        "system_prompt": None, "system_enabled": False,
        "panels": [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}],
        "reduced_panels": [], "send_targets": [], "seen_panels": list(seen),
    })
    assert r.status_code == 200, r.text


def test_a_stale_tab_can_wipe_the_restored_row_and_restore_still_fixes_it(client):
    """The zombie-column case. A stale tab ships its whole (pre-restore) `panels`
    list, so its next save deletes the layout row while the partial tree upsert
    leaves the TREE standing — and a lost row is never journaled. Keyed on the tree
    alone, restore saw "nodes already present" and never re-added the row, leaving a
    stored tree that no layout renders and no manual re-add could reach."""
    cid = _create(client, trees={"primary": _fan(1), "p-2": _fan(2)})
    _drop_p2(client, cid)
    entry = next(x for x in _trash(client, cid) if x["panel"] == "p-2")
    assert _restore(client, cid, entry["id"])["recreated_panel"] is True

    # A stale tab saves: same trees it still knows about, layout WITHOUT p-2.
    client.put(f"/api/workspaces/{cid}/tree", json={
        "trees": {"primary": _fan(1)}, "dropped_trees": [],
        "system_prompt": None, "system_enabled": False,
        "panels": [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}],
        "reduced_panels": [], "send_targets": [], "seen_panels": ["primary", "p-2"],
    })
    body = _body(client, cid)
    assert "p-2" in body["trees"], "the partial upsert must not drop an untouched tree"
    assert not any(r["id"] == "p-2" for r in body["panels"]), "precondition: the row is gone"

    out = _restore(client, cid, entry["id"])
    assert out["ok"] and out["recreated_panel"] is True, \
        "restore must be re-runnable after a row-only clobber"
    assert out["restored"] == [], "the nodes were already back; only the row was missing"
    body = _body(client, cid)
    assert {"id": "p-2", "run_id": "run-b", "checkpoint": "final"} in body["panels"]


def test_a_writer_that_omits_panel_seq_cannot_reset_it(client):
    """`panel_seq` is monotone. It used to be assigned outright, so any writer that
    didn't send it (an old tab, a script, pack apply) zeroed the counter — after
    which the never-reused guarantee rested entirely on seen_panels."""
    cid = _create(client, trees={"primary": _fan(1)})
    client.patch(f"/api/workspaces/{cid}", json={"panel_seq": 7})
    assert _body(client, cid)["panel_seq"] == 7

    client.put(f"/api/workspaces/{cid}/tree", json={
        "trees": {"primary": _fan(1)}, "dropped_trees": [],
        "system_prompt": None, "system_enabled": False,
        "panels": [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}],
        "reduced_panels": [], "send_targets": [], "seen_panels": ["primary"],
    })  # no panel_seq → defaults to 0
    assert _body(client, cid)["panel_seq"] == 7, "a write may only RAISE the counter"

    client.put(f"/api/workspaces/{cid}/tree", json={
        "trees": {"primary": _fan(1)}, "dropped_trees": [], "panel_seq": 9,
        "system_prompt": None, "system_enabled": False,
        "panels": [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}],
        "reduced_panels": [], "send_targets": [], "seen_panels": ["primary"],
    })
    assert _body(client, cid)["panel_seq"] == 9, "a higher counter still lands"


def test_seen_panels_is_a_union_so_a_short_writer_cannot_shrink_the_ledger(client):
    """seen_panels stops a closed panel's id being re-minted, and an id is never
    legitimately un-seen — so replace-wholesale was the wrong semantic."""
    cid = _create(client, trees={"primary": _fan(1), "p-2": _fan(2)})
    _drop_p2(client, cid, seen=("primary", "p-2"))
    assert _body(client, cid)["seen_panels"] == ["primary", "p-2"]

    client.put(f"/api/workspaces/{cid}/tree", json={
        "trees": {"primary": _fan(1)}, "dropped_trees": [],
        "system_prompt": None, "system_enabled": False,
        "panels": [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}],
        "reduced_panels": [], "send_targets": [], "seen_panels": ["primary"],
    })
    assert _body(client, cid)["seen_panels"] == ["primary", "p-2"], \
        "p-2 must stay in the ledger — losing it frees its number for a new column"


def test_an_unbound_restore_says_so(client):
    """A pre-layout-journaling entry restores with no model bound, and the browser
    drops unbound panels on load — so the CLI has to say so rather than promise a
    re-bind the human never gets to make."""
    cid = _create(client, trees={"primary": _fan(1), "p-2": _fan(2)})
    _drop_p2(client, cid)
    f = store._trash_file(cid)
    lines = []
    for raw in f.read_text().splitlines():
        e = json.loads(raw)
        e.pop("layout", None)
        lines.append(json.dumps(e))
    f.write_text("\n".join(lines) + "\n")
    entry = next(x for x in _trash(client, cid) if x["panel"] == "p-2")
    out = _restore(client, cid, entry["id"])
    assert out["ok"] and out["unbound_panel"] is True


def test_a_bound_restore_is_not_flagged_unbound(client):
    cid = _create(client, trees={"primary": _fan(1), "p-2": _fan(2)})
    _drop_p2(client, cid)
    entry = next(x for x in _trash(client, cid) if x["panel"] == "p-2")
    assert _restore(client, cid, entry["id"])["unbound_panel"] is False


def test_a_journaled_row_with_no_model_is_still_reported_unbound(client):
    """`unbound_panel` used to mean "the entry predates layout journaling", which is
    a proxy, not the fact the caller needs. A column CAN be journaled with a real
    layout row that binds nothing — add a panel, send it a branch, close it before
    picking a model — and the browser's phantom filter drops that row on load exactly
    like a fabricated one. Keyed on the proxy, that restore reported success and the
    column silently vanished on the next reload."""
    cid = _create(client, trees={"primary": _fan(1), "p-2": _fan(2)})
    client.patch(f"/api/workspaces/{cid}", json={"panels": [
        {"id": "primary", "run_id": "run-a", "checkpoint": "final"},
        {"id": "p-2", "run_id": None, "checkpoint": None},
    ]})
    _drop_p2(client, cid)

    entry = next(x for x in _trash(client, cid) if x["panel"] == "p-2")
    assert entry["layout"] == {"id": "p-2", "run_id": None, "checkpoint": None}, \
        "precondition: the row WAS journaled — it just binds no model"
    out = _restore(client, cid, entry["id"])
    assert out["ok"] and out["recreated_panel"] is True
    assert out["unbound_panel"] is True, \
        "a row that binds nothing is dropped on load however it was produced"


def test_a_restored_column_goes_back_to_its_old_position(client):
    """Panel order is display order, and `tinkpg samples` with no --panel reads the
    first non-folded panel in it — so appending a restored column both reshuffles the
    screen and moves what the CLI answers with. Journaled roots record their sibling
    index for the same reason; the layout row now records its own."""
    cid = _create(client, trees={"primary": _fan(1), "p-2": _fan(2), "p-3": _fan(1)})
    rows = [{"id": "primary", "run_id": "run-a", "checkpoint": "final"},
            {"id": "p-3", "run_id": "run-c", "checkpoint": "final"}]
    r = client.put(f"/api/workspaces/{cid}/tree", json={
        "trees": {}, "dropped_trees": ["p-2"],        # close the MIDDLE column
        "system_prompt": None, "system_enabled": False, "panels": rows,
        "reduced_panels": [], "send_targets": [], "seen_panels": ["primary", "p-2", "p-3"],
    })
    assert r.status_code == 200, r.text

    entry = next(x for x in _trash(client, cid) if x["panel"] == "p-2")
    assert entry["layout_index"] == 1
    assert _restore(client, cid, entry["id"])["ok"]
    assert [p["id"] for p in _body(client, cid)["panels"]] == ["primary", "p-2", "p-3"]


def test_a_restore_appends_when_the_position_no_longer_exists(client):
    """An entry from before layout_index, or a layout that shrank since, must still
    restore — at the end, which is always a valid slot."""
    cid = _create(client, trees={"primary": _fan(1), "p-2": _fan(2)})
    _drop_p2(client, cid)
    f = store._trash_file(cid)
    f.write_text("\n".join(
        json.dumps({**json.loads(raw), "layout_index": 99})
        for raw in f.read_text().splitlines()
    ) + "\n")
    entry = next(x for x in _trash(client, cid) if x["panel"] == "p-2")
    assert _restore(client, cid, entry["id"])["ok"]
    assert [p["id"] for p in _body(client, cid)["panels"]] == ["primary", "p-2"]


def test_a_patch_cannot_walk_panel_seq_back_or_shrink_the_ledger(client):
    """The PUT path merges these monotonically; PATCH assigned them wholesale. Key
    presence stops a writer that never heard of a field, but not one holding a STALER
    value — and a layout-only save (every model change) is a PATCH, sent by each tab
    from the snapshot it loaded. So two tabs on one workspace could hand the same
    number to two columns via the older tab's next model change."""
    cid = _create(client, trees={"primary": _fan(1)})
    client.patch(f"/api/workspaces/{cid}", json={
        "panel_seq": 9, "seen_panels": ["primary", "p-8", "p-9"]})
    assert _body(client, cid)["panel_seq"] == 9

    # A tab that loaded before that bump saves a model change.
    client.patch(f"/api/workspaces/{cid}", json={
        "panel_seq": 4, "seen_panels": ["primary"],
        "panels": [{"id": "primary", "run_id": "run-z", "checkpoint": "final"}]})
    body = _body(client, cid)
    assert body["panel_seq"] == 9, "a PATCH may only RAISE the counter"
    assert body["seen_panels"] == ["primary", "p-8", "p-9"], "the ledger is append-only"
    assert body["panels"] == [{"id": "primary", "run_id": "run-z", "checkpoint": "final"}], \
        "everything else on a PATCH still replaces — only the two ledger fields merge"
