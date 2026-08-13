"""Server-authoritative workspace TREE model + the op protocol.

This is the Python half of `web/src/lib/tree.ts`. Since the server-authority
migration (`docs/HANDOFF_SERVER_AUTHORITY.md`) the branch tree is **no longer
opaque to the server**: every mutation arrives as a small op on
`POST /api/workspaces/{id}/ops`, is applied HERE under the workspaces flock,
persisted, and broadcast on the bus as an `ops` event with a per-workspace
`rev`. Clients keep an optimistic MIRROR (`tree.ts`) and converge by replaying
those events in rev order.

Shape (identical on both sides, and on disk):

    tree = {nodes: {id: {id, role, content, …, parent, children[]}},
            rootChildren: [id],
            selected: {parentKey: childId}}   # parentKey = node id or ROOT

`selected` maps parent → the selected child's **ID** (not an index), and an
unset/dangling entry defaults to the **LAST** child (newest) — so a fresh
fork auto-shows, and deleting a sibling never silently reselects.

CONFLUENCE GUARD (§4.2) — read before adding an op:

    Every op is either **idempotent-structural** (`add_nodes` by globally-unique
    id + append, `delete` subtree by id, `copy_tree` keep-ids) or
    **last-writer-wins** (`select`, `replace_tree`, `set_meta`). Both classes are
    confluent under rev-ordered replay: append order = rev order (which is why an
    existing node is re-APPENDED on its own echo — a mirror that applied its own
    add optimistically holds it in the wrong slot, and skipping it outright leaves
    two tabs permanently disagreeing on sibling order), a missing-parent add is
    rejected identically everywhere, and an LWW key depends only on the last op
    touching it. The test that decides all of this is
    `test_concurrent_folds_under_one_parent_converge`, and it compares WHOLE trees
    on purpose. **Never add an op that mutates node content
    in place or inserts at an arbitrary index** — either breaks confluence and
    forces a real CRDT. Node content being immutable after creation (edits/regens
    mint NEW nodes) is what makes the whole table idempotent, and is also what
    makes the storage blobs write-once.

Deliberate differences from `tree.ts`:

- **Ops mutate their working copy.** tree.ts clones on every op because the
  browser store is `$state.raw` (an in-place mutation would neither render nor
  save). Here the caller holds the flock and hands us a private copy, so the
  immutability dance buys nothing.
- **Almost no id minting.** Clients mint their own ids (`nid()`); the server
  mints only for its OWN writes — chat folds and the CLI's user-turn ops — via
  `mint_node_id()`, the same `n<session><counter>` scheme with the same
  two-writers-collide odds `tree.ts` accepts (§2 of the handoff).
- **Reads are dict-based** (the stored JSON), so every helper tolerates a
  malformed/legacy tree rather than raising — `as_tree` is the coercion point.

`cli.py`'s read helpers were absorbed here (they were a second, drifting Python
mirror of tree.ts); it imports them under their old private names.
"""
from __future__ import annotations

import copy
import itertools
import logging
import random
import string
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("tinkerscope.tree_ops")

ROOT = "__root__"
ROLES = ("user", "assistant", "system")

# Per-process random session prefix for server-minted node ids — the Python
# `nid()` (tree.ts:172). One namespace, two kinds of writer: browser tabs and
# this process each carry a distinct 4-char base36 session, so concurrent
# minting collides only at the ~1/1.7M-per-pair odds the design accepts.
_ID_ALPHABET = string.digits + string.ascii_lowercase
_id_session = "".join(random.choice(_ID_ALPHABET) for _ in range(4))
_id_counter = itertools.count(1)  # itertools.count: thread-safe next() in CPython


def mint_node_id() -> str:
    """A fresh node id in `nid()`'s format (`n<4-char session><base36 counter>`).
    Used by the server's chat folds and the CLI's user-turn ops."""
    n, digits = next(_id_counter), ""
    while n:
        n, r = divmod(n, 36)
        digits = _ID_ALPHABET[r] + digits
    return f"n{_id_session}{digits}"

#: Metadata keys `set_meta` (and the PATCH sugar over it) may write.
META_FIELDS = (
    "name",
    "system_prompt",
    "system_enabled",
    "panels",
    "reduced_panels",
    "send_targets",
    "seen_panels",
    "panel_seq",
)

# Reserved panel ids the pre-multipanel {tree, compare_tree} shape maps onto.
_LEGACY_PANELS = (("tree", "primary"), ("compare_tree", "compare"))


# ── construction / coercion ──────────────────────────────────────────────────
def empty_tree() -> dict:
    return {"nodes": {}, "rootChildren": [], "selected": {}}


