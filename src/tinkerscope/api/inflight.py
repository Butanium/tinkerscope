"""In-flight chat placements — the delete-vs-generation guard.

The server folds a chat's samples under its `parent_node` at terminal
(HANDOFF_SERVER_AUTHORITY §4.3). Between chat begin and that terminal, the one
real cross-client race (§5) is a `delete` op pruning a subtree that contains the
parent: the fold would either graft onto nothing (silent loss) or resurrect the
branch. So chats register their placement here, and `tree_ops._op_delete`
REJECTS a delete whose doomed subtree contains a registered parent — loud 409,
nothing applied, retry after the chat ends (or cancel it).

Registration/validation happens inside `workspace_store.register_chat_placement`
under the workspaces flock — the same lock every op apply holds — so the guard
and the delete check are serialized, no
check-then-register window. Entries are COUNTED, not set-membership: two
concurrent fires under one parent (`--force` regens) each register, and the
guard holds until the last one releases.

Process-local on purpose, like `routes/chat.py`'s `_INFLIGHT`: a chat's producer
lives in this process, so a restart that kills the producers also (correctly)
clears their placements.
"""
from __future__ import annotations

import threading
from collections import Counter

_LOCK = threading.Lock()
_REG: Counter[tuple[str, str, str]] = Counter()  # (workspace, panel, parent_node) -> live chats


def register(workspace: str, panel: str, parent_node: str) -> None:
    with _LOCK:
        _REG[(workspace, panel, parent_node)] += 1


def release(workspace: str, panel: str, parent_node: str) -> None:
    """Idempotent-ish: releasing below zero clamps (a double release must not
    underflow into blocking unrelated future chats' accounting)."""
    with _LOCK:
        key = (workspace, panel, parent_node)
        left = _REG.get(key, 0) - 1
        if left > 0:
            _REG[key] = left
        else:
            _REG.pop(key, None)


def parents_under(workspace: str, panel: str) -> set[str]:
    """Registered parent-node ids for one (workspace, panel) — what a delete op
    checks its doomed subtree against."""
    with _LOCK:
        return {p for (w, pl, p), n in _REG.items() if w == workspace and pl == panel and n > 0}


def reset() -> None:
    """Test isolation only."""
    with _LOCK:
        _REG.clear()
