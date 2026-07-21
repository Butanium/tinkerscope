"""Storage v2 for saved conversation TREES — per-conversation files + node blobs.

WHY (measured): the v1 single-`conversations.json` design put every conversation
WITH full trees in one file. One real workspace hit 380 MB, and 89.8% of a heavy
conversation's bytes were `token_logprobs` (raw_meta another 6.9%). Loading /
re-saving that whole file — and shipping every tree to the browser on page load —
OOMed the tab. See `docs/STORAGE_V2.md` for the byte breakdown.

WHAT: a node's two heavy fields — **`token_logprobs` and `raw_meta`** — move OUT
of the tree into per-node **write-once blobs**. Everything else (content,
raw_text, prefill, finish_reason, reasoning, thinking, parent/children) stays in
the light tree; light nodes carry `has_token_logprobs` / `has_raw_meta` presence
flags so the UI can gate affordances without the payload.

ON-DISK LAYOUT (per instance/state dir):

    <state>/conversations/<cid>.json           # light conversation (light trees)
    <state>/conversations/<cid>.blobs/<nid>.json   # {"token_logprobs":[...]?, "raw_meta":"..."?}
    <state>/conversations.json.legacy          # pre-v2 file, renamed after migration

Blob invariant: **write-once**. Logprobs/raw_meta never change after a node is
created (edits/regens mint new nodes), so a blob that already exists on disk is
never rewritten (idempotent retries are free), and blobs are deleted only when
their whole conversation is deleted.

Blobs are keyed by node id, flat within one conversation's `.blobs/` dir. Node ids
are globally unique within a conversation (one client-side counter mints them),
and add-model's `duplicateTo` CLONES a panel's tree keeping the SAME ids — so two
panels can share a node id, and the shared blob is written once (identical data).

CACHING: an in-memory `_summaries` map (id → {id,name,created_at,updated_at,panels})
is built once at boot and maintained on every write — `GET /api/conversations`
never re-parses the store. Parsed light bodies are memoized in `_bodies`, evicted
on write. Every mutation is wrapped in `store.locked("conversations")` (the flock
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import locked, write_json

log = logging.getLogger("tinkerscope.conversation_store")

# Heavy per-node fields that live in blobs, not the light tree.
BLOB_FIELDS = ("token_logprobs", "raw_meta")
# light-node presence flag for each heavy field.
_FLAG = {"token_logprobs": "has_token_logprobs", "raw_meta": "has_raw_meta"}
_FLAGS = tuple(_FLAG.values())

# Conversation / node ids become path components — confine them to safe chars so a
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
    """Public: is this a filename-safe conversation id? The create route uses it to
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

    return SETTINGS.conversations_path  # <state_dir>/conversations.json


def _convs_dir() -> Path:
    return _state_dir() / "conversations"


def _conv_file(cid: str) -> Path:
    return _convs_dir() / f"{cid}.json"


def _blobs_dir(cid: str) -> Path:
    return _convs_dir() / f"{cid}.blobs"


def _blob_file(cid: str, nid: str) -> Path:
    return _blobs_dir(cid) / f"{nid}.json"


# ── node/tree/conversation split + re-materialize (PURE — never mutate input) ────
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


# Tree-bearing conversation keys. `trees` is the v2 {panel_id: tree} map; `tree` /
# `compare_tree` are the pre-multipanel single trees (2 of Clément's 16 real
# conversations still carry that shape — migration must split blobs out of them too,
# and preserve their key presence EXACTLY for the round-trip verify).
_TREES_MAP_KEY = "trees"
_SINGLE_TREE_KEYS = ("tree", "compare_tree")


def split_conv(conv: dict) -> tuple[dict, dict[str, dict]]:
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


def materialize_conv(light: dict, blobs: dict[str, dict]) -> dict:
    """Inverse of split_conv (migration verification only)."""
    conv: dict = {}
    for k, v in light.items():
        if k == _TREES_MAP_KEY:
            conv[k] = {pid: _materialize_tree(t, blobs) for pid, t in v.items()} if isinstance(v, dict) else v
        elif k in _SINGLE_TREE_KEYS:
            conv[k] = _materialize_tree(v, blobs)
        else:
            conv[k] = v
    return conv