def as_tree(t: Any) -> dict:
    """Coerce a stored value into a well-formed tree (mirrors `asTree` in
    web/src/lib/workspaces.svelte.ts). A brand-new workspace stores `{}` for its
    first panel and legacy bodies can hold junk — neither should blow up an op
    three frames deep. Returns the SAME dict when it is already well-formed."""
    if not isinstance(t, dict) or not isinstance(t.get("nodes"), dict):
        return empty_tree()
    if isinstance(t.get("rootChildren"), list) and isinstance(t.get("selected"), dict):
        return t
    out = dict(t)
    if not isinstance(out.get("rootChildren"), list):
        out["rootChildren"] = []
    if not isinstance(out.get("selected"), dict):
        out["selected"] = {}
    return out


def trees_of(body: Any) -> dict[str, Any]:
    """A workspace body's panel→tree map, with the legacy `{tree, compare_tree}`
    shape mapped onto its reserved panel ids — so callers speak one language."""
    if not isinstance(body, dict):
        return {}
    trees = body.get("trees")
    if isinstance(trees, dict) and trees:
        return trees
    out: dict[str, Any] = {}
    for legacy_key, panel in _LEGACY_PANELS:
        if body.get(legacy_key):
            out[panel] = body[legacy_key]
    return out


def normalize_legacy(body: dict) -> tuple[dict, bool]:
    """Fold a legacy `{tree, compare_tree}` body into `trees` — the seeding the
    retired PUT save path used to do, applied BEFORE the first op so a partial
    mutation can't drop the un-touched panel. Returns (body, changed); the input
    is never mutated.

    Under ops nobody ever ships a whole `trees` map (the retired browser-side
    full-map first save did), so the normalization happens on the side that owns
    the file."""
    if not isinstance(body, dict):
        return body, False
    has_legacy = any(k in body for k, _ in _LEGACY_PANELS)
    trees = body.get("trees")
    if isinstance(trees, dict) and trees and not has_legacy:
        return body, False
    out = dict(body)
    if not (isinstance(trees, dict) and trees):
        seeded: dict[str, Any] = {}
        for legacy_key, panel in _LEGACY_PANELS:
            if out.get(legacy_key):
                seeded[panel] = out[legacy_key]
        out["trees"] = seeded
    for legacy_key, _ in _LEGACY_PANELS:
        out.pop(legacy_key, None)
    # By VALUE, not by "we took the fold branch": a body with an empty `trees` map
    # and no legacy keys lands here and comes out identical, and claiming that as a
    # change would make every op batch on an empty workspace write and broadcast.
    return out, out != body


# ── derivation (absorbed from cli.py — mirrors tree.ts) ──────────────────────
def selected_child(tree: dict, parent_key: str) -> Optional[str]:
    """Selected child id of `parent_key`, defaulting to the LAST (newest) child."""
    kids = (
        tree.get("rootChildren", [])
        if parent_key == ROOT
        else (tree.get("nodes", {}).get(parent_key, {}) or {}).get("children", [])
    )
    if not kids:
        return None
    sel = (tree.get("selected") or {}).get(parent_key)
    return sel if (sel is not None and sel in kids) else kids[-1]


def thread_path(tree: dict, root_id: str) -> list[dict]:
    """Root sibling `root_id` → leaf, following the selected child at each step.
    A "thread" = one root-level sibling (a branch-from-start first message) and
    its subtree; this is the thread-scoped analogue of the active path."""
    nodes = tree.get("nodes", {})
    node = nodes.get(root_id)
    if node is None:
        return []
    path, seen, pk = [node], {root_id}, root_id
    while True:
        cid = selected_child(tree, pk)
        if cid is None or cid in seen:
            break
        node = nodes.get(cid)
        if node is None:
            break
        seen.add(cid)
        path.append(node)
        pk = cid
    return path


def active_path(tree: dict) -> list[dict]:
    """Root → leaf following the selected child at each step (mirrors activePath)."""
    sel = selected_child(tree, ROOT)
    return thread_path(tree, sel) if sel else []


def ancestry(tree: dict, node_id: str) -> list[dict]:
    """Root → `node_id` INCLUSIVE via the PARENT chain (mirrors tree.ts
    ancestryMessages) — works for ANY node regardless of the current selection, so
    `continue` can loom from a non-active branch. Returns the node dicts in order."""
    nodes = tree.get("nodes", {})
    chain: list[dict] = []
    cur, seen = nodes.get(node_id), set()
    while cur is not None and cur.get("id") not in seen:
        seen.add(cur.get("id"))
        chain.append(cur)
        parent = cur.get("parent")
        cur = nodes.get(parent) if parent else None
    chain.reverse()
    return chain


