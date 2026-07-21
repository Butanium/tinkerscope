"""Saved conversation TREES — branchable chat sessions, per scan-root-set (v2).

Each conversation is a named, branchable chat: a per-panel tree (`trees` keyed by
panel id) plus metadata. The tree shape is OPAQUE to the server — the browser owns
it (see `web/src/lib/tree.ts`); we only round-trip it as plain JSON.

Storage v2 (see `docs/STORAGE_V2.md` + `api/conversation_store.py`): each
conversation is its OWN light file under `<state>/conversations/<id>.json`, and a
node's two heavy fields (`token_logprobs`, `raw_meta`) live in per-node write-once
blobs under `<id>.blobs/`. This router is a thin HTTP layer over the store, which
owns the on-disk layout, the boot migration, the in-memory summary cache, and the
`store.locked` flock convention. Wire contract:

  GET    /api/conversations           → summaries (no trees); ?bodies=1 → light bodies
  GET    /api/conversations/{id}       → one light body
  POST   /api/conversations/{id}/node-blobs {nodes:[...]} → {id: {token_logprobs?, raw_meta?}}
  POST   /api/conversations            → create (unchanged shape; server strips blobs)
  PATCH  /api/conversations/{id}       → layout-only metadata (no tree bytes)
  PUT    /api/conversations/{id}/tree  → PARTIAL tree upsert (dirty panels) + dropped_trees
  DELETE /api/conversations/{id}       → remove light file + blobs dir
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import conversation_store as store

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    # Optional client-supplied id. The browser keeps a NEW conversation as an unsaved
    # DRAFT (not persisted until something actually changes), so it mints the id up
    # front and sends it here on the first save — keeping the URL/list id stable. The
    # create then upserts by id (idempotent under a save race). Omitted ⇒ server mints.
    id: str | None = None
    name: str = "Untitled"
    system_prompt: str | None = None
    # Power state of the conversation's system prompt (False = kept but muted).
    # None/absent (legacy bodies, old clients) → readers derive from text presence.
    system_enabled: bool | None = None
    # N-panel shape: {panel_id: opaque tree}.
    trees: dict[str, Any] | None = None
    # legacy 2-panel shape (transitional CLI callers) — synthesized into `trees`.
    tree: dict[str, Any] | None = None
    compare_tree: dict[str, Any] | None = None
    # Per-conversation panel LAYOUT: ordered [{id, run_id, checkpoint}] — which
    # models are shown in which panels. Travels with the conversation so switching
    # restores its model set (and a new conversation can inherit the current one's).
    panels: list[dict[str, Any]] | None = None
    # Per-conversation panel UI (see TreeSave) — sent when a draft is first persisted
    # so its folded/send-target state survives even if set before the first save.
    reduced_panels: list[str] = []
    send_targets: list[str] = []
    seen_panels: list[str] = []


class ConversationPatch(BaseModel):
    """Layout-only metadata patch (v2 §2.4). Every field optional — only provided
    keys apply — so a model swap / send-target toggle ships NO tree bytes. `name`
    alone is the old rename call."""

    name: str | None = None
    system_prompt: str | None = None
    system_enabled: bool | None = None
    panels: list[dict[str, Any]] | None = None
    reduced_panels: list[str] | None = None
    send_targets: list[str] | None = None
    seen_panels: list[str] | None = None


class TreeSave(BaseModel):
    # PARTIAL upsert map: only DIRTY panels (merged over the stored trees). Nodes MAY
    # carry inline token_logprobs/raw_meta (fresh folds) — the server strips them into
    # write-once blobs and stores light nodes.
    trees: dict[str, Any]
    # Panels removed since the last save — dropped from the stored trees.
    dropped_trees: list[str] = []
    system_prompt: str | None = None
    system_enabled: bool | None = None  # see ConversationCreate
    # Per-conversation panel UI (all OPAQUE panel-id lists; the browser owns the
    # semantics — see web/src/lib/conversations.svelte.ts):
    #   reduced_panels — panels folded out of view
    #   send_targets   — panels the composer fires a send to
    #   seen_panels    — defaulting bookkeeping (a panel is defaulted into
    #                    send_targets exactly once, when first seen). Persisted so
    #                    a restart restores the exact deselected/folded state.
    reduced_panels: list[str] = []
    send_targets: list[str] = []
    seen_panels: list[str] = []
    # Per-conversation panel LAYOUT — see ConversationCreate.panels.
    panels: list[dict[str, Any]] = []


class NodeBlobsRequest(BaseModel):
    # Node ids to fetch heavy blobs for (POST, not GET, because the list can be long).
    nodes: list[str] = []


@router.get("")
def list_conversations(bodies: int = Query(0)) -> list[dict]:
    """Summaries by default (no trees); ?bodies=1 → light bodies (trees incl., blobs
    excl.) for the CLI's link/browse paths."""
    return store.list_bodies() if bodies else store.list_summaries()


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    """One light conversation body (trees incl., blobs excl.)."""
    body = store.get_body(conversation_id)
    if body is None:
        raise HTTPException(404, f"no conversation {conversation_id}")
    return body


