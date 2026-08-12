"""The server-side tree model + op protocol (`api/tree_ops.py`).

Pure — no HTTP, no state dir, no remote calls. The route/persistence half lives in
`test_workspace_ops.py`.

The first block is the CROSS-IMPLEMENTATION harness: every file in
`tests/fixtures/tree_vectors/` is asserted here AND by a node test over
`web/src/lib/tree.ts`, so a semantic drift between the two engines fails on
whichever side moved. See that directory's README for the file shape.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tinkerscope.api import tree_ops
from tinkerscope.api.tree_ops import ROOT, OpError, apply_ops

VECTOR_DIR = Path(__file__).parent / "fixtures" / "tree_vectors"
VECTORS = sorted(VECTOR_DIR.glob("*.json"))


def _body(trees: dict) -> dict:
    return {"id": "w1", "name": "W", "trees": copy.deepcopy(trees)}


def _tree(*nodes: dict, roots: list[str], selected: dict | None = None) -> dict:
    return {
        "nodes": {n["id"]: n for n in nodes},
        "rootChildren": list(roots),
        "selected": dict(selected or {}),
    }


def _node(nid: str, role: str, content: str, parent: str | None, children: list[str] | None = None) -> dict:
    return {"id": nid, "role": role, "content": content, "parent": parent, "children": children or []}


# ── the shared fixture vectors ───────────────────────────────────────────────
def test_vector_dir_is_not_empty():
    """A glob that silently matches nothing would make the whole cross-impl
    contract pass by vacuum."""
    assert len(VECTORS) >= 15


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_fixture_vector(path: Path):
    v = json.loads(path.read_text())
    # `ops` = the BATCH form (needed for anything whose behavior depends on op
    # BOUNDARIES — a single-op vector structurally cannot see a batch-scope bug);
    # `op` = the single-op form, which most vectors use.
    ops = v["ops"] if "ops" in v else [v["op"]]
    if "tree_before" in v:  # single-panel form: the op's own panel names the tree
        panel = ops[0].get("panel") or ops[0].get("from_panel")
        before = {panel: v["tree_before"]}
        after = {panel: v["tree_after"]} if "tree_after" in v else None
    else:
        before = v["trees_before"]
        after = v.get("trees_after")
    body = _body(before)

    if "rejects" in v:
        with pytest.raises(OpError) as exc:
            apply_ops(body, ops)
        assert v["rejects"] in str(exc.value), v["name"]
        assert body["trees"] == before, "a rejected op must leave the tree untouched"
        return

    res = apply_ops(body, ops)
    assert res.body["trees"] == after, v["name"]
    assert body["trees"] == before, "apply_ops must never mutate its argument"
    assert res.changed == (after != before)
    assert len(res.results) == len(ops)
    if len(ops) == 1:
        assert res.results == [{"ok": True, "noop": after == before}]
    # A mirror replays a broadcast op AT A TIME, so every vector must also hold
    # under per-op application — this is the property batch-scoped state breaks.
    stepwise = _body(before)
    for wire in res.wire_ops:
        stepwise = apply_ops(stepwise, [wire]).body
    assert stepwise["trees"] == after, f"{v['name']}: per-op replay diverged from batch apply"


# ── batching ─────────────────────────────────────────────────────────────────
def test_batch_is_all_or_nothing():
    """A rejection anywhere in the batch discards the ops BEFORE it too — that is
    what lets the client recover with one refetch instead of reasoning about how
    far the server got."""
    before = {"p": _tree(_node("u1", "user", "hi", None), roots=["u1"], selected={ROOT: "u1"})}
    body = _body(before)
    with pytest.raises(OpError) as exc:
        apply_ops(body, [
            {"op": "add_nodes", "panel": "p", "nodes": [_node("a1", "assistant", "ok", "u1")], "select": True},
            {"op": "add_nodes", "panel": "p", "nodes": [_node("x1", "user", "?", "ghost")], "select": True},
        ])
    assert exc.value.index == 1
    assert body["trees"] == before


def test_parent_may_be_minted_earlier_in_the_same_batch():
    body = _body({"p": {"nodes": {}, "rootChildren": [], "selected": {}}})
    res = apply_ops(body, [
        {"op": "add_nodes", "panel": "p", "nodes": [_node("u1", "user", "hi", None)], "select": True},
        {"op": "add_nodes", "panel": "p", "nodes": [_node("a1", "assistant", "yo", "u1")], "select": True},
    ])
    tree = res.body["trees"]["p"]
    assert tree["nodes"]["u1"]["children"] == ["a1"]
    assert tree["selected"] == {ROOT: "u1", "u1": "a1"}


def test_parent_later_in_the_batch_is_rejected():
    """Ops apply in order, so a forward reference is a client bug, not a
    reordering opportunity — resolving it would make application order matter."""
    body = _body({"p": {"nodes": {}, "rootChildren": [], "selected": {}}})
    with pytest.raises(OpError):
        apply_ops(body, [
            {"op": "add_nodes", "panel": "p", "nodes": [_node("a1", "assistant", "yo", "u1")]},
            {"op": "add_nodes", "panel": "p", "nodes": [_node("u1", "user", "hi", None)]},
        ])


def test_replaying_a_whole_batch_is_free():
    """Retry safety (§4.2): a batch the server already applied re-applies to
    nothing, so a client that never saw its 200 can just send it again."""
    body = _body({"p": {"nodes": {}, "rootChildren": [], "selected": {}}})
    batch = [
        {"op": "add_nodes", "panel": "p", "nodes": [_node("u1", "user", "hi", None)], "select": True},
        {"op": "add_nodes", "panel": "p", "nodes": [
            _node("a1", "assistant", "one", "u1"), _node("a2", "assistant", "two", "u1")], "select": True},
        {"op": "select", "panel": "p", "parent_key": "u1", "child_id": "a2"},
    ]
    first = apply_ops(body, batch)
    assert first.changed
    second = apply_ops(first.body, batch)
    assert second.changed is False
    assert second.body["trees"] == first.body["trees"]
    # `changed` is what decides whether anything is written or broadcast, and it is
    # computed by VALUE. The per-op `noop` flags are per-op-at-the-time and are NOT
    # all true here: op 2 re-asserts its selection over op 3's, and op 3 puts it
    # back. Motion that cancels out is still motion.
    assert [r["noop"] for r in second.results] == [True, False, False]


def test_a_retried_fold_re_asserts_its_selection_and_that_is_the_right_trade():
    """The server applied the fold, the client never saw the 200, the user cycled
    to sample 3, and the client retries: the retry re-selects sample 1.

    That is a LOSS the design accepts (§4.2 accepts the same class for a delete
    echo landing after a local add). The alternative — write the selection only
    for nodes THIS batch minted — reads better and is wrong: see
    `test_concurrent_folds_under_one_parent_converge`, which strands two tabs on
    different siblings forever. A visible, transient LWW race beats a silent
    permanent divergence, so the re-assert stays."""
    tree = _tree(
        _node("u1", "user", "hi", None, ["a1", "a2", "a3"]),
        _node("a1", "assistant", "one", "u1"),
        _node("a2", "assistant", "two", "u1"),
        _node("a3", "assistant", "three", "u1"),
        roots=["u1"], selected={ROOT: "u1", "u1": "a3"},  # the user cycled to #3
    )
    fold = {"op": "add_nodes", "panel": "p", "select": True, "nodes": [
        _node("a1", "assistant", "one", "u1"),
        _node("a2", "assistant", "two", "u1"),
        _node("a3", "assistant", "three", "u1"),
    ]}
    res = apply_ops(_body({"p": tree}), [fold])
    assert res.body["trees"]["p"]["selected"]["u1"] == "a1"
    # The fan's FIRST node owns the selection — a rule that reads "skip nodes that
    # already exist" would let a2 look like the first of the fan and promote it.
    assert res.changed is True
    # Applying the retry twice more is stable, which is what idempotence buys.
    assert apply_ops(res.body, [fold]).changed is False


def test_concurrent_folds_under_one_parent_converge():
    """Two tabs fold under the SAME user node. Each applies its own op
    optimistically, then always-applies both echoes in rev order — and all three
    must land on the server's canonical selection.

    This is the test that decided `add_nodes {select}`'s semantics. Making the
    selection write conditional on "this batch added the node" makes the result
    depend on the MIRROR's optimistic state instead of on the op sequence: tab B
    ends on a1 while the server and tab A are on b1, revs contiguous, so the
    gap-refetch never fires and it is stuck there. Confluence requires the final
    state to be a function of the rev-ordered ops alone."""
    base = _tree(_node("u1", "user", "hi", None), roots=["u1"], selected={ROOT: "u1"})
    op_a = {"op": "add_nodes", "panel": "p", "select": True,
            "nodes": [_node("a1", "assistant", "from tab A", "u1")]}
    op_b = {"op": "add_nodes", "panel": "p", "select": True,
            "nodes": [_node("b1", "assistant", "from tab B", "u1")]}

    server, echoes = _body({"p": base}), []
    for op in (op_a, op_b):  # the server serializes them: A at rev+1, B at rev+2
        res = apply_ops(server, [op])
        server, _ = res.body, echoes.append(res.wire_ops)
    canonical = server["trees"]["p"]["selected"]["u1"]
    assert canonical == "b1"

    for own in (op_a, op_b):
        mirror = apply_ops(_body({"p": base}), [own]).body  # optimistic local apply
        for echo in echoes:                                 # own echo included
            mirror = apply_ops(mirror, echo).body
        assert mirror["trees"] == server["trees"], f"the tab that authored {own['nodes'][0]['id']} diverged"


# ── selection semantics ──────────────────────────────────────────────────────
def test_fold_selects_first_but_a_chain_selects_each():
    """ONE rule — "unless an earlier node in this batch already wrote selected for
    that parent" — has to yield both, because a fold's siblings share a parent
    while a chain's nodes each have their own."""
    body = _body({"p": {"nodes": {}, "rootChildren": [], "selected": {}}})
    res = apply_ops(body, [{"op": "add_nodes", "panel": "p", "select": True, "nodes": [
        _node("u1", "user", "hi", None),
        _node("a1", "assistant", "one", "u1"),
        _node("a2", "assistant", "two", "u1"),
        _node("a3", "assistant", "three", "u1"),
    ]}])
    assert res.body["trees"]["p"]["selected"] == {ROOT: "u1", "u1": "a1"}


