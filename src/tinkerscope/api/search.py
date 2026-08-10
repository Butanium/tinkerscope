"""Cross-workspace text search — the engine behind GET /api/search.

Searches EVERY branch of every saved workspace (active or not): node `content`,
assistant `reasoning` (thinking), thread `system_prompt` (root user nodes), plus
workspace-level matches (name, global system prompt, panel model ids) that the
palette pins above node hits. One hit per (node, field) — the FIRST match in a
field, like `tinkpg grep` always reported.

Cached scan, not an index. The corpus is light trees only (~tens of MB; blobs
never hold text), and `workspace_store._bodies` already memoizes parsed bodies,
so the only per-query cost worth removing is re-walking every tree dict — hence
`_UNITS` caches each workspace's flattened text units keyed on its `updated_at`
stamp. That key is sound under the store's own consistency model (this server is
the sole writer; every store write restamps `updated_at`). If the corpus ever
outgrows a linear scan, swap these internals for an FTS index — the wire shape
in `search()` deliberately doesn't leak the scan.

Snippets travel as a (before, match_display, after) TRIPLE, each
whitespace-collapsed separately, so the client can highlight the matched span
without offset math over collapsed text.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

from . import workspace_store as store

ROOT = "__root__"


@dataclass
class Unit:
    """One searchable text span, pre-resolved to its tree address."""

    panel: str
    node_id: str
    parent: str | None
    role: str
    field: str  # 'content' | 'reasoning' | 'system_prompt'
    text: str
    thread: int | None
    on_active_path: bool
    sib_index: int  # 0-based position among the parent's children
    sib_count: int


_LOCK = threading.Lock()
_UNITS: dict[str, tuple[str, list[Unit]]] = {}  # cid -> (updated_at, units)


def reset_cache() -> None:
    with _LOCK:
        _UNITS.clear()


def _selected_child(tree: dict, parent_key: str) -> str | None:
    """Mirror of tree.ts `selectedChildId`: the selected child, else the LAST one."""
    if parent_key == ROOT:
        kids = tree.get("rootChildren") or []
    else:
        kids = (tree.get("nodes", {}).get(parent_key) or {}).get("children") or []
    if not kids:
        return None
    sel = (tree.get("selected") or {}).get(parent_key)
    return sel if sel in kids else kids[-1]


def _active_ids(tree: dict) -> set[str]:
    """Node ids on the tree's active path (root → leaf following selection)."""
    out: set[str] = set()
    parent_key = ROOT
    nodes = tree.get("nodes") or {}
    while True:
        cid = _selected_child(tree, parent_key)
        if cid is None or cid in out or cid not in nodes:
            break
        out.add(cid)
        parent_key = cid
    return out


def _thread_of(tree: dict, node_id: str) -> int | None:
    """1-indexed root-thread number a node belongs to (walk parents to the root)."""
    nodes = tree.get("nodes") or {}
    nid: str | None = node_id
    seen: set[str] = set()
    while nid is not None and nid not in seen:
        seen.add(nid)
        node = nodes.get(nid)
        if node is None:
            return None
        parent = node.get("parent")
        if parent is None:
            roots = tree.get("rootChildren") or []
            return roots.index(nid) + 1 if nid in roots else None
        nid = parent
    return None


def _extract_units(body: dict) -> list[Unit]:
    """Flatten a light body into text units, in conversation order (DFS per root)."""
    units: list[Unit] = []
    for pid, tree in (body.get("trees") or {}).items():
        if not isinstance(tree, dict):
            continue
        nodes = tree.get("nodes") or {}
        active = _active_ids(tree)
        threads: dict[str, int | None] = {}

        def sibs(node: dict) -> tuple[int, int]:
            parent = node.get("parent")
            kids = (
                tree.get("rootChildren") or []
                if parent is None
                else (nodes.get(parent) or {}).get("children") or []
            )
            nid = node.get("id")
            return (kids.index(nid) if nid in kids else 0), max(len(kids), 1)

        def visit(nid: str, seen: set[str]) -> None:
            if nid in seen:  # defensive: trees are acyclic
                return
            seen.add(nid)
            node = nodes.get(nid)
            if not isinstance(node, dict):
                return
            if nid not in threads:
                threads[nid] = _thread_of(tree, nid)
            fields = [("content", node.get("content"))]
            if node.get("role") == "assistant":
                fields.append(("reasoning", node.get("reasoning")))
            if node.get("parent") is None:
                fields.append(("system_prompt", node.get("system_prompt")))
            i, n = sibs(node)
            for field, text in fields:
                if not text or not isinstance(text, str):
                    continue
                units.append(Unit(
                    panel=pid, node_id=nid, parent=node.get("parent"),
                    role=node.get("role") or "?", field=field, text=text,
                    thread=threads[nid], on_active_path=nid in active,
                    sib_index=i, sib_count=n,
                ))
            for kid in node.get("children") or []:
                visit(kid, seen)

        seen: set[str] = set()
        for rid in tree.get("rootChildren") or []:
            visit(rid, seen)
        for nid in nodes:  # orphans (defensive) — still searchable
            visit(nid, seen)
    return units