def root_of(tree: dict, node_id: str) -> str:
    """Walk parent pointers to the node's thread ROOT id (the node itself if it
    is a root / unknown)."""
    nodes = tree.get("nodes", {})
    cur, seen = node_id, set()
    while cur in nodes and cur not in seen:
        seen.add(cur)
        parent = nodes[cur].get("parent")
        if not parent:
            break
        cur = parent
    return cur


def siblings(tree: dict, node: dict) -> list[str]:
    """Ids of `node`'s siblings (children of its parent, or the roots)."""
    parent = node.get("parent")
    if parent is None:
        return tree.get("rootChildren", [])
    return (tree.get("nodes", {}).get(parent, {}) or {}).get("children", [])


def _child_array(tree: dict, parent_key: str) -> Optional[list]:
    """The mutable child list of `parent_key`, or None if it isn't a live node."""
    if parent_key == ROOT:
        kids = tree.setdefault("rootChildren", [])
        return kids if isinstance(kids, list) else None
    node = tree.get("nodes", {}).get(parent_key)
    if not isinstance(node, dict):
        return None
    kids = node.setdefault("children", [])
    return kids if isinstance(kids, list) else None


# ── validation (port of tree.ts assertValid) ─────────────────────────────────
def validate_tree(t: Any) -> None:
    """Raise ValueError on any structural corruption. Both directions (forward:
    listed children exist and point back; reverse: every node's parent resolves
    and lists it), child uniqueness, reachability from the roots, selected-key
    liveness. Applied to trees a CLIENT supplies wholesale (`replace_tree`) —
    never to trees we built ourselves, and never to already-stored ones (a legacy
    body that has always been slightly off must stay editable)."""
    if not isinstance(t, dict) or not isinstance(t.get("nodes"), dict):
        raise ValueError("tree must be {nodes, rootChildren, selected}")
    nodes = t["nodes"]
    root_children = t.get("rootChildren")
    selected = t.get("selected")
    if not isinstance(root_children, list) or not isinstance(selected, dict):
        raise ValueError("tree must be {nodes, rootChildren, selected}")
    if len(set(root_children)) != len(root_children):
        raise ValueError("rootChildren has duplicate child ids")
    for nid, n in nodes.items():
        if nid == ROOT:
            raise ValueError(f"node id collides with ROOT sentinel: {nid}")
        if not isinstance(n, dict):
            raise ValueError(f"node {nid} is not an object")
        if n.get("id") != nid:
            raise ValueError(f"node {nid} has mismatched .id {n.get('id')}")
        kids = n.get("children") or []
        if not isinstance(kids, list):
            raise ValueError(f"node {nid} children is not a list")
        if len(set(kids)) != len(kids):
            raise ValueError(f"node {nid} children has duplicate child ids")
        for c in kids:
            if c not in nodes:
                raise ValueError(f"node {nid} child {c} missing")
            if (nodes[c].get("parent") or ROOT) != nid:
                raise ValueError(f"child {c} parent pointer != {nid}")
        parent = n.get("parent")
        if parent is None:
            if nid not in root_children:
                raise ValueError(f"root node {nid} not in rootChildren")
        else:
            p = nodes.get(parent)
            if not isinstance(p, dict):
                raise ValueError(f"node {nid} parent {parent} missing")
            if nid not in (p.get("children") or []):
                raise ValueError(f"node {nid} not listed in parent {parent}")
    for c in root_children:
        if c not in nodes:
            raise ValueError(f"rootChild {c} missing")
        if nodes[c].get("parent") is not None:
            raise ValueError(f"rootChild {c} has non-null parent")
    seen: set[str] = set()
    stack = list(root_children)
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        stack.extend((nodes.get(nid) or {}).get("children") or [])
    if len(seen) != len(nodes):
        raise ValueError(f"unreachable nodes: {sorted(set(nodes) - seen)}")
    for parent_key, child_id in selected.items():
        kids = root_children if parent_key == ROOT else (nodes.get(parent_key) or {}).get("children")
        if kids is None:
            raise ValueError(f"selected key {parent_key} is not a live node/ROOT")
        if child_id not in kids:
            raise ValueError(f"selected[{parent_key}]={child_id} is not a child of it")