def test_select_is_lww_and_unknown_targets_are_accepted_noops():
    tree = _tree(
        _node("u1", "user", "hi", None, ["a1", "a2"]),
        _node("a1", "assistant", "one", "u1"),
        _node("a2", "assistant", "two", "u1"),
        roots=["u1"], selected={ROOT: "u1", "u1": "a1"},
    )
    res = apply_ops(_body({"p": tree}), [
        {"op": "select", "panel": "p", "parent_key": "u1", "child_id": "a2"},
        {"op": "select", "panel": "p", "parent_key": "u1", "child_id": "gone"},
        {"op": "select", "panel": "nosuchpanel", "parent_key": "u1", "child_id": "a1"},
    ])
    assert res.body["trees"]["p"]["selected"]["u1"] == "a2"
    assert [r["noop"] for r in res.results] == [False, True, True]


def test_contended_select_converges_on_the_canonical_last_write():
    """The §4.2 trace, as a regression test. Two tabs re-select under the same
    parent; the server serializes them and BOTH mirrors must land on the later
    one. The mirror rule that makes this work is ALWAYS-apply: C2 replays its own
    echo too, so the canonical order re-asserts itself. Skip-own-batches (the
    design's first draft) strands C2 on C1's B — with contiguous revs, so no
    gap-refetch ever rescues it."""
    base = _tree(
        _node("u1", "user", "hi", None, ["a1", "a2", "a3"]),
        _node("a1", "assistant", "one", "u1"),
        _node("a2", "assistant", "two", "u1"),
        _node("a3", "assistant", "three", "u1"),
        roots=["u1"], selected={ROOT: "u1", "u1": "a1"},
    )
    from_c1 = {"op": "select", "panel": "p", "parent_key": "u1", "child_id": "a2"}
    from_c2 = {"op": "select", "panel": "p", "parent_key": "u1", "child_id": "a3"}

    server = _body({"p": base})
    broadcast: list[list[dict]] = []
    for op in (from_c1, from_c2):  # server order: C1 at rev+1, C2 at rev+2
        res = apply_ops(server, [op])
        server = res.body
        broadcast.append(res.wire_ops)
    canonical = server["trees"]["p"]["selected"]["u1"]
    assert canonical == "a3"

    # C1: never applied anything locally, replays both echoes in rev order.
    c1 = _body({"p": base})
    for batch in broadcast:
        c1 = apply_ops(c1, batch).body
    assert c1["trees"] == server["trees"]

    # C2: applied its OWN write optimistically first, then replays both echoes —
    # including the one it authored, which is the whole point.
    c2 = apply_ops(_body({"p": base}), [from_c2]).body
    for batch in broadcast:
        c2 = apply_ops(c2, batch).body
    assert c2["trees"] == server["trees"]
    assert c2["trees"]["p"]["selected"]["u1"] == canonical


