"""Highlight rules — render-time text coloring driven by user-defined patterns.

Ported from samplescope's highlight model (its `web/src/lib/highlights.ts` +
`api/routes/highlights.py`), trimmed for a chat playground: **role scope only**
— no tabular-column or JS-condition scoping, since a chat transcript has no
arbitrary `row` to gate on. Keep this model in sync with samplescope's: same
rule shape, same overlap policy (earlier rule wins), same tint.

list / upsert-by-id / delete / reorder. Rules return in `sort_order` order so
the client resolves overlapping highlights deterministically. Persisted to
`<state_dir>/highlights.json`.

NB: this is NOT the saved-samples feature — that lives in `routes/pins.py`
now. The overhaul reclaimed the "highlights" name for coloring; legacy saved
samples were migrated to `pins.json` (see `settings._migrate_legacy_highlights`).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..settings import SETTINGS
from ..store import locked, read_json, write_json

router = APIRouter(prefix="/api/highlights", tags=["highlights"])


class HighlightRule(BaseModel):
    """One named coloring rule. `scope_role` None = applies to any role."""

    id: str
    name: str = "untitled"
    enabled: bool = True
    patterns: list[str] = Field(default_factory=list)
    combinator: Literal["or", "and"] = "or"
    is_regex: bool = False
    case_sensitive: bool = False
    color: str = "#fde047"
    scope_role: Optional[str] = None  # user | assistant | system | None (any)
    sort_order: int = 0


def _read() -> list[dict]:
    """Current rules — [] on a virgin state dir. No seeded defaults: a fresh
    instance (or a collaborator applying a share pack into a clean folder) starts
    with NO coloring rules, rather than the original fork's fixture highlighters
    (ed_sheeran / dentist / vesuvius / dreams), which were leftover artifacts."""
    return read_json(SETTINGS.highlights_path, [])


def _write(items: list[dict]) -> None:
    write_json(SETTINGS.highlights_path, items)


def _sorted(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda r: r.get("sort_order", 0))


@router.get("", response_model=list[HighlightRule])
def list_rules() -> list[HighlightRule]:
    """All rules, sort_order-ascending (earlier = higher overlap priority)."""
    return [HighlightRule(**r) for r in _sorted(_read())]


@router.put("/{rule_id}", response_model=HighlightRule)
def upsert_rule(rule_id: str, payload: dict) -> HighlightRule:
    """Create or replace a rule. `rule_id` in the URL is authoritative."""
    name = (payload.get("name") or "").strip()
    patterns = [str(p) for p in (payload.get("patterns") or []) if str(p).strip() != ""]
    if not name:
        raise HTTPException(400, "name required")
    if not patterns:
        raise HTTPException(400, "at least one pattern required")
    with locked("highlights"):
        items = _read()
        existing = next((r for r in items if r.get("id") == rule_id), None)
        if existing is None:
            sort_order = max((int(r.get("sort_order", 0)) for r in items), default=-1) + 1
        else:
            sort_order = int(payload.get("sort_order", existing.get("sort_order", 0)))
        rule = HighlightRule(
            id=rule_id,
            name=name,
            enabled=bool(payload.get("enabled", True)),
            patterns=patterns,
            combinator="and" if payload.get("combinator") == "and" else "or",
            is_regex=bool(payload.get("is_regex", False)),
            case_sensitive=bool(payload.get("case_sensitive", False)),
            color=payload.get("color") or "#fde047",
            scope_role=(payload.get("scope_role") or None),
            sort_order=sort_order,
        )
        items = [r for r in items if r.get("id") != rule_id]
        items.append(rule.model_dump())
        _write(_sorted(items))
    return rule


@router.delete("/{rule_id}")
def delete_rule(rule_id: str) -> dict:
    """Drop one rule. Idempotent — missing ids return ok."""
    with locked("highlights"):
        items = _read()
        _write([r for r in items if r.get("id") != rule_id])
    return {"status": "ok"}


@router.post("/reorder")
def reorder_rules(payload: dict) -> dict:
    """Set sort_order to the index of each id in `ids`. Unlisted ids unchanged."""
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
        raise HTTPException(400, "ids must be list[str]")
    order = {rid: i for i, rid in enumerate(ids)}
    with locked("highlights"):
        items = _read()
        for r in items:
            if r.get("id") in order:
                r["sort_order"] = order[r["id"]]
        _write(_sorted(items))
    return {"status": "ok", "n": len(ids)}
