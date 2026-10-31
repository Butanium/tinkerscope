"""Split rows that hold several recorded messages into one row per message.

`tinkerscope import --responses-call` used to group consecutive same-side items
into one node (a system prompt + a second system message as ONE system row, a run
of user messages as one user row). The wire was right, the rows weren't: one row
drew several messages and an edit collapsed them. The importer now makes a node per
turn (`openrouter_responses.turns`); this rewrites workspaces imported before that.

Each merged node N becomes a chain of fresh nodes, one per turn, in its place: same
parent, same sibling slot, N's children (samples, forks, everything below) re-hung
under the last piece, selections carried over. Fresh ids because blobs are
write-once per node id. A copy of N (a Shift-edit / fork graft, `items_ref` → N) is
split the same way, its pieces pointing at N's pieces' blobs. N itself goes to the
workspace's trash journal, so `tinkerscope trash restore` can undo a split.

Talks to a running server (`TINKERSCOPE_BASE_URL`, default http://127.0.0.1:8767)
and applies one `replace_tree` per changed panel, in ONE atomic ops batch:

    TINKERSCOPE_BASE_URL=http://127.0.0.1:8770 uv run scripts/split_recorded_items.py <workspace-id> [--dry-run]

Re-running is a no-op: a node with one turn is left alone.
"""
from __future__ import annotations

import argparse
import copy
import os
import sys

import httpx

from tinkerscope.api.openrouter_responses import turns
from tinkerscope.api.tree_ops import ROOT, mint_node_id
from tinkerscope.cli import _turn_node

SPLITTABLE = ("user", "system", "developer", "assistant")


def split_workspace(body: dict, blobs: dict) -> tuple[dict, list[str]]:
    """(the changed panels' new trees, a log line per split node). Pure."""
    trees = {p: copy.deepcopy(t) for p, t in (body.get("trees") or {}).items() if t}
    cands: list[tuple[str, dict]] = [
        (p, nd) for p, t in trees.items() for nd in (t.get("nodes") or {}).values()
        if nd.get("role") in SPLITTABLE and (nd.get("has_items") or nd.get("items_ref"))
    ]
    # originals first, so a copy can point at the pieces of the node it copies
    cands.sort(key=lambda c: bool(c[1].get("items_ref")))
    pieces_of: dict[str, list[tuple[str, bool]]] = {}  # original id → [(piece id, has recorded)]
    changed: set[str] = set()
    log: list[str] = []
    for panel, nd in cands:
        ref = nd.get("items_ref") or nd["id"]
        blob = blobs.get(ref) or {}
        items, rec = blob.get("items") or [], blob.get("items_recorded")
        if rec is not None and len(rec) != len(items):
            log.append(f"  skip {panel}:{nd['id']} — recorded items don't line up with the wire ones")
            continue
        groups = turns(items)
        if len(groups) <= 1:
            continue
        mapped = pieces_of.get(ref) if nd.get("items_ref") else None
        new_nodes: list[dict] = []
        for k, (side, idx) in enumerate(groups):
            piece = _turn_node(side, [items[i] for i in idx], [(rec or items)[i] for i in idx], {})
            if piece.get("reasoning_account") and nd.get("reasoning_account"):
                piece["reasoning_account"] = nd["reasoning_account"]
            if mapped:  # a copy: read the original's piece blob, like any graft copy
                pid, has_rec = mapped[k]
                piece.pop("items", None)
                piece.pop("items_recorded", None)
                piece.update({"has_items": True, "items_ref": pid, **({"has_items_recorded": True} if has_rec else {})})
            piece["id"] = mint_node_id()
            new_nodes.append(piece)
        if new_nodes[0]["role"] == "user" and nd.get("system_prompt"):
            new_nodes[0]["system_prompt"] = nd["system_prompt"]
        if not nd.get("items_ref"):
            pieces_of[nd["id"]] = [(p["id"], "items_recorded" in p) for p in new_nodes]
        _replace_with_chain(trees[panel], nd["id"], new_nodes)
        changed.add(panel)
        log.append(f"  {panel}:{nd['id']} {nd['role']} → {len(new_nodes)} rows "
                   f"({', '.join(p['role'] for p in new_nodes)})")
    return {p: trees[p] for p in changed}, log


def _replace_with_chain(tree: dict, nid: str, chain: list[dict]) -> None:
    nodes, sel = tree["nodes"], tree.setdefault("selected", {})
    old = nodes.pop(nid)
    parent = old.get("parent")
    pkey = parent or ROOT
    for i, piece in enumerate(chain):
        piece["parent"] = parent if i == 0 else chain[i - 1]["id"]
        piece["children"] = [chain[i + 1]["id"]] if i + 1 < len(chain) else list(old.get("children") or [])
        nodes[piece["id"]] = piece
        if i + 1 < len(chain):
            sel[piece["id"]] = chain[i + 1]["id"]
    last = chain[-1]["id"]
    for c in old.get("children") or []:
        nodes[c]["parent"] = last
    if nid in sel:
        sel[last] = sel.pop(nid)
    sibs = tree["rootChildren"] if parent is None else nodes[parent]["children"]
    sibs[sibs.index(nid)] = chain[0]["id"]
    if sel.get(pkey) == nid:
        sel[pkey] = chain[0]["id"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("workspace", help="workspace id")
    ap.add_argument("--dry-run", action="store_true", help="print the splits, write nothing")
    a = ap.parse_args()
    base = os.environ.get("TINKERSCOPE_BASE_URL", "http://127.0.0.1:8767").rstrip("/")
    with httpx.Client(base_url=base, timeout=600) as c:
        body = c.get(f"/api/workspaces/{a.workspace}").raise_for_status().json()
        refs = sorted({nd.get("items_ref") or nd["id"] for t in (body.get("trees") or {}).values() if t
                       for nd in (t.get("nodes") or {}).values() if nd.get("has_items") or nd.get("items_ref")})
        blobs = c.post(f"/api/workspaces/{a.workspace}/node-blobs",
                       json={"nodes": refs, "fields": ["items", "items_recorded"]}).raise_for_status().json() if refs else {}
        trees, log = split_workspace(body, blobs)
        print(f"{body.get('name')!r} ({a.workspace}): {sum('→' in ln for ln in log)} node(s) to split "
              f"in {len(trees)} panel(s)")
        print("\n".join(log))
        if a.dry_run or not trees:
            return
        r = c.post(f"/api/workspaces/{a.workspace}/ops",
                   json={"ops": [{"op": "replace_tree", "panel": p, "tree": t} for p, t in trees.items()]})
        if r.status_code >= 400:
            sys.exit(f"ops rejected (HTTP {r.status_code}): {r.text}")
        print(f"applied — rev {r.json()['rev']}")


if __name__ == "__main__":
    main()