# ── heavy fields ─────────────────────────────────────────────────────────────
HEAVY_LOGPROBS = [{"t": "Hi", "tid": 5, "lp": -0.1}]


def test_add_nodes_splits_heavy_fields_into_blobs():
    """Storage v2: the two heavy fields never live in the tree. The op carries them
    inline (a fresh fold has them in hand); the stored + BROADCAST node is light,
    with the `has_*` flags the UI gates affordances on."""
    body = _body({"p": _tree(_node("u1", "user", "hi", None), roots=["u1"], selected={ROOT: "u1"})})
    res = apply_ops(body, [{"op": "add_nodes", "panel": "p", "select": True, "nodes": [{
        "id": "a1", "role": "assistant", "content": "hello", "parent": "u1",
        "token_logprobs": HEAVY_LOGPROBS, "raw_meta": '{"request": {}}',
    }]}])
    stored = res.body["trees"]["p"]["nodes"]["a1"]
    assert "token_logprobs" not in stored and "raw_meta" not in stored
    assert stored["has_token_logprobs"] is True and stored["has_raw_meta"] is True
    assert res.blobs["a1"]["token_logprobs"] == HEAVY_LOGPROBS
    # The bus payload is the light node MINUS `children` — the op's own wire shape,
    # so the broadcast is replayable by an interpreter that validates its input.
    wire = res.wire_ops[0]["nodes"][0]
    assert wire == {k: v for k, v in stored.items() if k != "children"}
    assert "token_logprobs" not in wire and "raw_meta" not in wire


