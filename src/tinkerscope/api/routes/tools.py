"""Tool handlers outside a chat: the dry-run endpoint (the tools editor's "test"
button, `tinkerscope tools test`) and named TOOL SETS — a workspace's tools +
handlers saved under a name, so a scaffold ("AI Village") can be loaded into any
workspace. Sets are instance-wide (one `tool_sets.json` per scan-root state dir),
like pins; loading one writes the workspace's own meta, which is all a fire reads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import tool_handlers, tree_ops
from ..settings import SETTINGS
from ..store import locked, read_json, write_json

router = APIRouter(prefix="/api", tags=["tools"])


class HandlerTest(BaseModel):
    handler: dict[str, Any]
    name: str
    # the call's arguments, already parsed (an object), as a handler receives them
    args: Any = None
    call_id: str = "call_test"
    context: dict[str, Any] | None = None


@router.post("/tool-handlers/test")
async def test_handler(req: HandlerTest) -> dict:
    """Run one handler on sample arguments — no model, no tree. Returns
    `{ok, text?, image?, error?, logs, ms}`; python is refused (ok:false) on a
    server started without --allow-tool-code."""
    try:
        spec = (tool_handlers.check_tool_handlers({req.name: req.handler}) or {}).get(req.name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if spec is None or spec["mode"] == "manual":
        raise HTTPException(400, "a manual tool has no handler to run")
    ctx = req.context or {"workspace_id": None, "panel": None, "step": 1, "thread": []}
    return await tool_handlers.test_run(spec, name=req.name, args=req.args if req.args is not None else {},
                                        call_id=req.call_id, context=ctx, allow_code=SETTINGS.allow_tool_code)


# ── named tool sets ──────────────────────────────────────────────────────────
def _path():
    return SETTINGS.state_dir / "tool_sets.json"


class ToolSet(BaseModel):
    tools: list[dict[str, Any]] | None = None
    tool_handlers: dict[str, Any] | None = None
    tool_max_steps: int | None = None


def _check_name(name: str) -> str:
    name = name.strip()
    if not name or "/" in name or len(name) > 120:
        raise HTTPException(400, "a tool set needs a name (no '/', ≤120 chars)")
    return name


@router.get("/tool-sets")
def list_tool_sets() -> list[dict]:
    sets = read_json(_path(), {})
    return [{"name": k, **v} for k, v in sorted(sets.items(), key=lambda kv: kv[0].lower())]


@router.put("/tool-sets/{name}")
def put_tool_set(name: str, req: ToolSet) -> dict:
    name = _check_name(name)
    try:
        entry = {
            "tools": tree_ops.check_tools(req.tools),
            "tool_handlers": tool_handlers.check_tool_handlers(req.tool_handlers),
            "tool_max_steps": tool_handlers.check_max_steps(req.tool_max_steps),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    with locked("tool_sets"):
        sets = read_json(_path(), {})
        sets[name] = entry
        write_json(_path(), sets)
    return {"name": name, **entry}


@router.delete("/tool-sets/{name}")
def delete_tool_set(name: str) -> dict:
    with locked("tool_sets"):
        sets = read_json(_path(), {})
        if name not in sets:
            raise HTTPException(404, f"no tool set {name!r}")
        del sets[name]
        write_json(_path(), sets)
    return {"ok": True}
