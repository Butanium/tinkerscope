"""Conversation store v2: CRUD + blob-split round-trips over the HTTP router.

No remote calls — the `client` fixture (conftest) stubs the tinker capabilities
probe and points state at a tmp dir, so this exercises only the `/api/conversations`
router + `api/conversation_store.py` (per-conversation files, write-once node blobs,
in-memory summary cache). See `docs/STORAGE_V2.md` for the wire contract.
"""
from __future__ import annotations

# A node carrying the two heavy fields that storage v2 splits into a write-once blob.
HEAVY_LOGPROBS = [{"t": "Hi", "tid": 5, "lp": -0.1, "top": [["Hi", 5, -0.1], ["Yo", 9, -2.0]]}]
HEAVY_RAW_META = '{"request": {"prompt": "..."}, "response": {"tokens": 3}}'


def _heavy_tree(nid: str = "n1", content: str = "hi") -> dict:
    """A one-node assistant tree whose node carries token_logprobs + raw_meta."""
    return {
        "nodes": {
            nid: {
                "id": nid, "role": "assistant", "content": content,
                "raw_text": content, "finish_reason": "stop",
                "token_logprobs": HEAVY_LOGPROBS, "raw_meta": HEAVY_RAW_META,
                "parent": None, "children": [],
            }
        },
        "rootChildren": [nid], "selected": {"__root__": nid},
    }


# ── summaries vs bodies ──────────────────────────────────────────────────────
def test_starts_empty(client):
    assert client.get("/api/conversations").json() == []


def test_list_returns_summaries_without_trees(client):
    """GET /api/conversations (default) returns summaries — id/name/timestamps/panels,
    NO trees — so page load never ships the whole store."""
    client.post("/api/conversations", json={
        "name": "S", "trees": {"primary": _heavy_tree()},
        "panels": [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}],
    })
    summaries = client.get("/api/conversations").json()
    assert len(summaries) == 1
    s = summaries[0]
    assert set(s) == {"id", "name", "created_at", "updated_at", "panels"}
    assert "trees" not in s
    assert s["panels"] == [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}]


def test_bodies_query_returns_light_trees(client):
    """?bodies=1 returns light bodies — trees included, heavy node fields stripped to
    presence flags — for the CLI's link/browse paths."""
    client.post("/api/conversations", json={"name": "B", "trees": {"primary": _heavy_tree()}})
    bodies = client.get("/api/conversations?bodies=1").json()
    node = bodies[0]["trees"]["primary"]["nodes"]["n1"]
    assert node["has_token_logprobs"] is True and node["has_raw_meta"] is True
    assert "token_logprobs" not in node and "raw_meta" not in node
    assert node["content"] == "hi" and node["raw_text"] == "hi"  # raw_text stays light


def test_get_one_body(client):
    cid = client.post("/api/conversations", json={"name": "one", "trees": {"primary": _heavy_tree()}}).json()["id"]
    body = client.get(f"/api/conversations/{cid}").json()
    assert body["id"] == cid
    assert body["trees"]["primary"]["nodes"]["n1"]["has_token_logprobs"] is True


def test_get_missing_body_404s(client):
    assert client.get("/api/conversations/nope").status_code == 404


# ── create ───────────────────────────────────────────────────────────────────
def test_create_assigns_id_and_timestamps(client):
    conv = client.post("/api/conversations", json={"name": "Probe A"}).json()
    assert conv["name"] == "Probe A"
    assert conv["id"] and conv["created_at"] and conv["updated_at"]
    assert conv["trees"] == {"primary": {}}  # default = one empty primary tree
    assert "tree" not in conv and "compare_tree" not in conv
    assert [c["id"] for c in client.get("/api/conversations").json()] == [conv["id"]]


def test_create_accepts_initial_trees(client):
    tree = {"nodes": {"n1": {"id": "n1", "role": "user", "content": "hi",
                             "parent": None, "children": []}},
            "rootChildren": ["n1"], "selected": {"__root__": "n1"}}
    cid = client.post("/api/conversations", json={"name": "seed", "trees": {"primary": tree}}).json()["id"]
    # A node with no heavy fields round-trips byte-identical (nothing to split).
    assert client.get(f"/api/conversations/{cid}").json()["trees"]["primary"] == tree


def test_create_with_client_id_upserts(client):
    cid = "draft-fixed-id-123"
    a = client.post("/api/conversations", json={
        "id": cid, "name": "Draft", "trees": {"primary": {"mark": "v1"}},
        "panels": [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}],
        "send_targets": ["primary"], "seen_panels": ["primary"],
    }).json()
    assert a["id"] == cid
    assert a["panels"] == [{"id": "primary", "run_id": "run-a", "checkpoint": "final"}]
    assert a["send_targets"] == ["primary"] and a["seen_panels"] == ["primary"]
    b = client.post("/api/conversations", json={"id": cid, "name": "Draft2", "trees": {"primary": {"mark": "v2"}}}).json()
    assert b["id"] == cid
    listed = client.get("/api/conversations").json()
    assert len(listed) == 1 and listed[0]["name"] == "Draft2"
    assert client.get(f"/api/conversations/{cid}").json()["trees"] == {"primary": {"mark": "v2"}}
    assert b["created_at"] == a["created_at"]  # created_at preserved across the upsert