# ── meta merges (LWW except these two) ───────────────────────────────────────
def merge_panel_seq(stored: Any, incoming: Any) -> int:
    """`panel_seq` is MONOTONE, so a write may only raise it.

    A plain assignment let any writer that omits the field reset the counter to 0 —
    an older browser tab, a script, a pack apply (which exports the field but didn't
    import it). Because ids are also checked against everything the workspace has
    seen, that degraded rather than broke, but "correct only via the second
    mechanism" is not a guarantee. max() makes the field self-healing instead:
    a writer that doesn't know about it can no longer lose it."""
    s = stored if isinstance(stored, int) and not isinstance(stored, bool) else 0
    i = incoming if isinstance(incoming, int) and not isinstance(incoming, bool) else 0
    return max(s, i)


def merge_seen_panels(stored: Any, incoming: Any) -> list[str]:
    """`seen_panels` is a UNION, not a replacement.

    It began as first-sight bookkeeping (default a panel into send_targets once),
    where replace-wholesale was fine. It is now also the ledger that stops a closed
    panel's id being re-minted, and an id is never legitimately un-seen — so a
    writer with a shorter list must not be able to shrink it.

    Append-only, keeping first-seen order: the only reader tests membership, so order
    carries no behavior, but stored-then-new is both informative and a stable diff."""
    out: list[str] = []
    for src in (stored or []), (incoming or []):
        if not isinstance(src, list):
            continue
        for x in src:
            if isinstance(x, str) and x not in out:
                out.append(x)
    return out


def _panel_has_data(trees: Any, pid: Any) -> bool:
    tree = trees.get(pid) if isinstance(trees, dict) else None
    return bool(isinstance(tree, dict) and tree.get("nodes"))


def normalize_panels(panels: Any, trees: Any) -> Any:
    """Phantom-panel self-heal, relocated server-side (§4.1).

    A panel row with `run_id: null` can't sample anything — it is the inert
    "phantom" the resurrection bug used to mint, and earlier sessions baked some
    into saved layouts. The browser dropped them on every load
    (`workspaces.svelte.ts#loadTrees`); with the layout server-authoritative that
    filter has to live where the layout is written, or a stored phantom
    resurrects on the next open.

    ONE deviation from the browser's rule, because the two sides answer to
    different risks: a blank row whose panel HAS a tree is kept. The browser could
    drop it (it re-reads the file next load); dropping it here would strand or —
    if we also dropped the tree, as the browser does — DESTROY nodes, and the
    server is the only copy. A trash-restored column and a send-branch-to-panel
    target are both legitimately run_id-less with real content in them.

    Never empties a non-empty layout: a workspace always opens with ≥1 panel, and
    a single blank row IS the empty-thread state."""
    if not isinstance(panels, list) or not panels:
        return panels
    kept = [
        p
        for p in panels
        if not (isinstance(p, dict) and p.get("run_id") is None and not _panel_has_data(trees, p.get("id")))
    ]
    return kept or [panels[0]]


def apply_meta(body: dict, fields: dict, trees: Any = None) -> tuple[dict, bool]:
    """Apply a `set_meta` field map to `body` IN PLACE (the caller owns the copy).

    Field-wise last-writer-wins, except the two fields that are not values but
    ledgers (`panel_seq` monotone, `seen_panels` union) and `panels`, which is
    normalized (phantom heal). Returns the APPLIED values — post-merge, not as
    sent — because that is what the bus broadcasts: a mirror must converge on the
    merged result, not on the loser of a max()."""
    applied: dict[str, Any] = {}
    changed = False
    if trees is None:
        trees = trees_of(body)
    for k in META_FIELDS:
        if k not in fields:
            continue
        v = fields[k]
        if k == "panel_seq":
            v = merge_panel_seq(body.get(k), v)
        elif k == "seen_panels":
            v = merge_seen_panels(body.get(k), v)
        elif k == "panels":
            v = normalize_panels(v, trees)
        if k not in body or body[k] != v:
            changed = True
        body[k] = v
        applied[k] = v
    return applied, changed


# ── the op protocol ──────────────────────────────────────────────────────────
class OpError(Exception):
    """A rejected op. Discards the WHOLE batch (all-or-nothing) → HTTP 409 with
    `{index, error}`; nothing is persisted and `rev` does not move. `index` is
    stamped by `apply_ops` so op implementations can just raise a message."""

    def __init__(self, message: str, index: int = -1) -> None:
        super().__init__(message)
        self.error = message
        self.index = index


