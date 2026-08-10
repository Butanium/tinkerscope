"""GET /api/search — cross-workspace text search (see `api/search.py`).

  GET /api/search?q=<text>[&regex=1][&case=1][&ws=<id>][&max_hits=N][&width=N]
    → {query, workspace_hits, hits, total, truncated,
       workspaces_searched, workspaces_matched}

Consumers: the browser's Ctrl+K palette, and `tinkpg grep` (which used to fetch
every body via ?bodies=1 and scan client-side — same semantics, now shared).
A bad regex is a 400 with the compile error, not a 500.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query

from .. import search as engine

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
def search(
    q: str = Query(..., min_length=1),
    regex: bool = False,
    case: bool = Query(False, description="case-sensitive"),
    ws: str | None = Query(None, description="restrict to one workspace id"),
    max_hits: int = Query(200, ge=1, le=5000),
    width: int = Query(160, ge=40, le=2000),
) -> dict:
    try:
        return engine.search(
            q, regex=regex, case_sensitive=case, ws=ws,
            max_hits=max_hits, width=width,
        )
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"bad regex: {e}")
