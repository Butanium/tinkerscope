"""Discovered runs (replaces the hand-maintained models.yaml).

OpenRouter reference models live in routes/openrouter_models.py (global, UI-managed).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from .. import discovery, pack_models_store
from ..tinker_sampler import get_sampler, supports_thinking

router = APIRouter(prefix="/api", tags=["models"])


def ckpt_label(sampler_path: str, created: int | None) -> str:
    """Readable label for a UUID-only checkpoint: short-uuid · ckpt-name · date.
    (Public: routes/chat.py labels loose-checkpoint sends with it too.)"""
    body = sampler_path.split("://", 1)[-1]
    uuid = body.split(":", 1)[0][:8]
    name = sampler_path.rstrip("/").split("/")[-1]
    when = ""
    if created:
        when = " · " + datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d")
    return f"{uuid} · {name}{when}"


@router.get("/models")
def list_models() -> list[dict]:
    """One entry per discovered run, with its full checkpoint trajectory."""
    out = []
    for r in discovery.list_runs():
        d = discovery.run_to_dict(r)
        d["supports_thinking"] = supports_thinking(r.base_model)
        out.append(d)
    return out


@router.post("/models/refresh")
def refresh_models() -> dict:
    """Rescan the filesystem and re-probe tinker capabilities."""
    runs = discovery.list_runs(force=True)
    return {"status": "ok", "count": len(runs)}


@router.get("/tinker-models")
def tinker_models(refresh: bool = False) -> dict:
    """Everything you can sample directly through tinker, as one filterable list:

      1. Base models `get_server_capabilities` serves (raw, no LoRA) — kind="base".
         ':peft:<ctx>' LoRA-context variants are folded into their base name.
      2. Every sampler checkpoint this account still has (discovery's REST
         `list_user_checkpoints` sweep — NOT the 20-capped oai /v1/models),
         newest first — kind="checkpoint". These are UUID-only (base_model/renderer
         unknown), so they're sampled via the default chat template.

    Each entry carries a unified `id` plus its kind-specific field
    (`base_model` / `sampler_path`). Base entries also carry `supports_thinking`
    (the family exposes a binary thinking toggle) so the composer can hide its
    thinking control for base picks that have none. Base models come first, then
    checkpoints."""
    caps = discovery.get_capabilities()
    names = sorted({m.split(":peft")[0] for m in caps.get("supported_models", [])})
    # `supports_thinking` is computed with the same renderer-pair probe the native
    # sampling path uses (tinker_sampler.supports_thinking) so the composer's
    # thinking toggle only shows for base picks whose family has a binary toggle.
    # Loose checkpoints stay UUID-only (base/renderer unknown) → no field, and the
    # frontend keeps treating them as thinking-capable.
    models = [
        {"kind": "base", "id": n, "label": n, "base_model": n,
         "supports_thinking": supports_thinking(n)}
        for n in names
    ]

    error = caps.get("error")
    srv = discovery.get_servable_paths(force=refresh)
    if srv.get("available"):
        for c in srv.get("checkpoints", []):
            models.append({
                "kind": "checkpoint",
                "id": c["sampler_path"],
                "label": ckpt_label(c["sampler_path"], c.get("created")),
                "sampler_path": c["sampler_path"],
                "created": c.get("created"),
            })
    else:  # sweep unreachable: keep base models, note why
        error = error or f"checkpoint list unavailable: {srv.get('error')}"

    # Registry models (share pack applied to this state dir, or a checkpoint the user
    # named here): explicit sampler paths / base models the account sweep won't list
    # (trained on someone else's account), plus user labels for ones it DOES list.
    # A stored label OVERRIDES the derived one — `934cea31 · final · 2026-07-27` is a
    # label but not a name, and the whole point of naming a checkpoint is to stop
    # reading hex. Entries with no sweep counterpart are appended.
    by_id = {m["id"]: m for m in models}
    for e in pack_models_store.tinker_model_entries():
        if e["id"] in by_id:
            by_id[e["id"]]["label"] = e["label"]
            by_id[e["id"]]["named"] = True
        else:
            e["named"] = True
            models.append(e)
            by_id[e["id"]] = e

    return {
        "available": caps.get("available", False),
        "error": error,
        "models": models,
    }


@router.get("/tinker-models/probe")
async def probe_tinker_model(sampler_path: str) -> dict:
    """Does tinker serve this sampler path? `{available, base_model, error}`.

    Backs the picker's "add a custom checkpoint" row: a path a collaborator sent you
    is in NO local list (not a discovered run, not your account sweep), so the only
    way to tell a good path from a typo is to ask tinker. ~270 ms warm."""
    return await get_sampler().probe_sampler_path(sampler_path)


class TinkerModelName(BaseModel):
    kind: str  # 'ckpt' | 'base'
    ref: str  # sampler_path | base_model
    label: str


@router.post("/tinker-models/name")
def name_tinker_model(body: TinkerModelName) -> dict:
    """Give a checkpoint / base model a human name, persisted for this state dir.

    Writes the same registry share packs use, so a named checkpoint survives a
    restart, shows in every tab, is visible to `tinkpg`, and travels in
    `pack export` — unlike the browser-local ◇/◆ recents, which are per-tab."""
    label = body.label.strip()
    if not label:
        return {"status": "error", "error": "label must not be empty"}
    kind = body.kind if body.kind in ("ckpt", "base") else "ckpt"
    pack_models_store.upsert([{"label": label, "kind": kind, "ref": body.ref}])
    return {"status": "ok", "label": label, "kind": kind, "ref": body.ref}