@dataclass
class ApplyResult:
    body: dict
    """The mutated working copy (rev/updated_at are the store's business)."""
    results: list[dict] = field(default_factory=list)
    """Per-op `{ok, noop}`, positionally matching the request."""
    blobs: dict[str, dict] = field(default_factory=dict)
    """Heavy node fields split out for write-once blob storage."""
    wire_ops: list[dict] = field(default_factory=list)
    """The ops AS APPLIED (light nodes, merged meta values) — the bus payload."""
    changed: bool = False
    """False ⇒ a pure replay: skip the write, skip the broadcast, keep `rev`."""


class _Ctx:
    """One batch's working state. Panel trees are deep-copied on first WRITE, so
    a rejected batch leaves the caller's body untouched and an all-read batch
    costs nothing."""

    def __init__(self, body: dict) -> None:
        self.body = body
        self.trees: dict[str, Any] = dict(body.get("trees") or {})
        body["trees"] = self.trees
        self.blobs: dict[str, dict] = {}
        self.changed = False
        self._writable: set[str] = set()

    def peek(self, panel: str) -> Optional[dict]:
        """Read-only view of a panel's tree (no copy), or None if there is none."""
        t = self.trees.get(panel)
        return as_tree(t) if isinstance(t, dict) else None

    def writable(self, panel: str, create: bool = False) -> Optional[dict]:
        if panel in self._writable:
            return self.trees[panel]
        if panel not in self.trees:
            if not create:
                return None
            self.trees[panel] = empty_tree()
        else:
            self.trees[panel] = as_tree(copy.deepcopy(self.trees[panel]))
        self._writable.add(panel)
        return self.trees[panel]

    def put(self, panel: str, tree: dict) -> None:
        self.trees[panel] = tree
        self._writable.add(panel)

    def drop(self, panel: str) -> None:
        self.trees.pop(panel, None)
        self._writable.discard(panel)


def apply_ops(body: dict, ops: Any) -> ApplyResult:
    """Apply an op batch to a copy of `body`. All-or-nothing: the first rejection
    raises `OpError` (with `.index`) and the caller discards everything.

    Callers hold `store.locked("workspaces")` around load → apply → persist; this
    function does no I/O and never mutates its argument."""
    if not isinstance(ops, list):
        raise OpError("`ops` must be a list")
    work, legacy_folded = normalize_legacy(dict(body))
    ctx = _Ctx(work)
    ctx.changed = legacy_folded
    results: list[dict] = []
    wire_ops: list[dict] = []
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            raise OpError("op must be an object", i)
        kind = op.get("op")
        fn = _OPS.get(kind) if isinstance(kind, str) else None
        if fn is None:
            raise OpError(f"unknown op {kind!r}", i)
        try:
            changed, wire = fn(ctx, op)
        except OpError as e:
            e.index = i
            raise
        ctx.changed = ctx.changed or changed
        results.append({"ok": True, "noop": not changed})
        wire_ops.append(wire)
    # Per-op accounting says whether an op moved something WHEN IT RAN; this says
    # whether the batch left the stored body different, which is the question the
    # caller is actually asking ("do I write and broadcast?"). They differ when a
    # batch undoes itself — a replayed select-then-reselect, say. Only computed on
    # the path that was about to serialize the whole body anyway.
    return ApplyResult(
        body=work,
        results=results,
        blobs=ctx.blobs,
        wire_ops=wire_ops,
        changed=ctx.changed and work != body,
    )


# ── op implementations ───────────────────────────────────────────────────────
def _req_str(op: dict, key: str) -> str:
    v = op.get(key)
    if not isinstance(v, str) or not v:
        raise OpError(f"{op.get('op')} needs a `{key}` string")
    return v


def _safe_node_id(x: Any) -> bool:
    from .workspace_store import is_safe_id  # deferred: store imports this module

    return is_safe_id(x) and x != ROOT


def _split_node(node: dict) -> tuple[dict, dict]:
    from .workspace_store import split_node  # deferred: store imports this module

    return split_node(node)


def _op_add_nodes(ctx: _Ctx, op: dict) -> tuple[bool, dict]:
    panel = _req_str(op, "panel")
    nodes = op.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise OpError("add_nodes needs a non-empty `nodes` list")
    select = bool(op.get("select"))
    tree = ctx.writable(panel, create=True)
    assert tree is not None
    changed = False
    wire_nodes: list[dict] = []
    # Parents an earlier node of THIS OP already selected under. Scope is per-OP,
    # not per-batch, and that is a correctness requirement rather than a detail: a
    # mirror replays a broadcast op AT A TIME, so a batch-wide claim set makes
    # batch-apply differ from per-op replay of its own broadcast. Two add_nodes ops
    # under one parent then leave the server on the first node and every mirror on
    # the second, revs contiguous, forever. Both semantics survive the narrowing —
    # a fold's fan is one op (first sibling wins) and a chain's nodes have distinct
    # parents (every step selected).
    claimed: set[str] = set()
    for node in nodes:
        light, node_changed = _add_one(ctx, tree, node, select, claimed)
        changed = changed or node_changed
        # Minus `children`, per the op's own wire shape — the STORED node carries
        # them (populated by the adds) and shipping them would make the broadcast
        # unreplayable by an interpreter that validates its input the way we do,
        # while tempting one that doesn't into adopting a child list assembled by
        # ops the mirror may not have applied yet. Children are derived from parent
        # pointers on both sides. Copy, never mutate: `light` IS the stored node.
        wire_nodes.append({k: v for k, v in light.items() if k != "children"})
    return changed, {"op": "add_nodes", "panel": panel, "nodes": wire_nodes, "select": select}