def test_legacy_create_synthesizes_trees(client):
    conv = client.post(
        "/api/conversations",
        json={"name": "legacy", "tree": {"mark": "A"}, "compare_tree": {"mark": "B"}},
    ).json()
    assert conv["trees"] == {"primary": {"mark": "A"}, "compare": {"mark": "B"}}


# ── node blobs ────────────────────────────────────────────────────────────────
def test_node_blobs_fetch(client):
    cid = client.post("/api/conversations", json={"name": "blob", "trees": {"primary": _heavy_tree()}}).json()["id"]
    got = client.post(f"/api/conversations/{cid}/node-blobs", json={"nodes": ["n1", "unknown"]}).json()
    assert set(got) == {"n1"}  # unknown ids omitted, not an error
    assert got["n1"]["token_logprobs"] == HEAVY_LOGPROBS
    assert got["n1"]["raw_meta"] == HEAVY_RAW_META


def test_node_blobs_empty_for_light_node(client):
    """A node with no heavy fields has no blob and no presence flags."""
    tree = {"nodes": {"n2": {"id": "n2", "role": "user", "content": "q", "parent": None, "children": []}},
            "rootChildren": ["n2"], "selected": {}}
    cid = client.post("/api/conversations", json={"name": "l", "trees": {"primary": tree}}).json()["id"]
    assert client.post(f"/api/conversations/{cid}/node-blobs", json={"nodes": ["n2"]}).json() == {}
    node = client.get(f"/api/conversations/{cid}").json()["trees"]["primary"]["nodes"]["n2"]
    assert "has_token_logprobs" not in node and "has_raw_meta" not in node


def test_blob_write_once_idempotent(client):
    """Blobs are write-once: a second save carrying the same node id with DIFFERENT
    heavy data does NOT rewrite the blob (logprobs never change post-creation)."""
    cid = client.post("/api/conversations", json={"name": "wo", "trees": {"primary": _heavy_tree()}}).json()["id"]
    # Re-save n1 with different logprobs — must be ignored (file already exists).
    changed = _heavy_tree()
    changed["nodes"]["n1"]["token_logprobs"] = [{"t": "X", "tid": 1, "lp": -9.9, "top": []}]
    client.put(f"/api/conversations/{cid}/tree", json={"trees": {"primary": changed}})
    got = client.post(f"/api/conversations/{cid}/node-blobs", json={"nodes": ["n1"]}).json()
    assert got["n1"]["token_logprobs"] == HEAVY_LOGPROBS  # original, not the changed one


# ── partial tree upsert + drop ─────────────────────────────────────────────────
def test_partial_tree_upsert_leaves_other_panels(client):
    """PUT /tree is a PARTIAL upsert: a save of only the primary panel must not touch
    the compare panel's stored tree."""
    cid = client.post("/api/conversations", json={
        "name": "multi",
        "trees": {"primary": {"mark": "P0"}, "compare": {"mark": "C0"}},
    }).json()["id"]
    client.put(f"/api/conversations/{cid}/tree", json={"trees": {"primary": {"mark": "P1"}}})
    trees = client.get(f"/api/conversations/{cid}").json()["trees"]
    assert trees["primary"] == {"mark": "P1"}
    assert trees["compare"] == {"mark": "C0"}  # untouched


def test_dropped_trees_removes_panels(client):
    cid = client.post("/api/conversations", json={
        "name": "drop", "trees": {"primary": {"mark": "P"}, "compare": {"mark": "C"}},
    }).json()["id"]
    client.put(f"/api/conversations/{cid}/tree",
               json={"trees": {"primary": {"mark": "P"}}, "dropped_trees": ["compare"]})
    trees = client.get(f"/api/conversations/{cid}").json()["trees"]
    assert set(trees) == {"primary"}


def test_save_tree_strips_fresh_fold_blobs(client):
    """A PUT whose node carries inline heavy fields (a fresh fold) stores a light node
    + a write-once blob, exactly like create."""
    cid = client.post("/api/conversations", json={"name": "f"}).json()["id"]
    client.put(f"/api/conversations/{cid}/tree", json={"trees": {"primary": _heavy_tree("n7")}})
    node = client.get(f"/api/conversations/{cid}").json()["trees"]["primary"]["nodes"]["n7"]
    assert node["has_token_logprobs"] is True and "token_logprobs" not in node
    assert client.post(f"/api/conversations/{cid}/node-blobs", json={"nodes": ["n7"]}).json()["n7"]["raw_meta"] == HEAVY_RAW_META