def _units_for(cid: str, updated_at: str) -> list[Unit]:
    with _LOCK:
        hit = _UNITS.get(cid)
        if hit is not None and hit[0] == updated_at:
            return hit[1]
    body = store.get_body(cid)
    units = _extract_units(body) if body else []
    with _LOCK:
        _UNITS[cid] = (updated_at, units)
        # prune entries for deleted workspaces so the cache can't grow unbounded
        if len(_UNITS) > 4 * max(len(store.list_summaries()), 1):
            live = {s["id"] for s in store.list_summaries()}
            for k in [k for k in _UNITS if k not in live]:
                del _UNITS[k]
    return units


def _collapse(s: str) -> str:
    return " ".join(s.split())


def _snippet_parts(text: str, m: re.Match, width: int) -> tuple[str, str, str]:
    """(before, match, after) display triple around the FIRST match, each
    whitespace-collapsed separately; before/after capped at width//2, the matched
    text itself capped at width. Collapsing eats the whitespace AT the part
    boundaries, so a single boundary space is re-added where the raw text had
    one — otherwise the concatenated display reads "matchnext-word"."""
    half = max(20, width // 2)
    raw_before = text[max(0, m.start() - half):m.start()]
    before = _collapse(raw_before)
    if before and raw_before[-1:].isspace():
        before += " "
    if m.start() > half:
        before = "…" + before
    mid = _collapse(m.group(0))
    if len(mid) > width:
        mid = mid[:width] + "…"
    raw_after = text[m.end():m.end() + half]
    after = _collapse(raw_after)
    if after and raw_after[:1].isspace():
        after = " " + after
    if m.end() + half < len(text):
        after = after + "…"
    return before, mid, after


def compile_query(q: str, regex: bool, case_sensitive: bool) -> re.Pattern:
    """May raise re.error on a bad regex — the route maps that to a 400."""
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(q if regex else re.escape(q), flags)


def search(
    q: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    ws: str | None = None,
    max_hits: int = 200,
    width: int = 160,
) -> dict[str, Any]:
    rx = compile_query(q, regex, case_sensitive)
    summaries = store.list_summaries()
    if ws is not None:
        summaries = [s for s in summaries if s.get("id") == ws]
    # newest-touched first — the forgotten-screenshot case is usually recent-ish,
    # and the palette reads top-down
    summaries = sorted(summaries, key=lambda s: s.get("updated_at") or "", reverse=True)

    workspace_hits: list[dict] = []
    hits: list[dict] = []
    total = 0
    ws_totals: dict[str, dict] = {}  # cid -> {workspace_id, workspace_name, total}
    for s in summaries:
        cid, cname = s["id"], s.get("name") or "?"

        def ws_hit(field: str, text: str, panel: str | None = None) -> None:
            m = rx.search(text)
            if m:
                b, mid, a = _snippet_parts(text, m, width)
                workspace_hits.append({
                    "workspace_id": cid, "workspace_name": cname, "field": field,
                    "panel": panel, "match": m.group(0),
                    "before": b, "match_display": mid, "after": a,
                    "updated_at": s.get("updated_at"),
                })

        ws_hit("name", cname)
        for p in s.get("panels") or []:
            for key in ("run_id", "checkpoint"):
                v = p.get(key)
                if v and rx.search(str(v)):
                    ws_hit("model", str(v), panel=p.get("id"))
                    break  # one model hit per panel is plenty
        # global system prompt lives on the body, not the summary
        body = store.get_body(cid)
        gsp = (body or {}).get("system_prompt")
        if gsp:
            ws_hit("system", gsp)

        for u in _units_for(cid, s.get("updated_at") or ""):
            m = rx.search(u.text)
            if m is None:
                continue
            total += 1
            wt = ws_totals.setdefault(
                cid, {"workspace_id": cid, "workspace_name": cname, "total": 0})
            wt["total"] += 1
            if len(hits) >= max_hits:
                continue  # keep counting, stop collecting
            b, mid, a = _snippet_parts(u.text, m, width)
            hits.append({
                "workspace_id": cid, "workspace_name": cname,
                "panel": u.panel, "node_id": u.node_id, "parent": u.parent,
                "role": u.role, "field": u.field, "thread": u.thread,
                "on_active_path": u.on_active_path,
                "sib_index": u.sib_index, "sib_count": u.sib_count,
                "match": m.group(0), "before": b, "match_display": mid, "after": a,
            })
    return {
        "query": q,
        "workspace_hits": workspace_hits,
        "hits": hits,
        # per-workspace NODE-hit totals (uncapped), in result order — group
        # headers ("k matches") and the CLI's end tally both read this
        "workspace_totals": list(ws_totals.values()),
        "total": total,
        "truncated": total > max_hits,
        "workspaces_searched": len(summaries),
        "workspaces_matched": len(ws_totals),
    }