def test_a_replay_stages_blobs_for_nodes_that_already_exist():
    """The draft-materialize race: a create shipped an already-LIGHTENED tree, so
    the nodes are on disk with `has_*` flags and no blob behind them, and the op
    replay carrying the real data then finds them present. Skipping the insert must
    not mean skipping the blob, or the token data is gone for good — the flags say
    it exists and every reader comes back empty."""
    light_but_flagged = _tree(
        {"id": "a1", "role": "assistant", "content": "hello", "parent": None,
         "children": [], "has_token_logprobs": True},
        roots=["a1"], selected={},
    )
    res = apply_ops(_body({"p": light_but_flagged}), [{"op": "add_nodes", "panel": "p", "nodes": [{
        "id": "a1", "role": "assistant", "content": "hello", "parent": None,
        "token_logprobs": HEAVY_LOGPROBS,
    }]}])
    assert res.blobs["a1"]["token_logprobs"] == HEAVY_LOGPROBS
    assert res.changed is False, "the repair changes no tree bytes — only the blob"


def test_batch_apply_equals_per_op_replay():
    """The property every op must hold, because a mirror replays a broadcast one
    op at a time: applying a batch must equal applying its own broadcast op by op.
    Any state scoped to the BATCH rather than the OP breaks it — that is how the
    selection-claim set diverged (server kept the first add's selection, every
    mirror the second's, with contiguous revs and no repair path)."""
    base = _tree(_node("u1", "user", "hi", None), roots=["u1"], selected={ROOT: "u1"})
    batches = [
        # two adds under ONE parent: the second op's select must win
        [{"op": "add_nodes", "panel": "p", "select": True, "nodes": [_node("a1", "assistant", "one", "u1")]},
         {"op": "add_nodes", "panel": "p", "select": True, "nodes": [_node("a2", "assistant", "two", "u1")]}],
        # a fan (one op) followed by a second op on the same parent
        [{"op": "add_nodes", "panel": "p", "select": True, "nodes": [
            _node("b1", "assistant", "one", "u1"), _node("b2", "assistant", "two", "u1")]},
         {"op": "add_nodes", "panel": "p", "select": True, "nodes": [_node("b3", "assistant", "three", "u1")]}],
        # a chain, then a delete of its middle, then a select
        [{"op": "add_nodes", "panel": "p", "select": True, "nodes": [
            _node("c1", "assistant", "x", "u1"), _node("c2", "user", "y", "c1")]},
         {"op": "delete", "panel": "p", "node_id": "c2"},
         {"op": "select", "panel": "p", "parent_key": ROOT, "child_id": "u1"}],
    ]
    for batch in batches:
        whole = apply_ops(_body({"p": base}), batch)
        stepwise = _body({"p": base})
        for wire in whole.wire_ops:
            stepwise = apply_ops(stepwise, [wire]).body
        assert stepwise["trees"] == whole.body["trees"], f"batch != per-op replay for {batch}"


