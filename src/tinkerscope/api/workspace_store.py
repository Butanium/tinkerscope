"""Storage v2 for saved workspace TREES — per-workspace files + node blobs.

WHY (measured): the v1 single-`conversations.json` design put every conversation
WITH full trees in one file. One real workspace hit 380 MB, and 89.8% of a heavy
workspace's bytes were `token_logprobs` (raw_meta another 6.9%). Loading /
re-saving that whole file — and shipping every tree to the browser on page load —
OOMed the tab. See `docs/STORAGE_V2.md` for the byte breakdown.

WHAT: a node's two heavy fields — **`token_logprobs` and `raw_meta`** — move OUT
of the tree into per-node **write-once blobs**. Everything else (content,
raw_text, prefill, finish_reason, reasoning, thinking, parent/children) stays in
the light tree; light nodes carry `has_token_logprobs` / `has_raw_meta` presence
flags so the UI can gate affordances without the payload.

ON-DISK LAYOUT (per instance/state dir):

    <state>/workspaces/<cid>.json           # light workspace (light trees)
    <state>/workspaces/<cid>.blobs/<nid>.json   # {"token_logprobs":[...]?, "raw_meta":"..."?}
    <state>/workspaces/<cid>.layouts.jsonl      # {ts, panels} per panel-layout CHANGE
    <state>/conversations.json.legacy          # pre-v2 file, renamed after migration

Blob invariant: **write-once**. Logprobs/raw_meta never change after a node is
created (edits/regens mint new nodes), so a blob that already exists on disk is
never rewritten (idempotent retries are free), and blobs are deleted only when
their whole workspace is deleted.

Blobs are keyed by node id, flat within one workspace's `.blobs/` dir. Node ids
are globally unique within a workspace (one client-side counter mints them),
and add-model's `duplicateTo` CLONES a panel's tree keeping the SAME ids — so two
panels can share a node id, and the shared blob is written once (identical data).

REVISIONS: every workspace carries a monotonic `rev`, bumped in `_persist` — the
one choke point every write channel passes through (ops, PATCH, create, pack
apply, trash restore). Attached clients mirror the tree optimistically and
converge on the bus `ops` events, so a channel that moved a workspace without
bumping `rev` would leave them silently stale. 0 = written before revs existed;
the read path synthesizes it for those files rather than rewriting them.

CACHING: an in-memory `_summaries` map (id → {id,name,created_at,updated_at,panels,rev})
is built once at boot and maintained on every write — `GET /api/workspaces`
never re-parses the store. Parsed light bodies are memoized in `_bodies`, evicted
on write. Every mutation is wrapped in `store.locked("workspaces")` (the flock
convention) so two threads / a second process can't clobber sibling files or the
caches; mutations never nest the lock.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import inflight, tree_ops
from .store import locked, write_json

log = logging.getLogger("tinkerscope.workspace_store")

# Heavy per-node fields that live in blobs, not the light tree.
BLOB_FIELDS = ("token_logprobs", "raw_meta")
# light-node presence flag for each heavy field.
_FLAG = {"token_logprobs": "has_token_logprobs", "raw_meta": "has_raw_meta"}
_FLAGS = tuple(_FLAG.values())

# Workspace / node ids become path components — confine them to safe chars so a
# crafted id can't escape the store dir. Real ids are uuids, `draft-...` slugs, or
# `n<session><counter>` node ids, all within this set.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_safe_id(x: Any) -> bool:
    return isinstance(x, str) and bool(_SAFE_ID.match(x))


def _check_id(x: Any, kind: str) -> str:
    if not _is_safe_id(x):
        raise ValueError(f"unsafe {kind} id: {x!r}")
    return x


def is_safe_id(x: Any) -> bool:
    """Public: is this a filename-safe workspace id? The create route uses it to
    reject a crafted client id with a clean 400 instead of surfacing upsert's internal
    ValueError as a 500."""
    return _is_safe_id(x)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── paths (resolved lazily from SETTINGS so a test that reloads settings — new
#    XDG_STATE_HOME — is picked up, mirroring store.locked) ──────────────────────
def _state_dir() -> Path:
    from .settings import SETTINGS

    return SETTINGS.state_dir


def _legacy_path() -> Path:
    from .settings import SETTINGS

    return SETTINGS.legacy_conversations_path  # <state_dir>/conversations.json


def _ws_dir() -> Path:
    return _state_dir() / "workspaces"


def _ws_file(cid: str) -> Path:
    return _ws_dir() / f"{cid}.json"


def _blobs_dir(cid: str) -> Path:
    return _ws_dir() / f"{cid}.blobs"


def _blob_file(cid: str, nid: str) -> Path:
    return _blobs_dir(cid) / f"{nid}.json"


def _layouts_file(cid: str) -> Path:
    # NB: `.jsonl`, so the `*.json` summary glob never picks it up as a workspace.
    return _ws_dir() / f"{cid}.layouts.jsonl"


# ── node/tree/workspace split + re-materialize (PURE — never mutate input) ────
def split_node(node: dict) -> tuple[dict, dict]:
    """Split one tree node into (light_node, blob).

    - An INLINE heavy field (fresh fold straight off a sample) is moved into the
      blob and, when truthy, sets the light node's `has_*` flag.
    - A node that is ALREADY light (carries a `has_*` flag but no inline field —
      an unchanged node re-sent in a dirty panel's tree) keeps its flag and
      produces no blob entry.
    - The round-trip is EXACT for migration: legacy nodes have no `has_*` flags,
      so moving heavy keys by presence and restoring them by presence reconstructs
      the original byte-for-byte (a present-but-null heavy field round-trips too).
    """
    light = {k: v for k, v in node.items() if k not in BLOB_FIELDS}
    blob: dict = {}
    for f in BLOB_FIELDS:
        flag = _FLAG[f]
        if f in node:  # inline heavy field present → move it out
            blob[f] = node[f]
            if node[f]:  # truthy → affordance flag; falsy (null/[]) → no flag
                light[flag] = True
            else:
                light.pop(flag, None)
        # else: no inline field → light keeps whatever flag it already carried.
    return light, blob


def materialize_node(light: dict, blob: dict | None) -> dict:
    """Inverse of split_node: strip the `has_*` flags and fold the blob back in.

    Used only by migration verification (blobs come from the same split), never on
    the read path — the browser fetches blobs lazily via /node-blobs."""
    node = {k: v for k, v in light.items() if k not in _FLAGS}
    if blob:
        node.update(blob)
    return node


def _split_tree(tree: Any, blobs: dict[str, dict]) -> Any:
    """Split every node of one panel's tree; accumulate blobs by node id. A tree
    with no `nodes` key (empty `{}` or a legacy {tree}/{compare_tree} opaque blob
    with nothing to split) passes through untouched."""
    if not isinstance(tree, dict) or "nodes" not in tree:
        return tree
    light = {k: v for k, v in tree.items() if k != "nodes"}
    light_nodes: dict[str, Any] = {}
    for nid, node in (tree.get("nodes") or {}).items():
        lnode, blob = split_node(node) if isinstance(node, dict) else (node, {})
        light_nodes[nid] = lnode
        if blob:
            blobs[nid] = blob  # shared id across panels → identical data, last wins
    light["nodes"] = light_nodes
    return light


# Tree-bearing workspace keys. `trees` is the v2 {panel_id: tree} map; `tree` /
# `compare_tree` are the pre-multipanel single trees (2 of Clément's 16 real
# workspaces still carry that shape — migration must split blobs out of them too,
# and preserve their key presence EXACTLY for the round-trip verify).
_TREES_MAP_KEY = "trees"
_SINGLE_TREE_KEYS = ("tree", "compare_tree")


def split_workspace(conv: dict) -> tuple[dict, dict[str, dict]]:
    """Split a full conversation into (light_conversation, {node_id: blob}).

    Copies conv verbatim except its tree-bearing keys, whose nodes are split. Only
    keys actually present are emitted (never synthesizes a `trees` key on a legacy
    {tree, compare_tree} entry). Does not mutate conv (the original stays intact for
    migration's deep-compare)."""
    blobs: dict[str, dict] = {}
    light: dict = {}
    for k, v in conv.items():
        if k == _TREES_MAP_KEY:
            # Pass a null/non-dict `trees` through unchanged so the round-trip stays
            # honest (coercing null→{} would spuriously fail the migration verify).
            light[k] = {pid: _split_tree(t, blobs) for pid, t in v.items()} if isinstance(v, dict) else v
        elif k in _SINGLE_TREE_KEYS:
            light[k] = _split_tree(v, blobs)
        else:
            light[k] = v
    return light, blobs


def _materialize_tree(ltree: Any, blobs: dict[str, dict]) -> Any:
    if not isinstance(ltree, dict) or "nodes" not in ltree:
        return ltree
    tree = {k: v for k, v in ltree.items() if k != "nodes"}
    tree["nodes"] = {
        nid: (materialize_node(lnode, blobs.get(nid)) if isinstance(lnode, dict) else lnode)
        for nid, lnode in (ltree.get("nodes") or {}).items()
    }
    return tree


def materialize_workspace(light: dict, blobs: dict[str, dict]) -> dict:
    """Inverse of split_workspace (migration verification only)."""
    conv: dict = {}
    for k, v in light.items():
        if k == _TREES_MAP_KEY:
            conv[k] = {pid: _materialize_tree(t, blobs) for pid, t in v.items()} if isinstance(v, dict) else v
        elif k in _SINGLE_TREE_KEYS:
            conv[k] = _materialize_tree(v, blobs)
        else:
            conv[k] = v
    return conv


def _rev_of(body: Any) -> int:
    """A body's stored revision. 0 for anything written before revs existed —
    which is also the value a mirror starts from, so a legacy workspace's first
    write lands at 1 and looks like an ordinary first event."""
    rev = body.get("rev") if isinstance(body, dict) else None
    return rev if isinstance(rev, int) and not isinstance(rev, bool) and rev >= 0 else 0


def _with_rev(body: dict | None) -> dict | None:
    """A read-path body that always carries `rev` (legacy files have none). Shallow
    copy — never poison the memoized body with a synthesized field."""
    if body is None or "rev" in body:
        return body
    return {**body, "rev": 0}


def _summary_of(light: dict) -> dict:
    return {
        "id": light.get("id"),
        "name": light.get("name"),
        "created_at": light.get("created_at"),
        "updated_at": light.get("updated_at"),
        "panels": light.get("panels") or [],
        "rev": _rev_of(light),
    }


# ── in-memory caches ─────────────────────────────────────────────────────────
# `store.locked` (flock) serializes WRITERS across threads/processes for on-disk
# safety. This in-process lock ADDITIONALLY guards the shared cache dicts: FastAPI
# runs these sync handlers in a threadpool, so a lock-free reader (GET, fired on
# every page load) can run concurrently with a writer's insert — without this,
# `sorted(_summaries, …)` mid-insert crashes ("dictionary changed size during
# iteration"), and a reader could observe a half-built cache. Held only for the
# microseconds of dict access — NEVER across file I/O, and writers always take the
# flock BEFORE this lock (readers take this lock alone), so the two can't deadlock.
_CACHE_LOCK = threading.Lock()
_summaries: dict[str, dict] | None = None  # id -> summary; None = not yet built
_bodies: dict[str, dict] = {}  # id -> light body (parsed, evicted on write)


def reset_cache() -> None:
    """Drop both caches. Called at boot and (implicitly, via module reload) by tests
    so a fresh state dir never sees a previous run's cache."""
    global _summaries
    with _CACHE_LOCK:
        _summaries = None
        _bodies.clear()


def _quarantine(path: Path) -> None:
    """Move a corrupt file aside (never silently wipe a user-authored tree)."""
    try:
        path.rename(path.with_suffix(path.suffix + f".corrupt-{int(time.time())}"))
    except OSError:
        pass


def _build_summaries() -> dict[str, dict]:
    """Read each light file's head into a fresh summary map (file I/O; no lock held —
    the caller assigns the result under _CACHE_LOCK)."""
    built: dict[str, dict] = {}
    d = _ws_dir()
    if not d.exists():
        return built
    for f in sorted(d.glob("*.json")):
        try:
            conv = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            _quarantine(f)
            continue
        cid = conv.get("id")
        if cid:
            built[cid] = _summary_of(conv)
    return built


def _ensure_loaded() -> None:
    """Build the summary cache from disk once. Fast no-op once built (the common
    serving case — boot() builds it before the first request)."""
    global _summaries
    if _summaries is not None:
        return
    built = _build_summaries()  # disk reads OUTSIDE the lock
    with _CACHE_LOCK:
        if _summaries is None:  # double-check: first builder wins, others discard
            _summaries = built


def _snapshot_ordered_cids() -> list[str]:
    """Ordered cid snapshot taken atomically (safe against concurrent inserts).
    Deterministic: by created_at (append order for new creates), id tiebreak."""
    with _CACHE_LOCK:
        assert _summaries is not None
        return sorted(_summaries, key=lambda c: (_summaries[c].get("created_at") or "", c))


def _load_body(cid: str) -> dict | None:
    """Parsed light body for one workspace (memoized). None if missing/corrupt.
    The file read happens outside the lock; on insert we re-check under the lock so a
    concurrent writer's fresher body wins, and only cache when the file STILL exists —
    otherwise a DELETE landing during our read would leave a ghost body GETtable until
    restart (delete unlinks + pops under the same lock; the returned snapshot is still
    the valid content we read, it just isn't poisoned back into the cache)."""
    if not _is_safe_id(cid):  # a crafted id must not build a path outside the store
        return None
    with _CACHE_LOCK:
        if cid in _bodies:
            return _bodies[cid]
    f = _ws_file(cid)
    if not f.exists():
        return None
    try:
        conv = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        _quarantine(f)
        return None
    with _CACHE_LOCK:
        if cid in _bodies:  # a writer cached a (fresher) body while we read the file
            return _bodies[cid]
        if f.exists():  # not deleted out from under us → safe to memoize
            _bodies[cid] = conv
        return conv


def _write_blobs(cid: str, blobs: dict[str, dict]) -> None:
    """Persist node blobs — WRITE-ONCE: an existing blob file is never rewritten."""
    if not blobs:
        return
    _blobs_dir(cid).mkdir(parents=True, exist_ok=True)
    for nid, blob in blobs.items():
        _check_id(nid, "node")
        f = _blob_file(cid, nid)
        if f.exists():
            continue
        write_json(f, blob)


# ── panel-layout history ─────────────────────────────────────────────────────
# A layout is a few hundred bytes; keeping the last N turns a layout accident
# into a lookup. It also answers "what models did this workspace use last week?".
#
# Append-only `<cid>.layouts.jsonl`, one `{ts, panels}` per CHANGE (not per save
# — most saves are tree writes that leave the layout alone). Best-effort: a
# failure here must never fail the save that carries the user's actual data.
_LAYOUT_HISTORY_MAX = 50


def _layout_models(panels: Any) -> set[tuple[Any, Any]]:
    """The (run_id, checkpoint) set a layout points at — panel ids excluded, so a
    reorder or a rename isn't mistaken for a different model set."""
    if not isinstance(panels, list):
        return set()
    return {(p.get("run_id"), p.get("checkpoint")) for p in panels if isinstance(p, dict)}


def _suspicious_layout_change(prev: Any, new: Any) -> bool:
    """A layout replacement no human action produces: every panel's model swapped
    at once, both sides non-trivial. One panel changing, adding/removing panels, or
    filling in a blank layout are all normal and stay quiet."""
    if not isinstance(prev, list) or not isinstance(new, list):
        return False
    if len(prev) < 2 or len(new) < 2:
        return False
    old_models = {m for m in _layout_models(prev) if m[0]}  # ignore un-set panels
    new_models = {m for m in _layout_models(new) if m[0]}
    if len(old_models) < 2 or len(new_models) < 2:
        return False
    return not (old_models & new_models)


def _record_layout(cid: str, prev: Any, new: Any) -> None:
    """Append `new` to the workspace's layout history if it differs from `prev`."""
    if prev == new:
        return
    if _suspicious_layout_change(prev, new):
        log.warning(
            "workspace %s: panel layout fully replaced (%d panels -> %d, no model in common). "
            "If this was not a deliberate re-pick, restore from %s",
            cid, len(prev), len(new), _layouts_file(cid).name,
        )
    try:
        f = _layouts_file(cid)
        line = json.dumps({"ts": _now(), "panels": new}, separators=(",", ":"))
        # First entry ever for this workspace: seed the PRE-change layout too.
        # The history records `new` per change, so without this the layout a
        # workspace held before its first post-feature change is unrecoverable —
        # which on 2026-08-06 was exactly the layout a clobber destroyed.
        if isinstance(prev, list) and not (f.exists() and f.stat().st_size):
            f.write_text(json.dumps({"ts": _now(), "panels": prev}, separators=(",", ":")) + "\n")
        # A crash mid-append leaves a line with no trailing newline; appending
        # straight onto it would GLUE the next entry to the torn one and lose both.
        heal = ""
        if f.exists() and f.stat().st_size:
            with f.open("rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    heal = "\n"
        with f.open("a") as fh:
            fh.write(heal + line + "\n")
        # Amortized trim: rewrite only once the file drifts past twice the cap, so
        # the common append stays a single write.
        lines = f.read_text().splitlines()
        if len(lines) > _LAYOUT_HISTORY_MAX * 2:
            f.write_text("\n".join(lines[-_LAYOUT_HISTORY_MAX:]) + "\n")
    except (OSError, TypeError, ValueError) as e:
        # A safety net must never be the reason a save carrying the user's actual
        # data fails — swallow disk errors AND an unserializable panel list alike.
        log.warning("workspace %s: could not record layout history: %s", cid, e)


# ── trash journal ────────────────────────────────────────────────────────────
# The durable half of undo. The browser's own stack (web/src/lib/undo.ts) covers
# the hot case — Ctrl+Z seconds after the click, inside the 400 ms save debounce,
# where the server has not seen the deletion at all — and dies with the tab. This
# covers everything after that: another session, another tab, a browser crash,
# and the cross-tab last-write-wins clobber that BRANCHING_DESIGN §6 documents.
#
# It hooks `_persist` deliberately: _persist is the single choke point for EVERY
# workspace write, so the diff also catches `upsert`'s wholesale tree replacement
# and the ops path's legacy {tree, compare_tree} normalization alike (diffing
# before that normalization would mass-journal phantom deletions on a legacy
# workspace's first write).
#
# Append-only `<cid>.trash.jsonl`. Restore is exact — light nodes verbatim, blobs
# still on disk under the same ids (write-once), sibling INDEX recorded so a
# restore can't silently reorder a ‹k/N› cycler.
_TRASH_MAX_AGE_DAYS = 90
_TRASH_MAX_BYTES = 32 * 1024 * 1024
# Above this many nodes vanishing in ONE save, say so loudly: the legitimate ops
# are big (reset thread, discard 30 samples) but so is a truncated-write bug, and
# only the log distinguishes them after the fact.
_TRASH_LOUD_AT = 200


def _trash_file(cid: str) -> Path:
    return _ws_dir() / f"{cid}.trash.jsonl"


def _norm_trees(body: Any) -> dict[str, Any]:
    """A body's panel→tree map, with the legacy {tree, compare_tree} shape mapped
    onto its reserved panel ids — so both sides of a diff speak one language."""
    if not isinstance(body, dict):
        return {}
    trees = body.get("trees")
    if isinstance(trees, dict) and trees:
        return trees
    out: dict[str, Any] = {}
    if body.get("tree"):
        out["primary"] = body["tree"]
    if body.get("compare_tree"):
        out["compare"] = body["compare_tree"]
    return out


def _nodes_of(tree: Any) -> dict[str, Any]:
    nodes = tree.get("nodes") if isinstance(tree, dict) else None
    return nodes if isinstance(nodes, dict) else {}


def _preview(node: Any) -> str:
    text = node.get("content") if isinstance(node, dict) else ""
    return " ".join(str(text or "").split())[:120]


def _vanished(prev_tree: Any, new_tree: Any) -> dict[str, Any]:
    """Nodes present before and absent after. Presence by ID ONLY — node BODIES
    legitimately differ between writers (an op ships heavy fields inline; the
    stored light node carries `has_*` flags), so a content diff would journal
    noise."""
    new_ids = set(_nodes_of(new_tree))
    return {nid: n for nid, n in _nodes_of(prev_tree).items() if nid not in new_ids}


def _trash_entry(
    panel: str, kind: str, prev_tree: Any, gone: dict[str, Any],
    layout: Any = None, layout_index: int | None = None,
) -> dict:
    """One journal entry: the vanished light nodes + where their subtree roots
    hung, so a restore is a splice rather than an append.

    `layout` is the panel's layout row (id/run_id/checkpoint) and is recorded only
    for a whole-column entry — a restore has to RE-ADD the column, and a panel with
    no layout row is a tree the browser never renders. Absent on entries journaled
    before this was recorded; restore falls back to a bare, unbound panel.

    `layout_index` is that row's POSITION, for the same reason each subtree root
    records its sibling index: appending instead would silently reorder the columns,
    and panel order is load-bearing — it is the display order, and `tinkpg samples`
    with no --panel reads the first non-folded panel in exactly this order."""
    prev_nodes = _nodes_of(prev_tree)
    roots = []
    for nid, node in gone.items():
        parent = node.get("parent") if isinstance(node, dict) else None
        if parent in gone:
            continue  # interior of a deleted subtree — its root carries the anchor
        siblings = (
            _nodes_of(prev_tree).get(parent, {}).get("children")
            if parent
            else prev_tree.get("rootChildren")
        )
        index = siblings.index(nid) if isinstance(siblings, list) and nid in siblings else -1
        roots.append({
            "id": nid,
            "parent": parent,
            "index": index,
            "role": node.get("role") if isinstance(node, dict) else None,
            "preview": _preview(node),
        })
    # Selections INSIDE the deleted subtree — restoring them puts the subtree's own
    # cyclers back. Selections outside it are the live view's business, not ours.
    selected = prev_tree.get("selected") if isinstance(prev_tree, dict) else None
    inner = {k: v for k, v in (selected or {}).items() if k in gone} if isinstance(selected, dict) else {}
    return {
        "id": uuid.uuid4().hex[:8],
        "ts": _now(),
        "panel": panel,
        "kind": kind,
        "count": len(gone),
        "roots": sorted(roots, key=lambda r: r["index"]),
        "selected": inner,
        "nodes": {nid: gone[nid] for nid in gone if nid in prev_nodes},
        **({"layout": layout} if layout is not None else {}),
        **({"layout_index": layout_index} if layout_index is not None else {}),
    }


def _append_trash(cid: str, entries: list[dict]) -> None:
    """Append + amortized retention. Best-effort: a safety net must never be the
    reason the save carrying the user's actual data fails."""
    if not entries:
        return
    try:
        f = _trash_file(cid)
        heal = ""
        if f.exists() and f.stat().st_size:
            with f.open("rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    heal = "\n"  # a torn last line must not glue onto the next entry
        with f.open("a") as fh:
            fh.write(heal + "\n".join(json.dumps(e, separators=(",", ":")) for e in entries) + "\n")
        if f.stat().st_size > _TRASH_MAX_BYTES * 2:
            _trim_trash(cid)
    except (OSError, TypeError, ValueError) as e:
        log.warning("workspace %s: could not record trash: %s", cid, e)


def _trim_trash(cid: str) -> None:
    """Drop entries past the age cap, then oldest-first until under the byte cap.
    Bytes, not entry count: one discarded 30-sample thinking fan is ~1 MB, so a
    count cap would either hoard gigabytes or evict a single big deletion."""
    kept: list[str] = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_TRASH_MAX_AGE_DAYS)).isoformat()
    for line in _read_trash_lines(cid):
        entry = line[1]
        if str(entry.get("ts") or "") >= cutoff:
            kept.append(line[0])
    total = sum(len(s) + 1 for s in kept)
    while kept and total > _TRASH_MAX_BYTES:
        total -= len(kept.pop(0)) + 1
    _trash_file(cid).write_text(("\n".join(kept) + "\n") if kept else "")


def _read_trash_lines(cid: str) -> list[tuple[str, dict]]:
    """(raw line, parsed entry) pairs, oldest first. Torn/garbage lines are skipped
    rather than raising — one bad append must not hide every other recoverable node."""
    f = _trash_file(cid)
    out: list[tuple[str, dict]] = []
    if not _is_safe_id(cid) or not f.exists():
        return out
    try:
        for raw in f.read_text().splitlines():
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                out.append((raw, entry))
    except OSError:
        return []
    return out


def _record_trash(cid: str, prev: Any, new: dict) -> None:
    """Journal every node that this write makes disappear."""
    prev_trees = _norm_trees(prev)
    if not prev_trees:
        return
    new_trees = _norm_trees(new)
    prev_rows = [p for p in (prev.get("panels") or []) if isinstance(p, dict) and p.get("id")]
    prev_layout = {p["id"]: (i, p) for i, p in enumerate(prev_rows)}
    entries: list[dict] = []
    for panel, prev_tree in prev_trees.items():
        layout, layout_index = None, None
        if panel not in new_trees:
            gone = _nodes_of(prev_tree)
            kind = "panel"  # the whole column went away (removePanel; folding keeps its tree)
            layout_index, layout = prev_layout.get(panel, (None, None))
        else:
            gone = _vanished(prev_tree, new_trees[panel])
            kind = "nodes"
        if gone:
            entries.append(_trash_entry(panel, kind, prev_tree, gone, layout, layout_index))
    total = sum(e["count"] for e in entries)
    if total >= _TRASH_LOUD_AT:
        log.warning(
            "workspace %s: %d nodes vanished in one save (panels %s). If that was not "
            "deliberate, restore from %s",
            cid, total, [e["panel"] for e in entries], _trash_file(cid).name,
        )
    _append_trash(cid, entries)


def list_trash(cid: str) -> list[dict]:
    """`GET /{id}/trash` — journal entries newest first, WITHOUT their node bodies
    (a listing is for choosing; the bodies can be megabytes)."""
    out = [{k: v for k, v in e.items() if k != "nodes"} for _, e in _read_trash_lines(cid)]
    out.reverse()
    return out


def _find_entry(cid: str, handle: str) -> dict | None:
    """Resolve a restore handle: an entry id, a subtree-root node id, or any node
    id in an entry — you rarely know which of those you're holding. Newest wins."""
    entries = [e for _, e in _read_trash_lines(cid)]
    for entry in reversed(entries):
        if entry.get("id") == handle:
            return entry
    for entry in reversed(entries):
        if any(r.get("id") == handle for r in entry.get("roots") or []):
            return entry
    for entry in reversed(entries):
        if handle in (entry.get("nodes") or {}):
            return entry
    return None


def restore_trash(cid: str, handle: str) -> dict:
    """`POST /{id}/trash/restore` — splice a journaled subtree back in.

    Returns {ok, restored, panel, recreated_panel, unbound_panel, entry} or
    {ok: False, error}. `recreated_panel` = this restore re-added a whole column
    (tree and/or its layout row, checked separately — they drift); `unbound_panel` =
    the re-added row carries no model, so the browser will drop it as a phantom.
    Idempotent-ish: a
    node already present is left alone, so a double restore is a no-op rather than
    a duplicate. Blobs need no work — they were never deleted (write-once)."""
    with locked("workspaces"):
        _ensure_loaded()
        conv = _load_body(cid)
        if conv is None:
            return {"ok": False, "error": "unknown workspace"}
        entry = _find_entry(cid, handle)
        if entry is None:
            return {"ok": False, "error": f"nothing in the trash matches {handle!r}"}
        panel = entry.get("panel")
        trees = dict(_norm_trees(conv))
        # A whole-column delete removed the panel from BOTH trees and the layout, so
        # restoring one has to re-add the column — otherwise the only route back was
        # "re-add a panel that happens to mint the same id by hand", which stopped
        # being reachable at all once panel ids became monotonic.
        #
        # The two halves are checked SEPARATELY because they can drift apart: a stale
        # tab's next save ships its whole (pre-restore) `panels` list and wipes the
        # row, while the partial tree upsert leaves the tree standing — and losing a
        # row is not journaled (_record_trash diffs trees only). Gating on the tree
        # alone made that state terminal: nodes already present ⇒ nothing to restore
        # ⇒ row never re-added ⇒ a stored tree no layout renders, with the manual
        # escape hatch gone. Keyed on the row too, restore stays re-runnable.
        layout_rows = [r for r in (conv.get("panels") or []) if isinstance(r, dict)]
        needs_tree = panel not in trees
        needs_row = not any(r.get("id") == panel for r in layout_rows)
        recreated_panel = needs_tree or needs_row
        if needs_tree:
            trees[panel] = {"nodes": {}, "rootChildren": [], "selected": {}}
        tree = json.loads(json.dumps(trees[panel]))  # deep copy: never mutate the cached body
        nodes = tree.setdefault("nodes", {})
        restored = [nid for nid in entry.get("nodes") or {} if nid not in nodes]
        for nid in restored:
            nodes[nid] = entry["nodes"][nid]
        for root in entry.get("roots") or []:
            nid, parent = root.get("id"), root.get("parent")
            if nid not in nodes:
                continue
            if parent is None:
                siblings = tree.setdefault("rootChildren", [])
            elif parent in nodes:
                siblings = nodes[parent].setdefault("children", [])
            else:
                continue  # the anchor itself was deleted later — leave it detached
            if nid in siblings:
                continue
            idx = root.get("index")
            siblings.insert(idx if isinstance(idx, int) and 0 <= idx <= len(siblings) else len(siblings), nid)
        if entry.get("selected"):
            tree.setdefault("selected", {}).update(
                {k: v for k, v in entry["selected"].items() if k in nodes})
        trees[panel] = tree
        conv = dict(conv)
        conv["trees"] = trees
        conv.pop("tree", None)
        conv.pop("compare_tree", None)
        unbound = False
        if needs_row:
            # The journaled layout row carries the model the column was bound to;
            # entries predating that fall back to an unbound panel the human re-binds.
            layout = entry.get("layout")
            row = dict(layout) if isinstance(layout, dict) else {"id": panel, "run_id": None, "checkpoint": None}
            row["id"] = panel
            # What matters to the caller is whether the row that just landed BINDS a
            # model, not which of the two branches above produced it: a journaled row
            # can itself carry run_id=None (a panel that got its tree from
            # send-branch-to-panel and was closed before a model was picked), and the
            # browser's phantom filter drops that one exactly like a fabricated one.
            unbound = row.get("run_id") is None
            rows = [*(conv.get("panels") or [])]
            # Back at its old POSITION, like a subtree root goes back at its sibling
            # index. Appending would reorder the columns on screen and move which
            # panel `tinkpg samples` picks by default. Absent / out-of-range (an old
            # entry, or a layout that shrank since) ⇒ append, which is still valid.
            at = entry.get("layout_index")
            rows.insert(at if isinstance(at, int) and not isinstance(at, bool)
                        and 0 <= at <= len(rows) else len(rows), row)
            conv["panels"] = rows
        conv["updated_at"] = _now()
        _persist(conv)
    return {"ok": True, "restored": restored, "panel": panel,
            "recreated_panel": recreated_panel,
            # A column restored with no model bound, and the browser's phantom-panel
            # filter drops run_id==null panels on load — so it would vanish before the
            # human saw it. Surfaced so the CLI can say so rather than promise a
            # re-bind that never gets the chance.
            "unbound_panel": unbound,
            "entry": {k: v for k, v in entry.items() if k != "nodes"}}


def purge_trash(cid: str) -> bool:
    """`DELETE /{id}/trash` — forget a workspace's journal. The answer to "I pasted
    a secret and want it gone", which write-once blobs already make hard."""
    if not _is_safe_id(cid):
        return False
    _trash_file(cid).unlink(missing_ok=True)
    return True


def _persist(light: dict) -> None:
    """Write one light workspace file + refresh both caches for it. The file write
    is atomic (tmp+rename); the cache refresh is under _CACHE_LOCK.

    The single choke point for every workspace write, which is why the layout
    history hooks in here rather than in each of the three mutation entry points."""
    cid = _check_id(light.get("id"), "workspace")
    with _CACHE_LOCK:
        prev = _bodies.get(cid)
    prev_panels = prev.get("panels") if isinstance(prev, dict) else None
    # Per-workspace monotonic revision (HANDOFF_SERVER_AUTHORITY §4.2). Bumped
    # HERE for the same reason the trash journal diffs here: _persist is the one
    # choke point EVERY write channel goes through — /ops, PATCH, create,
    # pack apply, trash restore — so no path can move a workspace without the
    # attached mirrors being told. max() of both sides keeps it monotone whichever
    # is fresher: the caller's body wins on a cold cache (post-restart write to a
    # stored workspace), the cache wins if a caller rebuilt a body without it.
    light["rev"] = max(_rev_of(prev), _rev_of(light)) + 1
    _record_trash(cid, prev, light)
    write_json(_ws_file(cid), light)
    with _CACHE_LOCK:
        assert _summaries is not None
        _bodies[cid] = light
        _summaries[cid] = _summary_of(light)
    _record_layout(cid, prev_panels, light.get("panels"))


# ── public reads ─────────────────────────────────────────────────────────────
def list_summaries() -> list[dict]:
    """`GET /api/workspaces` — {id,name,created_at,updated_at,panels,rev}, no trees.

    Returns refs to the cached summary dicts, which are replaced wholesale (never
    mutated in place) on write, so a caller holding one is unaffected by later saves."""
    _ensure_loaded()
    with _CACHE_LOCK:
        assert _summaries is not None
        cids = sorted(_summaries, key=lambda c: (_summaries[c].get("created_at") or "", c))
        return [_summaries[c] for c in cids]


def list_bodies() -> list[dict]:
    """`GET /api/workspaces?bodies=1` — light bodies (trees incl., blobs excl.)."""
    _ensure_loaded()
    return [_with_rev(b) for c in _snapshot_ordered_cids() if (b := _load_body(c)) is not None]


def get_body(cid: str) -> dict | None:
    """`GET /api/workspaces/{id}` — one light body (carrying `rev`), or None (404)."""
    _ensure_loaded()
    return _with_rev(_load_body(cid))


def get_blobs(cid: str, node_ids: list[str]) -> dict[str, dict]:
    """`POST /api/workspaces/{id}/node-blobs` — {node_id: blob} for known ids.
    Unknown / unsafe / unreadable ids are omitted (never an error)."""
    out: dict[str, dict] = {}
    if not _is_safe_id(cid) or not _blobs_dir(cid).exists():
        return out
    for nid in node_ids:
        if not _is_safe_id(nid):
            continue
        f = _blob_file(cid, nid)
        if f.exists():
            try:
                out[nid] = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
    return out


# ── public mutations (each self-locks; helpers above never lock) ─────────────
def upsert(
    *,
    id: str | None,
    name: str,
    system_prompt: str | None,
    system_enabled: bool | None,
    trees: dict[str, Any],
    panels: list[dict],
    reduced_panels: list[str],
    send_targets: list[str],
    seen_panels: list[str],
    panel_seq: int = 0,
) -> dict:
    """Create (or upsert by client-supplied id) a workspace. Returns the LIGHT
    body (trees included, blobs excluded) — same top-level shape as v1 create."""
    with locked("workspaces"):
        _ensure_loaded()
        cid = _check_id(id or str(uuid.uuid4()), "workspace")
        now = _now()
        entry = {
            "id": cid,
            "name": name,
            "system_prompt": system_prompt,
            "system_enabled": system_enabled,
            "trees": trees,
            # Phantom-panel heal on every layout SET (see tree_ops.normalize_panels):
            # the browser used to do this on load, and with the layout
            # server-authoritative a stored phantom would otherwise resurrect.
            "panels": tree_ops.normalize_panels(panels, trees),
            "reduced_panels": reduced_panels,
            "send_targets": send_targets,
            "seen_panels": seen_panels,
            "panel_seq": panel_seq,
            "created_at": now,
            "updated_at": now,
        }
        existing = _load_body(cid)
        if existing is not None:  # upsert: keep original created_at
            entry["created_at"] = existing.get("created_at", now)
            # `entry` is rebuilt from kwargs, so without this the stored rev is
            # dropped and _persist restarts the counter at 1 — a mirror would see
            # its workspace jump BACKWARDS (pack apply overwrites live workspaces).
            entry["rev"] = _rev_of(existing)
            # Monotone/union fields survive a writer that doesn't send them.
            entry["panel_seq"] = _merge_panel_seq(existing.get("panel_seq"), panel_seq)
            entry["seen_panels"] = _merge_seen_panels(existing.get("seen_panels"), seen_panels)
        light, blobs = split_workspace(entry)
        _write_blobs(cid, blobs)
        _persist(light)
    return light


# The meta-merge rules live with the op vocabulary they implement (`set_meta`),
# so the create path and the op path can't drift apart. Aliased because these
# names are the ones the save paths have always used.
_merge_panel_seq = tree_ops.merge_panel_seq
_merge_seen_panels = tree_ops.merge_seen_panels


def set_meta(cid: str, fields: dict[str, Any]) -> dict | None:
    """`set_meta` — the workspace's metadata write, shared by the `PATCH /{id}`
    sugar and the `set_meta` op, so both take the same locked apply, the same rev
    bump and the same broadcast. Returns `{summary, rev, ops}` (`ops` is the wire
    batch to broadcast — empty when nothing changed), or None if unknown (404).

    Field-wise last-writer-wins, except `panel_seq` (monotone max) and
    `seen_panels` (union) — key-presence gating already stops a writer that has
    never heard of a field from dropping it, but it does NOT stop one holding a
    STALER value, and two tabs on one workspace both send the fields from the
    snapshot they loaded. `panels` is normalized (the phantom-panel heal, now
    server-side — see tree_ops.normalize_panels)."""
    with locked("workspaces"):
        _ensure_loaded()
        conv = _load_body(cid)
        if conv is None:
            return None
        conv = dict(conv)
        applied, changed = tree_ops.apply_meta(conv, fields)
        if not changed:
            return {"summary": _summary_of(conv), "rev": _rev_of(conv), "ops": []}
        conv["updated_at"] = _now()
        _persist(conv)
        return {
            "summary": _summary_of(conv),
            "rev": _rev_of(conv),
            "ops": [{"op": "set_meta", "fields": applied}],
        }


def patch_meta(cid: str, fields: dict[str, Any]) -> dict | None:
    """`set_meta` returning only the summary — the pre-ops signature, kept for
    callers that don't broadcast (scripts, tests/small-smokes/store_concurrency)."""
    out = set_meta(cid, fields)
    return None if out is None else out["summary"]


def apply_ops(cid: str, ops: Any) -> dict | None:
    """`POST /{id}/ops` — apply an op batch ATOMICALLY under the workspaces flock.

    All-or-nothing: any rejection discards the whole batch and returns
    `{"rejected": {index, error}}` with nothing written and `rev` unmoved. On
    success returns `{rev, results, ops}` — `ops` being the batch AS APPLIED
    (light nodes, merged meta values), for the caller to broadcast.

    A batch that changes nothing (an idempotent replay — the retry-safety path)
    writes nothing, broadcasts nothing and keeps `rev`: replaying is free, and a
    bumped rev with no content would make every mirror re-render for nothing.
    Returns None if the workspace is unknown (404)."""
    with locked("workspaces"):
        _ensure_loaded()
        conv = _load_body(cid)
        if conv is None:
            return None
        try:
            res = tree_ops.apply_ops(conv, ops)
        except tree_ops.OpError as e:
            return {"rejected": {"index": e.index, "error": e.error}}
        # BEFORE the no-change bail-out: a replay whose nodes all already exist
        # changes no tree bytes but may be the only carrier of their heavy data
        # (a light-node create can leave `has_*` flags with no blob behind them).
        # Write-once means this is free whenever the blobs are already there, and
        # it makes a retry the repair path for that dangling-flag state.
        _write_blobs(cid, res.blobs)
        if not res.changed:
            return {"rev": _rev_of(conv), "results": res.results, "ops": []}
        body = res.body
        body["updated_at"] = _now()
        _persist(body)
        return {"rev": _rev_of(body), "results": res.results, "ops": res.wire_ops}


def register_chat_placement(cid: str, panel: str, parent_node: str) -> str | None:
    """Validate-and-register a chat's fold placement (HANDOFF_SERVER_AUTHORITY
    §4.3), atomically under the workspaces flock — the same lock every op apply
    holds, so between this and the chat's terminal release a `delete` op pruning
    `parent_node`'s subtree is REJECTED (`tree_ops._guard_inflight_delete`)
    instead of silently orphaning the fold. Returns an error string with nothing
    registered, or None on success (the caller MUST `inflight.release` at the
    chat's terminal).

    The parent must be a USER node: folds hang assistant siblings under the user
    turn they answer (same contract as `foldAssistant`), and under §4.3 that node
    was persisted by the writer's own op BEFORE the fire — "not found" here means
    the op was never emitted, or a delete won the tiny op-to-fire race."""
    with locked("workspaces"):
        _ensure_loaded()
        conv = _load_body(cid)
        if conv is None:
            return f"unknown workspace {cid!r}"
        tree = tree_ops.as_tree(tree_ops.trees_of(conv).get(panel))
        node = tree.get("nodes", {}).get(parent_node)
        if not isinstance(node, dict):
            return (
                f"parent node {parent_node!r} not found in panel {panel!r} of workspace "
                f"{cid!r} — the user turn must be persisted (add_nodes op) before the fire"
            )
        if node.get("role") != "user":
            return (
                f"parent node {parent_node!r} is a {node.get('role')!r} turn — "
                "samples fold under USER nodes"
            )
        inflight.register(cid, panel, parent_node)
        return None


def layout_history(cid: str) -> list[dict]:
    """Oldest-first `{ts, panels}` entries for a workspace ([] if none / unreadable).
    Read side of the safety net — consumed by `scripts/layout_history.py`."""
    if not _is_safe_id(cid):
        return []
    f = _layouts_file(cid)
    if not f.exists():
        return []
    out: list[dict] = []
    try:
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn last line (crash mid-append) must not hide the rest
            if isinstance(entry, dict):
                out.append(entry)
    except OSError:
        return []
    return out


def _deleted_dir() -> Path:
    # Leading dot: the `*.json` summary glob doesn't descend, so a set-aside
    # workspace can never come back as a live one.
    return _ws_dir() / ".deleted"


def _prune_deleted() -> None:
    """Age out set-aside workspaces. Cheap (a handful of stat calls) and only runs
    on a delete, which is rare."""
    root = _deleted_dir()
    if not root.exists():
        return
    cutoff = time.time() - _TRASH_MAX_AGE_DAYS * 86400
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            continue


def delete(cid: str) -> bool:
    """DELETE /{id} — retire a workspace: its light file, blobs, layout history and
    trash journal are MOVED to `workspaces/.deleted/<cid>-<ts>/`, not unlinked.
    False if unknown.

    Soft on purpose. This is the single most destructive click in the app, and it
    is the one deletion the trash journal cannot cover — the journal lives inside
    the workspace, so an rmtree took the evidence with it. Blobs are write-once and
    never rewritten, so a set-aside directory is a complete workspace: moving it
    back restores everything, logprobs included. Aged out after
    `_TRASH_MAX_AGE_DAYS`."""
    if not _is_safe_id(cid):  # never move/rmtree a path built from a crafted id
        return False
    with locked("workspaces"):
        _ensure_loaded()
        with _CACHE_LOCK:
            assert _summaries is not None
            known = cid in _summaries
        if not known and not _ws_file(cid).exists():
            return False
        grave = _deleted_dir() / f"{cid}-{int(time.time())}"
        try:
            grave.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("workspace %s: could not set aside on delete: %s", cid, e)
            grave = None  # fall through to a hard delete rather than refusing to delete
        with _CACHE_LOCK:
            # Move the light file + drop the caches atomically vs _load_body's
            # exists-check-then-cache, so a concurrent GET can't re-cache a ghost body.
            if grave and _ws_file(cid).exists():
                _ws_file(cid).rename(grave / _ws_file(cid).name)
            else:
                _ws_file(cid).unlink(missing_ok=True)
            _bodies.pop(cid, None)
            _summaries.pop(cid, None)
        for path in (_blobs_dir(cid), _layouts_file(cid), _trash_file(cid)):
            if not path.exists():
                continue
            if grave:
                path.rename(grave / path.name)
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        _prune_deleted()
    return True


# ── boot: migration + cache build ────────────────────────────────────────────
def _progress(msg: str) -> None:
    """Boot-migration progress — printed to STDERR (flushed) so it's visible during
    the one boot that matters. uvicorn configures only its own loggers, so a plain
    log.info here is dropped at default config; a silent multi-second migration would
    look like a hung start and invite a Ctrl-C. Also logged, for structured sinks."""
    print(msg, file=sys.stderr, flush=True)
    log.info(msg)


def _migrate_dir_locked() -> None:
    """v1.0.0 rename: `<state>/conversations/` → `<state>/workspaces/`.

    The saved container has been a WORKSPACE (panels + their branch trees) in the
    UI, CLI and docs since 2026-07-17; v1.0.0 finishes the job on the wire and on
    disk. File CONTENTS are untouched — no field of a stored body contains the old
    word — so this is a directory rename and nothing else.

    Runs before the storage-v2 migration below: that one keys off `workspaces/`
    existing, so an already-v2 instance whose data still sits in `conversations/`
    has to be moved first or it would look like a fresh install.

    Rollback is the inverse rename (see docs/MIGRATIONS.md); the rename is atomic
    and instant, which is why this doesn't copy — the stores run to hundreds of MB
    and a copy on the slow volume would be a multi-minute boot."""
    old = _state_dir() / "conversations"
    new = _ws_dir()
    if new.exists() or not old.exists():
        return
    old.rename(new)
    _progress(f"v1.0.0 migration: {old.name}/ → {new.name}/ (directory rename; contents untouched)")


def _migrate_locked() -> None:
    """Migrate legacy `conversations.json` → per-conversation files + blob dirs.

    Runs iff the legacy file exists and `workspaces/` does NOT. STRONG verify:
    every workspace is split AND re-materialized (blobs folded back) and deep-
    compared against the legacy object in memory; ANY mismatch raises (refuse to
    start) with the legacy file untouched. Only after all pass do we write into a
    staging dir, atomically swap it into place, then rename legacy → `.legacy`
    (never deleted)."""
    legacy = _legacy_path()
    convs_dir = _ws_dir()
    legacy_done = legacy.with_suffix(legacy.suffix + ".legacy")
    if convs_dir.exists():
        # Already migrated (the normal case). But a crash BETWEEN the atomic dir swap
        # and the legacy rename can leave conversations.json un-renamed forever (this
        # guard would skip it every subsequent boot); finish that rename now so a later
        # deletion of workspaces/ can't silently re-migrate resurrected stale state.
        if legacy.exists() and not legacy_done.exists():
            legacy.rename(legacy_done)
            _progress(f"storage-v2: completed an interrupted migration (legacy → {legacy_done.name})")
        return
    if not legacy.exists():
        return
    try:
        items = json.loads(legacy.read_text())
    except json.JSONDecodeError:
        # A legacy file too corrupt to parse can't be migrated or verified; move it
        # aside and start fresh (mirrors v1's corrupt-file handling).
        _progress("storage-v2: legacy conversations.json is unparseable — moving aside, starting empty")
        _quarantine(legacy)
        return
    if not isinstance(items, list):
        raise RuntimeError("legacy conversations.json is not a JSON list — refusing to migrate")

    _progress(f"storage-v2 migration: verifying {len(items)} workspace(s)…")
    staged: list[tuple[dict, dict[str, dict]]] = []
    for conv in items:
        if not isinstance(conv, dict):
            raise RuntimeError(f"legacy entry is not an object: {conv!r} — refusing to migrate")
        light, blobs = split_workspace(conv)
        if materialize_workspace(light, blobs) != conv:
            raise RuntimeError(
                f"storage-v2 migration verify FAILED for workspace "
                f"{conv.get('id')!r}: re-materialized body != legacy. Refusing to "
                f"start; legacy file left untouched."
            )
        staged.append((light, blobs))

    staging = _state_dir() / "workspaces.migrating"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    n_blobs = 0
    for light, blobs in staged:
        cid = _check_id(light.get("id"), "workspace")
        write_json(staging / f"{cid}.json", light)
        if blobs:
            bdir = staging / f"{cid}.blobs"
            bdir.mkdir(parents=True, exist_ok=True)
            for nid, blob in blobs.items():
                write_json(bdir / f"{_check_id(nid, 'node')}.json", blob)
                n_blobs += 1
    os.replace(staging, convs_dir)  # atomic dir swap (same filesystem)
    legacy.rename(legacy_done)
    _progress(
        f"storage-v2 migration complete: {len(staged)} workspace(s), "
        f"{n_blobs} blob(s); legacy → {legacy_done.name}"
    )


def boot() -> None:
    """Called once at app startup (main.lifespan). Migrates if needed (may RAISE to
    refuse start), then rebuilds the summary cache from disk."""
    with locked("workspaces"):
        _migrate_dir_locked()   # v1.0.0: conversations/ → workspaces/ (must come first)
        _migrate_locked()       # storage v2: legacy conversations.json → per-workspace files
    reset_cache()
    _ensure_loaded()