def _add_one(ctx: _Ctx, tree: dict, node: Any, select: bool, claimed: set[str]) -> tuple[dict, bool]:
    if not isinstance(node, dict):
        raise OpError("each entry of `nodes` must be an object")
    nid = node.get("id")
    if not _safe_node_id(nid):
        raise OpError(f"unsafe node id {nid!r}")
    role = node.get("role")
    if role not in ROLES:
        raise OpError(f"node {nid}: role must be one of {ROLES}, got {role!r}")
    content = node.get("content", "")
    if not isinstance(content, str):
        raise OpError(f"node {nid}: content must be a string")
    parent = node.get("parent")
    if parent is not None and not isinstance(parent, str):
        raise OpError(f"node {nid}: parent must be a node id or null")
    if node.get("children"):
        raise OpError(f"node {nid}: `children` are server-owned — send none")

    added = False
    reordered = False
    existing = tree["nodes"].get(nid)
    if isinstance(existing, dict):
        # Idempotent replay (a retried batch, or a mirror re-sending its own echo).
        # Content is immutable after creation, so a MISMATCH is a client bug, not a
        # concurrent edit — reject loudly rather than pick a winner.
        # role/content/parent are a node's IDENTITY — all three immutable after
        # creation. Other fields are deliberately not compared: a replay can carry
        # heavy fields inline that the stored light node now holds as `has_*` flags,
        # so a whole-body compare would false-reject every retry. Parent is in the
        # set because without it a buggy client can re-send an id under a DIFFERENT
        # parent and be silently skipped — and then the re-append below finds the id
        # missing from that parent's children and quietly does nothing. Loud beats
        # defensive, same rationale as the content assert.
        if (
            existing.get("role") != role
            or existing.get("content") != content
            or existing.get("parent") != parent
        ):
            raise OpError(f"node {nid} already exists with different role/content/parent")
        light = existing
        parent_key = existing.get("parent") or ROOT
        # Stage this node's heavy fields even though we're skipping the insert.
        # The light node's `has_*` flags can already be on disk with NO blob behind
        # them — a create that shipped an already-lightened tree (a draft
        # materializing after a failed first attempt) writes the flags, and then the
        # op replay that carries the actual data lands here. Without this, the
        # token_logprobs are gone for good and the UI just says "no token data".
        # Write-once makes it a no-op whenever the blob does exist, so a normal
        # replay costs nothing.
        _, replay_blob = _split_node({k: v for k, v in node.items() if k != "children"})
        if replay_blob:
            ctx.blobs[nid] = replay_blob
        # Re-APPEND it. Sibling order is "append order = rev order", and a mirror
        # that applied its own add optimistically has the node in the wrong slot:
        # tab B holds [b1] and appends the echoed a1 to get [b1, a1] where the
        # server has [a1, b1]. Moving an existing node to the end on its own echo
        # makes replay reproduce rev order exactly — and a full batch replay is
        # still a no-op, since moving each of [a1,a2,a3] to the end in turn lands
        # them back in the same order. Order matters beyond cosmetics: with no
        # `selected` entry the render falls back to the LAST child, so two tabs
        # disagreeing on order eventually disagree on the active path.
        kids = _child_array(tree, parent_key)
        if kids is not None and nid in kids and kids[-1] != nid:
            kids.remove(nid)
            kids.append(nid)
            reordered = True
    else:
        parent_key = parent if parent is not None else ROOT
        if parent is not None and parent not in tree["nodes"]:
            raise OpError(f"node {nid}: parent {parent!r} does not exist")
        light, blob = _split_node({k: v for k, v in node.items() if k != "children"})
        # The four structural fields are the server's, not the sender's: every
        # stored node HAS them (a `content`-less node would read back as None and
        # then fail its own idempotency check on the next replay), and children are
        # derived from the parent pointers we append below.
        light["id"] = nid
        light["role"] = role
        light["content"] = content
        light["parent"] = parent
        light["children"] = []
        tree["nodes"][nid] = light
        kids = _child_array(tree, parent_key)
        if kids is None:  # unreachable (parent existence checked above)
            raise OpError(f"node {nid}: parent {parent!r} has no child list")
        kids.append(nid)
        if blob:
            ctx.blobs[nid] = blob
        added = True

    selected_changed = False
    if select:
        # First node to claim this parent in the batch writes the selection — one
        # rule giving chain semantics (each node its own parent ⇒ every step
        # selected) and fold semantics (a fan shares a parent ⇒ the FIRST selected).
        #
        # It writes whether or not THIS batch minted the node, and that is
        # load-bearing for convergence, not laziness: making the write conditional
        # on "we added it" makes the outcome depend on the mirror's own optimistic
        # state rather than on the op sequence, and two tabs folding under one
        # parent then strand on different siblings with contiguous revs — no gap,
        # so no refetch ever corrects it. Measured, see ENGINEERING_LOGS 2026-08-12.
        # The cost is the accepted LWW race: a retried batch re-asserts its
        # selection over a sibling the user cycled to in between.
        if parent_key not in claimed:
            claimed.add(parent_key)
            if tree["selected"].get(parent_key) != nid:
                tree["selected"][parent_key] = nid
                selected_changed = True
    return light, added or selected_changed or reordered