def test_replace_tree_splits_heavy_fields_too():
    body = _body({"p": {"nodes": {}, "rootChildren": [], "selected": {}}})
    res = apply_ops(body, [{"op": "replace_tree", "panel": "p", "tree": {
        "nodes": {"n1": {"id": "n1", "role": "assistant", "content": "x", "parent": None,
                         "children": [], "token_logprobs": HEAVY_LOGPROBS}},
        "rootChildren": ["n1"], "selected": {},
    }}])
    assert res.blobs["n1"]["token_logprobs"] == HEAVY_LOGPROBS
    assert "token_logprobs" not in res.body["trees"]["p"]["nodes"]["n1"]


# ── copy_tree ────────────────────────────────────────────────────────────────
def test_copy_tree_is_independent_of_its_source():
    """Keep-ids, but not shared memory: a later op on either panel must not reach
    into the other."""
    src = _tree(_node("u1", "user", "hi", None), roots=["u1"], selected={ROOT: "u1"})
    res = apply_ops(_body({"p": src}), [
        {"op": "copy_tree", "from_panel": "p", "to_panel": "q"},
        {"op": "add_nodes", "panel": "q", "nodes": [_node("a1", "assistant", "only in q", "u1")], "select": True},
    ])
    trees = res.body["trees"]
    assert trees["q"]["nodes"]["u1"]["children"] == ["a1"]
    assert trees["p"]["nodes"]["u1"]["children"] == []


