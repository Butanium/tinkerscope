"""Install a share pack at RUNTIME, so `?w=<path-or-url>` opens a shared setup.

`tinkerscope --pack <file|url>` has always been a launch-time flag, which means a
collaborator has to restart the server to take a link. This router exposes the same
`pack.load_pack` + `pack.apply_pack` path over HTTP, so pasting a pack URL into the
browser's address bar installs it and opens it (see web/src/lib/pack-install.ts; the
static-site twin does the same work client-side, with no backend).

Two-phase on purpose. A GET-shaped preview reports which workspace ids the pack
would land on and whether they already exist; the browser then asks the human to
overwrite or keep both, and re-POSTs with that answer. Deciding server-side would
mean either clobbering someone's work silently or refusing a legitimate re-import.

A LOCAL PATH is resolved by this server, in its own filesystem — that is the whole
reason the local half of `?w=` needs a backend at all. It is deliberately not
sandboxed to the scan roots: `--pack /any/path.yaml` already reads anywhere, this is
the same operation from a different trigger, and the server is a single-user tool
bound to loopback by default.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/pack", tags=["pack"])


class PackApplyRequest(BaseModel):
    #: Local filesystem path or http(s) URL of the pack file (YAML or JSON).
    source: str
    #: 'overwrite' replaces a workspace whose deterministic id already exists;
    #: 'new' installs alongside it under `<name> (2)`. Omit for a dry-run preview.
    on_conflict: str | None = None
    #: Also overwrite this folder's default params/layout (normally kept once the
    #: folder has been used). Mirrors `--force`.
    force: bool = False


def _load(source: str):
    from ... import pack as packmod

    try:
        return packmod.load_pack(source)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"pack not found: {source}")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - user-supplied path/URL/YAML: report, don't crash
        raise HTTPException(status_code=400, detail=f"could not load pack from {source!r}: {e}")


@router.post("/apply")
def apply(req: PackApplyRequest) -> dict:
    """Preview (no `on_conflict`) or install (`overwrite` | `new`) a pack."""
    from ... import pack as packmod

    p = _load(req.source)
    if req.on_conflict is None:
        return {"status": "preview", **packmod.preview_pack(p)}
    if req.on_conflict not in ("overwrite", "new"):
        raise HTTPException(status_code=422, detail="on_conflict must be 'overwrite' or 'new'")
    summary = packmod.apply_pack(p, force=req.force, on_conflict=req.on_conflict)
    return {"status": "applied", **summary}