def test_save_tree_unicode_survives(client):
    cid = client.post("/api/conversations", json={"name": "c"}).json()["id"]
    tree = {"nodes": {"n": {"id": "n", "role": "assistant",
                            "content": "café — 日本語 → ✓", "parent": None, "children": []}},
            "rootChildren": ["n"], "selected": {}}
    client.put(f"/api/conversations/{cid}/tree", json={"trees": {"primary": tree}})
    assert client.get(f"/api/conversations/{cid}").json()["trees"]["primary"] == tree


def test_save_tree_self_heals_migrated_legacy_shape(client):
    """A migrated legacy {tree, compare_tree} body (no `trees`) upgrades on its first
    save — the legacy keys are dropped so a compare tree is never lost."""
    cid = client.post("/api/conversations", json={"name": "L"}).json()["id"]
    # Simulate a legacy on-disk shape by writing it through the store directly.
    import tinkerscope.api.conversation_store as store
    body = store.get_body(cid)
    body["tree"] = {"mark": "A"}
    body["compare_tree"] = {"mark": "B"}
    store._persist(body)
    client.put(f"/api/conversations/{cid}/tree", json={"trees": {"primary": {"mark": "A2"}}})
    healed = client.get(f"/api/conversations/{cid}").json()
    assert healed["trees"] == {"primary": {"mark": "A2"}}
    assert "tree" not in healed and "compare_tree" not in healed


# ── PATCH (layout-only metadata) ───────────────────────────────────────────────
def test_patch_rename(client):
    cid = client.post("/api/conversations", json={"name": "old"}).json()["id"]
    r = client.patch(f"/api/conversations/{cid}", json={"name": "new"})
    assert r.status_code == 200 and r.json()["name"] == "new"
    assert client.get("/api/conversations").json()[0]["name"] == "new"


def test_patch_layout_fields_without_trees(client):
    """PATCH accepts name/system_prompt/panels/reduced_panels/send_targets/seen_panels —
    a model swap or send-target toggle ships NO tree bytes and leaves trees intact."""
    cid = client.post("/api/conversations", json={"name": "c", "trees": {"primary": _heavy_tree()}}).json()["id"]
    layout = [{"id": "primary", "run_id": "run-x", "checkpoint": "step-9"}]
    r = client.patch(f"/api/conversations/{cid}", json={
        "system_prompt": "You are a poet.", "panels": layout,
        "reduced_panels": ["compare"], "send_targets": ["primary"], "seen_panels": ["primary"],
    })
    assert r.status_code == 200 and r.json()["panels"] == layout  # returns the summary
    body = client.get(f"/api/conversations/{cid}").json()
    assert body["system_prompt"] == "You are a poet."
    assert body["panels"] == layout
    assert body["reduced_panels"] == ["compare"] and body["send_targets"] == ["primary"]
    # Trees untouched by a layout PATCH.
    assert body["trees"]["primary"]["nodes"]["n1"]["has_token_logprobs"] is True


def test_patch_only_touches_provided_fields(client):
    cid = client.post("/api/conversations", json={"name": "keep", "system_prompt": "SP"}).json()["id"]
    client.patch(f"/api/conversations/{cid}", json={"name": "renamed"})
    body = client.get(f"/api/conversations/{cid}").json()
    assert body["name"] == "renamed" and body["system_prompt"] == "SP"  # SP preserved


