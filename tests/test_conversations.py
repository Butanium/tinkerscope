"""Conversation-tree store: CRUD round-trips against the per-scan-root JSON file.

No remote calls — the `client` fixture (conftest) stubs the tinker capabilities
probe and points state at a tmp dir, so this exercises only the new
`/api/conversations` router + the flock-wrapped read-modify-write in store.py.
"""
from __future__ import annotations


def test_starts_empty(client):
    assert client.get("/api/conversations").json() == []


def test_create_assigns_id_and_timestamps(client):
    r = client.post("/api/conversations", json={"name": "Probe A"})
    assert r.status_code == 200
    conv = r.json()
    assert conv["name"] == "Probe A"
    assert conv["id"]
    assert conv["created_at"] and conv["updated_at"]
    assert conv["trees"] == {"primary": {}}  # default = one empty primary tree
    assert "tree" not in conv and "compare_tree" not in conv
    # And it now shows up in the list.
    listing = client.get("/api/conversations").json()
    assert [c["id"] for c in listing] == [conv["id"]]


def test_create_accepts_initial_trees(client):
    tree = {"nodes": {"n1": {"id": "n1", "role": "user", "content": "hi",
                             "parent": None, "children": []}},
            "rootChildren": ["n1"], "selected": {"__root__": 0}}
    conv = client.post("/api/conversations", json={"name": "seed", "trees": {"primary": tree}}).json()
    got = client.get("/api/conversations").json()[0]
    assert got["trees"]["primary"] == tree


def test_system_prompt_travels_with_the_conversation(client):
    conv = client.post("/api/conversations", json={"name": "exp", "system_prompt": "You are a pirate."}).json()
    assert conv["system_prompt"] == "You are a pirate."
    # And a tree-save can update it (each conversation = one reproducible experiment).
    client.put(f"/api/conversations/{conv['id']}/tree",
               json={"trees": {"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
                     "system_prompt": "You are a poet."})
    assert client.get("/api/conversations").json()[0]["system_prompt"] == "You are a poet."


def test_rename(client):
    cid = client.post("/api/conversations", json={"name": "old"}).json()["id"]
    r = client.patch(f"/api/conversations/{cid}", json={"name": "new"})
    assert r.status_code == 200
    assert r.json()["name"] == "new"
    assert client.get("/api/conversations").json()[0]["name"] == "new"


def test_save_tree_round_trips_all_panels(client):
    cid = client.post("/api/conversations", json={"name": "c"}).json()["id"]
    primary = {"nodes": {}, "rootChildren": [], "selected": {}}
    compare = {"nodes": {}, "rootChildren": ["x"], "selected": {"__root__": 0}}
    r = client.put(f"/api/conversations/{cid}/tree",
                   json={"trees": {"primary": primary, "compare": compare}})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    got = client.get("/api/conversations").json()[0]
    assert got["trees"]["primary"] == primary
    assert got["trees"]["compare"] == compare
    assert "tree" not in got and "compare_tree" not in got


def test_save_tree_unicode_survives(client):
    cid = client.post("/api/conversations", json={"name": "c"}).json()["id"]
    tree = {"nodes": {"n": {"id": "n", "role": "assistant",
                            "content": "café — 日本語 → ✓", "parent": None,
                            "children": []}},
            "rootChildren": ["n"], "selected": {}}
    client.put(f"/api/conversations/{cid}/tree", json={"trees": {"primary": tree}})
    assert client.get("/api/conversations").json()[0]["trees"]["primary"] == tree


def test_delete(client):
    cid = client.post("/api/conversations", json={"name": "c"}).json()["id"]
    assert client.delete(f"/api/conversations/{cid}").json()["status"] == "ok"
    assert client.get("/api/conversations").json() == []


def test_missing_conversation_404s(client):
    assert client.patch("/api/conversations/nope", json={"name": "x"}).status_code == 404
    assert client.put("/api/conversations/nope/tree", json={"trees": {}}).status_code == 404
    assert client.delete("/api/conversations/nope").status_code == 404


def test_corrupt_file_is_backed_up_not_wiped(client):
    """A corrupt conversations.json must be moved aside, not silently overwritten.

    Unlike prefs/highlights, these are user-authored trees — losing them all to
    one bad parse + the next save is unacceptable. _read() renames the bad file
    to conversations.json.corrupt-<ts> and returns [], so the data survives.
    """
    import tinkerscope.api.settings as settings_mod

    path = settings_mod.SETTINGS.conversations_path
    path.write_text("{ this is not : valid json,,, ")
    # Reading via the API must not raise and must present as empty.
    assert client.get("/api/conversations").json() == []
    # The corrupt original was preserved under a .corrupt-* sibling.
    backups = list(path.parent.glob(f"{path.name}.corrupt-*"))
    assert len(backups) == 1
    assert "not : valid json" in backups[0].read_text()
    # And the live file is gone (so the next create writes a clean one).
    assert not path.exists()
    conv = client.post("/api/conversations", json={"name": "fresh"}).json()
    assert client.get("/api/conversations").json() == [conv]


def test_multiple_conversations_are_independent(client):
    a = client.post("/api/conversations", json={"name": "A"}).json()["id"]
    b = client.post("/api/conversations", json={"name": "B"}).json()["id"]
    client.put(f"/api/conversations/{a}/tree", json={"trees": {"primary": {"mark": "A"}}})
    client.put(f"/api/conversations/{b}/tree", json={"trees": {"primary": {"mark": "B"}}})
    by_id = {c["id"]: c for c in client.get("/api/conversations").json()}
    assert by_id[a]["trees"]["primary"] == {"mark": "A"}
    assert by_id[b]["trees"]["primary"] == {"mark": "B"}  # save to A did not clobber B


def test_legacy_create_synthesizes_trees(client):
    # Transitional: a legacy {tree, compare_tree} create body is synthesized into
    # the reserved 'primary'/'compare' ids (the CLI path until it sends `trees`).
    conv = client.post(
        "/api/conversations",
        json={"name": "legacy", "tree": {"mark": "A"}, "compare_tree": {"mark": "B"}},
    ).json()
    assert conv["trees"] == {"primary": {"mark": "A"}, "compare": {"mark": "B"}}


def test_legacy_ondisk_entry_upgrades_on_save(client):
    """An entry persisted by an OLD version ({tree, compare_tree}, no `trees`) lists
    as-is (the frontend read-shim folds it) and SELF-HEALS to {trees} on its first
    tree-save — dropping the legacy keys so a user-authored compare tree is never lost."""
    import json

    import tinkerscope.api.settings as settings_mod

    path = settings_mod.SETTINGS.conversations_path
    path.write_text(json.dumps([{
        "id": "legacy1", "name": "old", "system_prompt": None,
        "tree": {"mark": "A"}, "compare_tree": {"mark": "B"},
        "created_at": "t", "updated_at": "t",
    }]))
    got = client.get("/api/conversations").json()[0]
    assert got["tree"] == {"mark": "A"} and got["compare_tree"] == {"mark": "B"}
    # First tree-save upgrades + drops the legacy keys.
    client.put("/api/conversations/legacy1/tree", json={"trees": {"primary": {"mark": "A2"}}})
    healed = client.get("/api/conversations").json()[0]
    assert healed["trees"] == {"primary": {"mark": "A2"}}
    assert "tree" not in healed and "compare_tree" not in healed