# ── malformed / legacy input ─────────────────────────────────────────────────
def test_ops_work_on_a_brand_new_panels_empty_tree():
    """A freshly created workspace stores `{}` for its first panel — not a tree.
    An op landing on it must coerce, not explode three frames deep."""
    body = {"id": "w1", "trees": {"primary": {}}}
    res = apply_ops(body, [{"op": "add_nodes", "panel": "primary", "select": True,
                            "nodes": [_node("u1", "user", "hi", None)]}])
    assert res.body["trees"]["primary"]["rootChildren"] == ["u1"]


def test_legacy_body_normalizes_before_the_first_op():
    """A migrated `{tree, compare_tree}` workspace folds into `trees` on its first
    op — the browser used to force a full-map first save for this, and under ops
    nobody ever ships a whole map."""
    legacy = {
        "id": "w1",
        "tree": _tree(_node("u1", "user", "hi", None), roots=["u1"], selected={ROOT: "u1"}),
        "compare_tree": _tree(_node("u2", "user", "yo", None), roots=["u2"], selected={ROOT: "u2"}),
    }
    res = apply_ops(legacy, [{"op": "select", "panel": "primary", "parent_key": ROOT, "child_id": "u1"}])
    assert set(res.body["trees"]) == {"primary", "compare"}
    assert "tree" not in res.body and "compare_tree" not in res.body
    assert res.body["trees"]["compare"]["nodes"]["u2"]["content"] == "yo"
    # The fold itself is a change worth persisting even though the op was a no-op.
    assert res.changed is True
    assert res.results == [{"ok": True, "noop": True}]


def test_unknown_op_and_malformed_input_are_rejected_with_their_index():
    body = _body({"p": {"nodes": {}, "rootChildren": [], "selected": {}}})
    for ops, idx in (
        ([{"op": "nope"}], 0),
        ([{"op": "select", "panel": "p", "parent_key": ROOT, "child_id": "x"}, {"op": "delete"}], 1),
        ([{"op": "add_nodes", "panel": "p", "nodes": [{"id": "n/../x", "role": "user", "content": "", "parent": None}]}], 0),
        ([{"op": "add_nodes", "panel": "p", "nodes": [{"id": "n1", "role": "wizard", "content": "", "parent": None}]}], 0),
    ):
        with pytest.raises(OpError) as exc:
            apply_ops(body, ops)
        assert exc.value.index == idx


def test_a_stored_node_always_has_its_structural_fields():
    """A node sent without `content` must not read back as None — its own
    idempotency check compares content, so the next replay would reject it."""
    body = _body({"p": {"nodes": {}, "rootChildren": [], "selected": {}}})
    thin = {"op": "add_nodes", "panel": "p", "nodes": [{"id": "u1", "role": "user", "parent": None}]}
    res = apply_ops(body, [thin])
    assert res.body["trees"]["p"]["nodes"]["u1"]["content"] == ""
    assert apply_ops(res.body, [thin]).changed is False


def test_the_cli_reads_the_same_tree_model():
    """cli.py's read helpers were absorbed into tree_ops; it imports them under
    their old private names. Two Python mirrors of tree.ts is what we just
    deleted, so pin that they are literally the same functions."""
    from tinkerscope import cli

    assert cli._active_path is tree_ops.active_path
    assert cli._selected_child is tree_ops.selected_child
    assert cli._thread_path is tree_ops.thread_path
    assert cli._ancestry is tree_ops.ancestry
    assert cli._root_of is tree_ops.root_of
    assert cli._siblings is tree_ops.siblings
    assert cli.ROOT == tree_ops.ROOT


def test_a_node_may_not_smuggle_children():
    """Children are derived from the parent pointers the server itself appends;
    an incoming list could only fabricate dangling refs."""
    body = _body({"p": {"nodes": {}, "rootChildren": [], "selected": {}}})
    with pytest.raises(OpError):
        apply_ops(body, [{"op": "add_nodes", "panel": "p", "nodes": [
            {"id": "u1", "role": "user", "content": "hi", "parent": None, "children": ["ghost"]}]}])