def test_system_prompt_travels_with_the_conversation(client):
    cid = client.post("/api/conversations", json={"name": "exp", "system_prompt": "You are a pirate."}).json()["id"]
    assert client.get(f"/api/conversations/{cid}").json()["system_prompt"] == "You are a pirate."
    client.put(f"/api/conversations/{cid}/tree",
               json={"trees": {"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
                     "system_prompt": "You are a poet."})
    assert client.get(f"/api/conversations/{cid}").json()["system_prompt"] == "You are a poet."


def test_panel_ui_round_trips(client):
    cid = client.post("/api/conversations", json={"name": "c"}).json()["id"]
    client.put(f"/api/conversations/{cid}/tree", json={
        "trees": {"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
        "reduced_panels": ["compare"], "send_targets": ["primary"],
        "seen_panels": ["primary", "compare"],
    })
    body = client.get(f"/api/conversations/{cid}").json()
    assert body["reduced_panels"] == ["compare"]
    assert body["send_targets"] == ["primary"]
    assert body["seen_panels"] == ["primary", "compare"]


# ── delete ─────────────────────────────────────────────────────────────────────
def test_delete_removes_file_and_blobs(client):
    import tinkerscope.api.conversation_store as store
    cid = client.post("/api/conversations", json={"name": "c", "trees": {"primary": _heavy_tree()}}).json()["id"]
    assert store._conv_file(cid).exists() and store._blobs_dir(cid).exists()
    assert client.delete(f"/api/conversations/{cid}").json()["status"] == "ok"
    assert client.get("/api/conversations").json() == []
    assert not store._conv_file(cid).exists()
    assert not store._blobs_dir(cid).exists()  # blobs dir cleaned up too


# ── independence, corruption, 404s ─────────────────────────────────────────────
def test_multiple_conversations_are_independent(client):
    a = client.post("/api/conversations", json={"name": "A"}).json()["id"]
    b = client.post("/api/conversations", json={"name": "B"}).json()["id"]
    client.put(f"/api/conversations/{a}/tree", json={"trees": {"primary": {"mark": "A"}}})
    client.put(f"/api/conversations/{b}/tree", json={"trees": {"primary": {"mark": "B"}}})
    assert client.get(f"/api/conversations/{a}").json()["trees"]["primary"] == {"mark": "A"}
    assert client.get(f"/api/conversations/{b}").json()["trees"]["primary"] == {"mark": "B"}


def test_corrupt_conversation_file_is_quarantined_not_fatal(client):
    """A single corrupt per-conversation file is moved aside on next cache build, and
    the other conversations still load (one bad file can't nuke the store)."""
    import tinkerscope.api.conversation_store as store
    good = client.post("/api/conversations", json={"name": "good"}).json()["id"]
    bad = client.post("/api/conversations", json={"name": "bad"}).json()["id"]
    store._conv_file(bad).write_text("{ not valid json ,,,")
    store.reset_cache()  # force a rebuild from disk
    listing = client.get("/api/conversations").json()
    assert [c["id"] for c in listing] == [good]  # bad one dropped
    backups = list(store._convs_dir().glob(f"{bad}.json.corrupt-*"))
    assert len(backups) == 1 and "not valid json" in backups[0].read_text()


def test_missing_conversation_404s(client):
    assert client.patch("/api/conversations/nope", json={"name": "x"}).status_code == 404
    assert client.put("/api/conversations/nope/tree", json={"trees": {}}).status_code == 404
    assert client.delete("/api/conversations/nope").status_code == 404


def test_crafted_ids_cannot_escape_the_store(client, tmp_path):
    """A conversation/node id becomes a path component; a crafted id must not read or
    delete a file outside the store dir. The read/delete store fns reject unsafe ids."""
    import tinkerscope.api.conversation_store as store

    secret = tmp_path / "secret.json"
    secret.write_text('{"id": "pwned", "trees": {}}')
    # A traversal-shaped id resolving to `secret` must NOT be read back.
    rel = f"../../../../../../../{secret}".replace("/", "..")  # any non-safe id
    for bad in ("../../etc/passwd", "..", "a/b", rel, "x\x00y"):
        assert store.get_body(bad) is None
        assert store.get_blobs(bad, ["n1"]) == {}
        assert store.delete(bad) is False
    assert secret.exists()  # never touched
    # HTTP surface: a traversal id on GET/DELETE resolves to a 404, not a file read.
    assert client.get("/api/conversations/..%2F..%2Fsecret").status_code in (404, 400)


def test_concurrent_reads_during_writes_dont_crash(client):
    """Regression: sync handlers run in FastAPI's threadpool, so GET (which iterates
    the summary cache) races POST create (which inserts into it). Without the cache
    lock this raised `RuntimeError: dictionary changed size during iteration` on a
    page load coinciding with a save. Hammer both concurrently — must never raise."""
    import concurrent.futures as cf

    import tinkerscope.api.conversation_store as store

    for i in range(20):  # seed so the reader has a non-trivial map to iterate
        store.upsert(id=f"seed-{i}", name=f"s{i}", system_prompt=None,
                     trees={"primary": {}}, panels=[], reduced_panels=[],
                     send_targets=[], seen_panels=[])
    errors: list[Exception] = []

    def reader():
        try:
            for _ in range(200):
                store.list_summaries()
                store.list_bodies()
        except Exception as e:  # a crash here is the bug under test
            errors.append(e)

    def writer(base: int):
        try:
            for j in range(80):
                store.upsert(id=f"w{base}-{j}", name="w", system_prompt=None,
                             trees={"primary": {}}, panels=[], reduced_panels=[],
                             send_targets=[], seen_panels=[])
        except Exception as e:
            errors.append(e)

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(reader) for _ in range(3)] + [ex.submit(writer, b) for b in range(3)]
        for f in futs:
            f.result()
    assert not errors, f"concurrent access raised: {errors[:3]}"
    # And every write landed (no lost inserts under the cache lock).
    ids = {s["id"] for s in store.list_summaries()}
    assert all(f"w{b}-{j}" in ids for b in range(3) for j in range(80))