def _op_select(ctx: _Ctx, op: dict) -> tuple[bool, dict]:
    panel = _req_str(op, "panel")
    parent_key = _req_str(op, "parent_key")
    child_id = _req_str(op, "child_id")
    wire = {"op": "select", "panel": panel, "parent_key": parent_key, "child_id": child_id}
    tree = ctx.peek(panel)
    if tree is None:
        return False, wire
    kids = tree.get("rootChildren") if parent_key == ROOT else (tree["nodes"].get(parent_key) or {}).get("children")
    # An unknown parent / stale child is an ACCEPTED no-op, not a rejection: the
    # render clamp (default = last child) makes a stale selection harmless, and
    # rejecting would turn every ordinary cross-tab race into a refetch storm.
    if not isinstance(kids, list) or child_id not in kids:
        return False, wire
    if tree.get("selected", {}).get(parent_key) == child_id:
        return False, wire
    tree = ctx.writable(panel)
    assert tree is not None
    tree["selected"][parent_key] = child_id
    return True, wire


def _op_delete(ctx: _Ctx, op: dict) -> tuple[bool, dict]:
    panel = _req_str(op, "panel")
    node_id = _req_str(op, "node_id")
    wire = {"op": "delete", "panel": panel, "node_id": node_id}
    tree = ctx.peek(panel)
    if tree is None or node_id not in tree["nodes"]:
        return False, wire  # already gone — idempotent
    _guard_inflight_delete(ctx.body.get("id"), panel, tree, node_id)
    tree = ctx.writable(panel)
    assert tree is not None
    _delete_subtree(tree, node_id)
    return True, wire


def _guard_inflight_delete(ws_id: Any, panel: str, tree: dict, node_id: str) -> None:
    """Reject a delete whose doomed subtree contains the parent node of an
    IN-FLIGHT chat (§5's one real cross-client race): the terminal fold would
    otherwise land on a pruned parent and be lost. Subtree containment covers
    ancestors for free — deleting an ancestor of the parent prunes it too. The
    caller holds the workspaces flock, which `register_chat_placement` also
    takes, so guard and registration are serialized.

    Deliberately delete-only: `replace_tree`/`copy_tree` can also orphan a
    registered parent cross-client, but no shipped client emits those
    mid-generation (the browser busy-gates reset/undo per panel) — that residual
    window ends in the fold's loud rejection log, same as the §4.3
    delete-races-the-fire acceptance, not in silent loss."""
    from .inflight import parents_under  # deferred: keep tree_ops import-light

    if not isinstance(ws_id, str) or not ws_id:
        return  # vector/unit-test trees carry no workspace id — nothing registered
    registered = parents_under(ws_id, panel)
    if not registered:
        return
    hit = sorted(registered & set(_collect_subtree(tree, node_id)))
    if hit:
        raise OpError(
            f"delete rejected: a chat is generating under node {hit[0]} — "
            "stop it (or wait for its terminal) first"
        )


