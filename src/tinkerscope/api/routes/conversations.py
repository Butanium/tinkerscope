"""Saved conversation TREES — branchable chat sessions, per scan-root-set.

Each conversation is a named, branchable chat: a per-panel tree (`tree` for the
primary panel, `compare_tree` for the compare panel) plus metadata. The tree
shape is OPAQUE to the server — the browser owns it (see `web/src/lib/tree.ts`);
we only round-trip it as plain JSON. The linear ACTIVE PATH the sampler/CLI read
lives in `PlaygroundState.messages` (separate concern); the tree is the superset
that adds off-path siblings and is NOT broadcast over the state bus.

Persisted to `<state_dir>/conversations.json` as a LIST of entries (mirrors
highlights.py — ordered, id-addressable). Saves are far more frequent than
highlight adds (potentially every branch edit), and two tabs / a tab + the CLI
can both write, so every read-modify-write is wrapped in `store.locked` — the
in-repo flock pattern — to prevent lost updates clobbering sibling entries.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..settings import SETTINGS
from ..store import locked, write_json

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> list[dict]:
    """Load the conversation list.

    Unlike highlights/prefs (where a silent reset-to-default is harmless), these
    are user-authored trees: on a corrupt file we MUST NOT return `[]` and let
    the next save overwrite the only copy. So we side-step store.read_json's
    swallow-and-default and, on a parse error, MOVE the bad file aside to
    `conversations.json.corrupt-<ts>` before returning empty — the backup
    survives the next write, and `corrupted=True` lets the client surface a
    banner.
    """
    path = SETTINGS.conversations_path
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        backup = path.with_suffix(path.suffix + f".corrupt-{int(time.time())}")
        try:
            path.rename(backup)
        except OSError:
            pass
        return []


def _write(items: list[dict]) -> None:
    write_json(SETTINGS.conversations_path, items)


class ConversationCreate(BaseModel):
    name: str = "Untitled"
    system_prompt: str | None = None
    # N-panel shape: {panel_id: opaque tree}.
    trees: dict[str, Any] | None = None
    # legacy 2-panel shape (transitional CLI callers) — synthesized into `trees`.
    tree: dict[str, Any] | None = None
    compare_tree: dict[str, Any] | None = None
    # Per-conversation panel LAYOUT: ordered [{id, run_id, checkpoint}] — which
    # models are shown in which panels. Travels with the conversation so switching
    # restores its model set (and a new conversation can inherit the current one's).
    panels: list[dict[str, Any]] | None = None


class ConversationRename(BaseModel):
    name: str


class TreeSave(BaseModel):
    trees: dict[str, Any]
    system_prompt: str | None = None
    # Per-conversation panel UI (all OPAQUE panel-id lists; the browser owns the
    # semantics — see web/src/lib/conversations.svelte.ts):
    #   reduced_panels — panels folded out of view
    #   send_targets   — panels the composer fires a send to
    #   seen_panels    — defaulting bookkeeping (a panel is defaulted into
    #                    send_targets exactly once, when first seen). Persisted so
    #                    a restart restores the exact deselected/folded state
    #                    instead of re-defaulting every panel ON.
    reduced_panels: list[str] = []
    send_targets: list[str] = []
    seen_panels: list[str] = []
    # Per-conversation panel LAYOUT — see ConversationCreate.panels.
    panels: list[dict[str, Any]] = []


@router.get("")
def list_conversations() -> list[dict]:
    """All saved conversations (ordered; newest activity is the client's to sort)."""
    return _read()


@router.post("")
def create_conversation(req: ConversationCreate) -> dict:
    """Create a conversation; the server assigns id + timestamps."""
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
    entry = {
        "id": str(uuid.uuid4()),
        "name": req.name,
        "system_prompt": req.system_prompt,
        "trees": trees,
        # Panel layout the conversation opens with (inherited from the current
        # conversation, or a single blank panel). Empty ⇒ legacy fallback (browser
        # keeps whatever panels are currently shown).
        "panels": req.panels or [],
        # Fresh conversation: empty panel-UI lists ⇒ the browser defaults every
        # open panel ON the first time it lays them out (see #applyPanelUi).
        "reduced_panels": [],
        "send_targets": [],
        "seen_panels": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    with locked("conversations"):
        items = _read()
        items.append(entry)
        _write(items)
    return entry


@router.patch("/{conversation_id}")
def rename_conversation(conversation_id: str, req: ConversationRename) -> dict:
    with locked("conversations"):
        items = _read()
        for c in items:
            if c.get("id") == conversation_id:
                c["name"] = req.name
                c["updated_at"] = _now()
                _write(items)
                return c
    raise HTTPException(404, f"no conversation {conversation_id}")


@router.put("/{conversation_id}/tree")
def save_conversation_tree(conversation_id: str, req: TreeSave) -> dict:
    """Hot path: persist a conversation's tree(s) after a branch edit."""
    with locked("conversations"):
        items = _read()
        for c in items:
            if c.get("id") == conversation_id:
                c["trees"] = req.trees
                c["system_prompt"] = req.system_prompt
                c["panels"] = req.panels
                c["reduced_panels"] = req.reduced_panels
                c["send_targets"] = req.send_targets
                c["seen_panels"] = req.seen_panels
                c["updated_at"] = _now()
                # self-heal: a legacy {tree, compare_tree} entry upgrades to {trees}
                # on its first save — drop the stale keys so they can't linger.
                c.pop("tree", None)
                c.pop("compare_tree", None)
                _write(items)
                return {"status": "ok", "id": conversation_id}
    raise HTTPException(404, f"no conversation {conversation_id}")


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict:
    with locked("conversations"):
        items = _read()
        kept = [c for c in items if c.get("id") != conversation_id]
        if len(kept) == len(items):
            raise HTTPException(404, f"no conversation {conversation_id}")
        _write(kept)
    return {"status": "ok"}