def _summary_of(light: dict) -> dict:
    return {
        "id": light.get("id"),
        "name": light.get("name"),
        "created_at": light.get("created_at"),
        "updated_at": light.get("updated_at"),
        "panels": light.get("panels") or [],
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
    d = _convs_dir()
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
    """Parsed light body for one conversation (memoized). None if missing/corrupt.
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
    f = _conv_file(cid)
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


def _persist(light: dict) -> None:
    """Write one light conversation file + refresh both caches for it. The file write
    is atomic (tmp+rename); the cache refresh is under _CACHE_LOCK."""
    cid = _check_id(light.get("id"), "conversation")
    write_json(_conv_file(cid), light)
    with _CACHE_LOCK:
        assert _summaries is not None
        _bodies[cid] = light
        _summaries[cid] = _summary_of(light)


# ── public reads ─────────────────────────────────────────────────────────────
def list_summaries() -> list[dict]:
    """`GET /api/conversations` — {id,name,created_at,updated_at,panels}, no trees.

    Returns refs to the cached summary dicts, which are replaced wholesale (never
    mutated in place) on write, so a caller holding one is unaffected by later saves."""
    _ensure_loaded()
    with _CACHE_LOCK:
        assert _summaries is not None
        cids = sorted(_summaries, key=lambda c: (_summaries[c].get("created_at") or "", c))
        return [_summaries[c] for c in cids]


def list_bodies() -> list[dict]:
    """`GET /api/conversations?bodies=1` — light bodies (trees incl., blobs excl.)."""
    _ensure_loaded()
    return [b for c in _snapshot_ordered_cids() if (b := _load_body(c)) is not None]


def get_body(cid: str) -> dict | None:
    """`GET /api/conversations/{id}` — one light body, or None (404)."""
    _ensure_loaded()
    return _load_body(cid)


def get_blobs(cid: str, node_ids: list[str]) -> dict[str, dict]:
    """`POST /api/conversations/{id}/node-blobs` — {node_id: blob} for known ids.
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
) -> dict:
    """Create (or upsert by client-supplied id) a conversation. Returns the LIGHT
    body (trees included, blobs excluded) — same top-level shape as v1 create."""
    with locked("conversations"):
        _ensure_loaded()
        cid = _check_id(id or str(uuid.uuid4()), "conversation")
        now = _now()
        entry = {
            "id": cid,
            "name": name,
            "system_prompt": system_prompt,
            "system_enabled": system_enabled,
            "trees": trees,
            "panels": panels,
            "reduced_panels": reduced_panels,
            "send_targets": send_targets,
            "seen_panels": seen_panels,
            "created_at": now,
            "updated_at": now,
        }
        existing = _load_body(cid)
        if existing is not None:  # upsert: keep original created_at
            entry["created_at"] = existing.get("created_at", now)
        light, blobs = split_conv(entry)
        _write_blobs(cid, blobs)
        _persist(light)
    return light


def save_tree(
    cid: str,
    *,
    trees_partial: dict[str, Any],
    dropped_trees: list[str],
    system_prompt: str | None,
    system_enabled: bool | None,
    panels: list[dict],
    reduced_panels: list[str],
    send_targets: list[str],
    seen_panels: list[str],
) -> bool:
    """PUT /{id}/tree — PARTIAL upsert. `trees_partial` carries only dirty panels
    (merged over the stored trees); `dropped_trees` removes panels. Inline heavy
    fields on fresh-fold nodes are stripped into write-once blobs. Returns False if
    the conversation is unknown (404). Blobs for dropped panels are NOT deleted
    (write-once invariant — cleaned only on conversation delete)."""
    with locked("conversations"):
        _ensure_loaded()
        conv = _load_body(cid)
        if conv is None:
            return False
        light_partial, blobs = split_conv({"trees": trees_partial})
        trees = dict(conv.get("trees") or {})
        # A migrated legacy {tree, compare_tree} conversation has no `trees` yet.
        # Seed the merge base with its reserved-id panels BEFORE the partial upsert so
        # a first save carrying only one dirty panel can't drop the other — then the
        # self-heal pop below is always safe regardless of what the client sends. The
        # frontend maps tree→'primary', compare_tree→'compare' (truthy-checked); we
        # mirror that here so the backend defends the data on its own.
        if not trees:
            if conv.get("tree"):
                trees["primary"] = conv["tree"]
            if conv.get("compare_tree"):
                trees["compare"] = conv["compare_tree"]
        trees.update(light_partial["trees"])
        for pid in dropped_trees or []:
            trees.pop(pid, None)
        conv = dict(conv)
        conv["trees"] = trees
        conv["system_prompt"] = system_prompt
        conv["system_enabled"] = system_enabled
        conv["panels"] = panels
        conv["reduced_panels"] = reduced_panels
        conv["send_targets"] = send_targets
        conv["seen_panels"] = seen_panels
        conv["updated_at"] = _now()
        # self-heal a migrated legacy {tree, compare_tree} entry on its first save
        # (its trees are now folded into `trees` above, so dropping the keys is safe).
        conv.pop("tree", None)
        conv.pop("compare_tree", None)
        _write_blobs(cid, blobs)
        _persist(conv)
    return True