def _collect_subtree(tree: dict, node_id: str) -> list[str]:
    """`node_id` + every descendant id (the ids `_delete_subtree` would prune)."""
    out: list[str] = []
    stack = [node_id]
    while stack:
        nid = stack.pop()
        out.append(nid)
        n = tree["nodes"].get(nid)
        if isinstance(n, dict):
            stack.extend(n.get("children") or [])
    return out


def _delete_subtree(tree: dict, node_id: str) -> None:
    """Prune `node_id` + its whole subtree, in place (port of tree.ts
    deleteSubtree). Selection-by-id means unrelated siblings keep theirs; only the
    parent's now-dangling entry is dropped, so default-last picks a survivor."""
    node = tree["nodes"].get(node_id)
    if not isinstance(node, dict):
        return
    parent_key = node.get("parent") or ROOT
    to_remove = _collect_subtree(tree, node_id)
    removed = set(to_remove)
    kids = _child_array(tree, parent_key)
    if kids is not None and node_id in kids:
        kids.remove(node_id)
    for nid in to_remove:
        tree["nodes"].pop(nid, None)
        tree["selected"].pop(nid, None)
    if tree["selected"].get(parent_key) in removed:
        del tree["selected"][parent_key]


def _op_copy_tree(ctx: _Ctx, op: dict) -> tuple[bool, dict]:
    """Whole-tree keep-ids clone (the add-panel `duplicateTo`).

    Keep-ids is the point: it is what lets the two panels SHARE their write-once
    node blobs instead of duplicating megabytes of logprobs. §4.1 specified a
    `copy_subtree {node_id}`, but duplicateTo is the only call site and it copies
    everything — a partial keep-ids splice would have to answer what happens when
    the destination already holds those ids, with no user asking the question."""
    from_panel = _req_str(op, "from_panel")
    to_panel = _req_str(op, "to_panel")
    wire = {"op": "copy_tree", "from_panel": from_panel, "to_panel": to_panel}
    src = ctx.peek(from_panel)
    if src is None:
        raise OpError(f"copy_tree: unknown source panel {from_panel!r}")
    clone = copy.deepcopy(src)
    if ctx.trees.get(to_panel) == clone:
        return False, wire
    ctx.put(to_panel, clone)
    return True, wire


def _op_replace_tree(ctx: _Ctx, op: dict) -> tuple[bool, dict]:
    panel = _req_str(op, "panel")
    incoming = op.get("tree")
    if incoming is None:
        if panel not in ctx.trees:
            return False, {"op": "replace_tree", "panel": panel, "tree": None}
        ctx.drop(panel)
        return True, {"op": "replace_tree", "panel": panel, "tree": None}
    if not isinstance(incoming, dict):
        raise OpError("replace_tree: `tree` must be an object or null")
    light = as_tree(copy.deepcopy(incoming))
    nodes: dict[str, Any] = {}
    for nid, node in (light.get("nodes") or {}).items():
        if not isinstance(node, dict):
            raise OpError(f"replace_tree: node {nid} is not an object")
        # Same id charset `add_nodes` enforces — node ids become blob FILENAMES.
        # Without this an unsafe id either reaches `_write_blobs`, whose `_check_id`
        # raises an uncaught ValueError (a 500, which the client's retry policy
        # treats as retriable and burns its attempts on), or — with no heavy field
        # to trip that check — PERSISTS into the light tree as a node whose blobs
        # can never be read back.
        if not _safe_node_id(nid):
            raise OpError(f"replace_tree: unsafe node id {nid!r}")
        lnode, blob = _split_node(node)
        nodes[nid] = lnode
        if blob:
            ctx.blobs[nid] = blob
    light["nodes"] = nodes
    try:
        validate_tree(light)
    except ValueError as e:
        raise OpError(f"replace_tree: {e}") from e
    wire = {"op": "replace_tree", "panel": panel, "tree": light}
    if ctx.trees.get(panel) == light:
        return False, wire
    ctx.put(panel, light)
    return True, wire


def _op_set_meta(ctx: _Ctx, op: dict) -> tuple[bool, dict]:
    fields = op.get("fields")
    if not isinstance(fields, dict):
        raise OpError("set_meta needs a `fields` object")
    unknown = [k for k in fields if k not in META_FIELDS]
    if unknown:
        raise OpError(f"set_meta: unknown field(s) {unknown}")
    applied, changed = apply_meta(ctx.body, fields, trees=ctx.trees)
    return changed, {"op": "set_meta", "fields": applied}


_OPS = {
    "add_nodes": _op_add_nodes,
    "select": _op_select,
    "delete": _op_delete,
    "copy_tree": _op_copy_tree,
    "replace_tree": _op_replace_tree,
    "set_meta": _op_set_meta,
}