@router.post("/{conversation_id}/node-blobs")
def get_node_blobs(conversation_id: str, req: NodeBlobsRequest) -> dict:
    """Heavy blobs for a batch of node ids: {node_id: {token_logprobs?, raw_meta?}}.
    Unknown ids are omitted (not an error)."""
    return store.get_blobs(conversation_id, req.nodes)


@router.post("")
def create_conversation(req: ConversationCreate) -> dict:
    """Create a conversation. The server mints the id + timestamps unless the client
    supplied an id (a draft being persisted for the first time), in which case it
    upserts by that id — so a save race can't duplicate the entry. Heavy node fields
    are stripped into write-once blobs; the returned body is light."""
    if req.id is not None and not store.is_safe_id(req.id):
        raise HTTPException(400, "invalid conversation id")
    trees = req.trees
    if trees is None:
        # transitional: synthesize {trees} from a legacy {tree, compare_tree} body
        trees = {}
        if req.tree is not None:
            trees["primary"] = req.tree
        if req.compare_tree is not None:
            trees["compare"] = req.compare_tree
    if not trees:
        trees = {"primary": {}}
    return store.upsert(
        id=req.id,
        name=req.name,
        system_prompt=req.system_prompt,
        system_enabled=req.system_enabled,
        trees=trees,
        panels=req.panels or [],
        reduced_panels=req.reduced_panels,
        send_targets=req.send_targets,
        seen_panels=req.seen_panels,
    )


@router.patch("/{conversation_id}")
def patch_conversation(conversation_id: str, req: ConversationPatch) -> dict:
    """Layout-only metadata update — rename + system_prompt + panel layout/UI, no
    tree bytes. Returns the updated summary."""
    fields = req.model_dump(exclude_unset=True)
    summary = store.patch_meta(conversation_id, fields)
    if summary is None:
        raise HTTPException(404, f"no conversation {conversation_id}")
    return summary


@router.put("/{conversation_id}/tree")
def save_conversation_tree(conversation_id: str, req: TreeSave) -> dict:
    """Hot path: persist a conversation's DIRTY panel tree(s) after a branch edit.
    `trees` is a partial upsert (dirty panels only) + `dropped_trees` for removals."""
    ok = store.save_tree(
        conversation_id,
        trees_partial=req.trees,
        dropped_trees=req.dropped_trees,
        system_prompt=req.system_prompt,
        system_enabled=req.system_enabled,
        panels=req.panels,
        reduced_panels=req.reduced_panels,
        send_targets=req.send_targets,
        seen_panels=req.seen_panels,
    )
    if not ok:
        raise HTTPException(404, f"no conversation {conversation_id}")
    return {"status": "ok", "id": conversation_id}


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict:
    if not store.delete(conversation_id):
        raise HTTPException(404, f"no conversation {conversation_id}")
    return {"status": "ok"}
