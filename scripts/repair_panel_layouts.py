#!/usr/bin/env python3
"""Audit / repair workspace panel layouts against the EVIDENCE in the node blobs.

Why this exists: until 2026-07-24 the per-panel model layout (`panels`) lived in
the process-global state bus, so two browser tabs on two workspaces clobbered
each other — the losing tab mirrored the winner's models and persisted them onto
its own workspace (see docs/API_CONTRACT.md §"Workspace scoping on the state
bus"). The bug is fixed, but workspaces corrupted before the fix still carry the
wrong models on disk.

The chats themselves are intact, and they know who generated them: every
assistant node's `raw_meta` blob records the `sampler_path` / `base_model` /
`openrouter_model` actually sampled. So the true layout is recoverable —
that is what this script does.

Method. For each panel, look only at nodes UNIQUE to that panel: trees get cloned
across panels (add-panel duplicates the first panel's tree, send-branch-to-panel
grafts), and a cloned node names the model that produced it, not the panel's own.
The unique nodes' modal model is the panel's evidence. A panel whose unique nodes
disagree is reported, never auto-repaired.

    # audit every workspace of the running instance (read-only)
    uv run python scripts/repair_panel_layouts.py

    # repair, backing up each body first
    uv run python scripts/repair_panel_layouts.py --apply

    uv run python scripts/repair_panel_layouts.py --base-url http://127.0.0.1:8809
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SAMPLER_RE = re.compile(r'"sampler_path":\s*"([^"]+)"')
BASE_RE = re.compile(r'"base_model":\s*"([^"]+)"')
OR_RE = re.compile(r'"openrouter_model":\s*"([^"]+)"')


def _req(base: str, path: str, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"content-type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def sampler_index(base: str) -> dict[str, tuple[str, str]]:
    """tinker sampler_path -> (run_id, checkpoint name), from discovery."""
    return {
        c["sampler_path"]: (run["id"], c["name"])
        for run in _req(base, "/api/models")
        for c in run.get("checkpoints", [])
        if c.get("sampler_path")
    }


def model_of(raw_meta: str, index: dict[str, tuple[str, str]]) -> tuple[str, str | None] | None:
    """(run_id sentinel, checkpoint) actually used for this node, or None if the
    blob doesn't say (older samples, or a node with no raw_meta)."""
    if not isinstance(raw_meta, str):
        return None
    if (m := SAMPLER_RE.search(raw_meta)):
        if m.group(1) in index:
            return index[m.group(1)]
        return (f"ckpt:{m.group(1)}", None)  # loose checkpoint, no discovered run
    if (m := OR_RE.search(raw_meta)):
        return (f"openrouter:{m.group(1)}", None)
    if (m := BASE_RE.search(raw_meta)):
        return (f"base:{m.group(1)}", None)
    return None


def evidence_for(base: str, conv: dict, index) -> dict[str, tuple[tuple[str, str | None], int, int]]:
    """{panel_id: (model, votes, distinct_models)} from each panel's UNIQUE nodes."""
    trees = conv.get("trees") or {}
    if not trees:
        return {}
    ids = {pid: set(t.get("nodes") or {}) for pid, t in trees.items()}
    wanted: list[str] = []
    unique: dict[str, list[str]] = {}
    for pid, t in trees.items():
        others = set().union(*[v for k, v in ids.items() if k != pid]) if len(ids) > 1 else set()
        own = [n for n in ids[pid] - others
               if (t["nodes"][n].get("role") == "assistant" and t["nodes"][n].get("has_raw_meta"))]
        unique[pid] = own
        wanted += own
    if not wanted:
        return {}
    blobs = _req(base, f"/api/workspaces/{conv['id']}/node-blobs", {"nodes": wanted}, "POST")
    out = {}
    for pid, nodes in unique.items():
        votes = Counter()
        for n in nodes:
            if (m := model_of((blobs.get(n) or {}).get("raw_meta") or "", index)):
                votes[m] += 1
        if votes:
            model, count = votes.most_common(1)[0]
            out[pid] = (model, count, len(votes))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:8767", help="running tinkerscope instance")
    ap.add_argument("--apply", action="store_true", help="write the repaired layouts (default: audit only)")
    ap.add_argument("--backup-dir", default=None, help="where to save bodies before --apply (default: ./layout-backups)")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    try:
        index = sampler_index(base)
    except urllib.error.URLError as e:
        print(f"cannot reach {base}: {e}", file=sys.stderr)
        return 2
    convs = _req(base, "/api/workspaces?bodies=1")
    print(f"{len(convs)} workspaces, {len(index)} sampler paths indexed\n")

    repairs: list[tuple[dict, list[dict], list[str]]] = []
    ambiguous = 0
    for conv in convs:
        stored = {p["id"]: p for p in (conv.get("panels") or [])}
        if not stored:
            continue  # legacy workspace with no stored layout — nothing to compare
        ev = evidence_for(base, conv, index)
        lines, layout, changed = [], [], False
        for pid, p in stored.items():
            got = ev.get(pid)
            # A panel whose own nodes name SEVERAL models was genuinely re-pointed
            # mid-workspace; picking "the right one" would need an ordering the tree
            # doesn't carry. Report, never guess.
            if got and got[2] > 1:
                lines.append(f"    {pid:8s} AMBIGUOUS — {got[2]} distinct models in its own nodes; left alone")
                ambiguous += 1
                layout.append(p)
                continue
            if got and got[0][0] != p.get("run_id"):
                (run_id, ckpt), votes, _ = got
                lines.append(f"    {pid:8s} {p.get('run_id')}  ->  {run_id} ({votes} nodes)")
                layout.append({"id": pid, "run_id": run_id, "checkpoint": ckpt})
                changed = True
            else:
                layout.append(p)
        # panels whose trees exist but that fell out of the stored layout entirely
        for pid, (model, votes, distinct) in ev.items():
            if pid in stored or distinct > 1:
                continue
            lines.append(f"    {pid:8s} MISSING from layout  ->  {model[0]} ({votes} nodes)")
            layout.append({"id": pid, "run_id": model[0], "checkpoint": model[1]})
            changed = True
        if changed:
            print(f"⚠ {conv.get('name')!r}  {conv['id']}")
            print("\n".join(lines))
            repairs.append((conv, layout, lines))

    if not repairs:
        print("all workspace layouts agree with their chats' evidence ✓")
        return 0
    print(f"\n{len(repairs)} workspace(s) need repair" + (f", {ambiguous} ambiguous panel(s) skipped" if ambiguous else ""))
    if not args.apply:
        print("(audit only — re-run with --apply to write these)")
        return 1

    out = Path(args.backup_dir or "layout-backups") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    for conv, layout, _ in repairs:
        (out / f"{conv['id']}.json").write_text(json.dumps(conv, indent=2))
        _req(base, f"/api/workspaces/{conv['id']}", {"panels": layout}, "PATCH")
        print(f"repaired {conv.get('name')!r}")
    print(f"\nbodies backed up under {out}/")
    print("NOTE: reload any open browser tab — a tab still holding the old layout may save it back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