_PATCH_FIELDS = ("name", "system_prompt", "system_enabled", "panels", "reduced_panels", "send_targets", "seen_panels")


def patch_meta(cid: str, fields: dict[str, Any]) -> dict | None:
    """PATCH /{id} — layout-only metadata (name/system_prompt/panels/reduced_panels/
    send_targets/seen_panels), no tree bytes. Returns the updated summary, or None
    (404). Only keys present in `fields` are applied."""
    with locked("conversations"):
        _ensure_loaded()
        conv = _load_body(cid)
        if conv is None:
            return None
        conv = dict(conv)
        for k in _PATCH_FIELDS:
            if k in fields:
                conv[k] = fields[k]
        conv["updated_at"] = _now()
        _persist(conv)
        return _summary_of(conv)


def delete(cid: str) -> bool:
    """DELETE /{id} — remove the light file AND the blobs dir. False if unknown."""
    if not _is_safe_id(cid):  # never unlink/rmtree a path built from a crafted id
        return False
    with locked("conversations"):
        _ensure_loaded()
        with _CACHE_LOCK:
            assert _summaries is not None
            known = cid in _summaries
        if not known and not _conv_file(cid).exists():
            return False
        with _CACHE_LOCK:
            # Unlink the light file + drop the caches atomically vs _load_body's
            # exists-check-then-cache, so a concurrent GET can't re-cache a ghost body.
            _conv_file(cid).unlink(missing_ok=True)
            _bodies.pop(cid, None)
            _summaries.pop(cid, None)
        shutil.rmtree(_blobs_dir(cid), ignore_errors=True)  # blobs: no cache impact
    return True


# ── boot: migration + cache build ────────────────────────────────────────────
def _progress(msg: str) -> None:
    """Boot-migration progress — printed to STDERR (flushed) so it's visible during
    the one boot that matters. uvicorn configures only its own loggers, so a plain
    log.info here is dropped at default config; a silent multi-second migration would
    look like a hung start and invite a Ctrl-C. Also logged, for structured sinks."""
    print(msg, file=sys.stderr, flush=True)
    log.info(msg)


def _migrate_locked() -> None:
    """Migrate legacy `conversations.json` → per-conversation files + blob dirs.

    Runs iff the legacy file exists and `conversations/` does NOT. STRONG verify:
    every conversation is split AND re-materialized (blobs folded back) and deep-
    compared against the legacy object in memory; ANY mismatch raises (refuse to
    start) with the legacy file untouched. Only after all pass do we write into a
    staging dir, atomically swap it into place, then rename legacy → `.legacy`
    (never deleted)."""
    legacy = _legacy_path()
    convs_dir = _convs_dir()
    legacy_done = legacy.with_suffix(legacy.suffix + ".legacy")
    if convs_dir.exists():
        # Already migrated (the normal case). But a crash BETWEEN the atomic dir swap
        # and the legacy rename can leave conversations.json un-renamed forever (this
        # guard would skip it every subsequent boot); finish that rename now so a later
        # deletion of conversations/ can't silently re-migrate resurrected stale state.
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

    _progress(f"storage-v2 migration: verifying {len(items)} conversation(s)…")
    staged: list[tuple[dict, dict[str, dict]]] = []
    for conv in items:
        if not isinstance(conv, dict):
            raise RuntimeError(f"legacy entry is not an object: {conv!r} — refusing to migrate")
        light, blobs = split_conv(conv)
        if materialize_conv(light, blobs) != conv:
            raise RuntimeError(
                f"storage-v2 migration verify FAILED for conversation "
                f"{conv.get('id')!r}: re-materialized body != legacy. Refusing to "
                f"start; legacy file left untouched."
            )
        staged.append((light, blobs))

    staging = _state_dir() / "conversations.migrating"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    n_blobs = 0
    for light, blobs in staged:
        cid = _check_id(light.get("id"), "conversation")
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
        f"storage-v2 migration complete: {len(staged)} conversation(s), "
        f"{n_blobs} blob(s); legacy → {legacy_done.name}"
    )


def boot() -> None:
    """Called once at app startup (main.lifespan). Migrates if needed (may RAISE to
    refuse start), then rebuilds the summary cache from disk."""
    with locked("conversations"):
        _migrate_locked()
    reset_cache()
    _ensure_loaded()
