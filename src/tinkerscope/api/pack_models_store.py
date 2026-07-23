"""Per-state-dir registry of models a share-pack injected.

Distinct from the two model sources discovery already knows: auto-discovered runs
(scan dirs) and the account's checkpoint sweep (`list_user_checkpoints`). A pack ships
EXPLICIT refs — sampler paths / base models a collaborator has no local run dir for and
that the account sweep won't list (they were trained on someone else's account) — so we
persist them here and merge them into `GET /api/tinker-models`, which is what the
browser's "+ Tinker model" typeahead renders. That makes the shared checkpoints
first-class addable models, not just pre-baked panels.

Stored at `<state_dir>/pack_models.json` as `[{label, kind, ref}]` (kind: ckpt | base;
openrouter refs live in the global openrouter list, not here).
"""
from __future__ import annotations

from .store import read_json, write_json
from .tinker_sampler import supports_thinking


def _path():
    # Resolve SETTINGS lazily (fresh module lookup) so a test that reloads settings
    # against a new XDG_STATE_HOME is honored — mirrors conversation_store._state_dir.
    from .settings import SETTINGS

    return SETTINGS.pack_models_path


def read() -> list[dict]:
    return read_json(_path(), []) or []


def upsert(new: list[dict]) -> list[dict]:
    """Add/refresh models, de-duped by (kind, ref). Later entries win (label refresh)."""
    index: dict[tuple[str, str], dict] = {(m["kind"], m["ref"]): m for m in read()}
    for m in new:
        index[(m["kind"], m["ref"])] = {"label": m["label"], "kind": m["kind"], "ref": m["ref"]}
    items = list(index.values())
    write_json(_path(), items)
    return items


def tinker_model_entries() -> list[dict]:
    """Pack models in the `/api/tinker-models` entry shape (ckpt → kind 'checkpoint',
    base → kind 'base'). The `pack` flag lets a client tell these from the account
    sweep's entries."""
    out: list[dict] = []
    for m in read():
        if m["kind"] == "ckpt":
            out.append({
                "kind": "checkpoint", "id": m["ref"], "label": m["label"],
                "sampler_path": m["ref"], "pack": True,
            })
        elif m["kind"] == "base":
            out.append({
                "kind": "base", "id": m["ref"], "label": m["label"], "base_model": m["ref"],
                "supports_thinking": supports_thinking(m["ref"]), "pack": True,
            })
    return out