# ── set_meta ─────────────────────────────────────────────────────────────────
def test_set_meta_is_lww_except_the_two_ledgers():
    body = {"id": "w1", "trees": {}, "panel_seq": 7, "seen_panels": ["p-1", "p-2"], "name": "old"}
    res = apply_ops(body, [{"op": "set_meta", "fields": {
        "name": "new", "panel_seq": 3, "seen_panels": ["p-3"],
    }}])
    assert res.body["name"] == "new"
    assert res.body["panel_seq"] == 7, "monotone: a staler tab may not walk it back"
    assert res.body["seen_panels"] == ["p-1", "p-2", "p-3"], "union: an id is never un-seen"
    # The BROADCAST carries the merged values, not what was sent — a mirror must
    # converge on the winner of the max(), not on its own losing write.
    assert res.wire_ops[0]["fields"]["panel_seq"] == 7
    assert res.wire_ops[0]["fields"]["seen_panels"] == ["p-1", "p-2", "p-3"]


def test_set_meta_rejects_unknown_fields():
    """The op writes workspace metadata, so an unrecognized key is either a typo or
    an attempt to set something that isn't metadata — both worth a 409."""
    with pytest.raises(OpError):
        apply_ops({"id": "w1", "trees": {}}, [{"op": "set_meta", "fields": {"trees": {}}}])


# ── phantom-panel heal ───────────────────────────────────────────────────────
def test_phantom_panel_rows_are_dropped_on_set():
    """A run_id-less row whose panel has no tree can't sample and can't show
    anything — the inert phantom older layouts baked in."""
    trees = {"p-1": _tree(_node("u1", "user", "hi", None), roots=["u1"], selected={})}
    body = {"id": "w1", "trees": trees}
    res = apply_ops(body, [{"op": "set_meta", "fields": {"panels": [
        {"id": "p-1", "run_id": "run-a", "checkpoint": "final"},
        {"id": "p-9", "run_id": None, "checkpoint": None},
    ]}}])
    assert [p["id"] for p in res.body["panels"]] == ["p-1"]


def test_a_blank_panel_holding_a_TREE_survives():
    """The server is the only copy. A send-branch-to-panel target and a
    trash-restored column are both legitimately unbound with real content in them,
    so the heal is narrower here than the browser's load-time filter was."""
    trees = {"p-9": _tree(_node("u1", "user", "carried over", None), roots=["u1"], selected={})}
    body = {"id": "w1", "trees": trees}
    res = apply_ops(body, [{"op": "set_meta", "fields": {"panels": [
        {"id": "p-9", "run_id": None, "checkpoint": None},
    ]}}])
    assert [p["id"] for p in res.body["panels"]] == ["p-9"]


def test_the_heal_never_empties_a_layout():
    """A workspace always opens with ≥1 panel; a single blank row IS the
    empty-thread state, not a phantom."""
    assert tree_ops.normalize_panels([{"id": "p-1", "run_id": None}], {}) == [{"id": "p-1", "run_id": None}]
    assert tree_ops.normalize_panels([], {}) == []


# ── validation ───────────────────────────────────────────────────────────────
def test_validate_tree_catches_every_structural_break():
    good = _tree(
        _node("u1", "user", "hi", None, ["a1"]),
        _node("a1", "assistant", "yo", "u1"),
        roots=["u1"], selected={ROOT: "u1", "u1": "a1"},
    )
    tree_ops.validate_tree(good)  # no raise

    broken_parent = copy.deepcopy(good)
    broken_parent["nodes"]["a1"]["parent"] = "u2"
    island = copy.deepcopy(good)
    island["nodes"]["x1"] = _node("x1", "user", "unreachable", None)
    stale_selection = copy.deepcopy(good)
    stale_selection["selected"]["u1"] = "gone"
    for bad in (broken_parent, island, stale_selection):
        with pytest.raises(ValueError):
            tree_ops.validate_tree(bad)
