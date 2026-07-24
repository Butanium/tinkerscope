#!/usr/bin/env python3
"""Browse / restore a workspace's past panel layouts.

Why this exists: a workspace's panel layout (which model sits in which column) is
overwritten in place on every change, so before 2026-07-24 a layout accident was
only recoverable by forensics over the node blobs — `scripts/repair_panel_layouts.py`,
written after a real incident that clobbered four live workspaces. The server now
appends every layout CHANGE to `<state>/workspaces/<id>.layouts.jsonl`, and this
script is the front end for it: see what a workspace used to show, and put it back.

The two scripts answer different questions. Use THIS one first — it is exact
(what was actually stored, and when). Fall back to `repair_panel_layouts.py` when
the damage predates the history, since that one reconstructs the layout from
evidence rather than reading it.

    # workspaces that have any recorded layout change
    uv run python scripts/layout_history.py

    # one workspace's history, newest last
    uv run python scripts/layout_history.py <workspace-id-or-name-substring>

    # put entry #2 back (asks first; --yes to skip)
    uv run python scripts/layout_history.py <workspace> --restore 2

    uv run python scripts/layout_history.py --base-url http://127.0.0.1:8809
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def _req(base: str, path: str, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"content-type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def _panel_line(p: dict) -> str:
    run = p.get("run_id") or "—"
    ckpt = p.get("checkpoint")
    return f"{p.get('id', '?')}: {run}" + (f"@{ckpt}" if ckpt else "")


def _fmt_layout(panels: list[dict]) -> str:
    if not panels:
        return "(no panels)"
    return "\n".join(f"      {_panel_line(p)}" for p in panels)


def _resolve(base: str, sel: str) -> dict:
    """A workspace by exact id, else a unique case-insensitive name substring."""
    convs = _req(base, "/api/workspaces")
    for c in convs:
        if c.get("id") == sel:
            return c
    hits = [c for c in convs if sel.lower() in (c.get("name") or "").lower()]
    if not hits:
        sys.exit(f"no workspace matching {sel!r} (have: {', '.join(c.get('name') or '?' for c in convs)})")
    if len(hits) > 1:
        sys.exit(f"{sel!r} is ambiguous: {', '.join(c.get('name') or '?' for c in hits)}")
    return hits[0]


def cmd_list(base: str) -> None:
    convs = _req(base, "/api/workspaces")
    any_history = False
    for c in convs:
        hist = _req(base, f"/api/workspaces/{c['id']}/layout-history")
        if not hist:
            continue
        any_history = True
        n_panels = len(c.get("panels") or [])
        print(f"{c.get('name') or 'Untitled'}  [{c['id']}]")
        print(f"    {len(hist)} recorded layout change(s), now {n_panels} panel(s); "
              f"latest {hist[-1].get('ts')}")
    if not any_history:
        print("No workspace has a recorded layout change yet.")
        print("(History starts accruing on the first layout change AFTER the server "
              "with this feature came up — it is not backfilled.)")


def cmd_show(base: str, conv: dict) -> list[dict]:
    hist = _req(base, f"/api/workspaces/{conv['id']}/layout-history")
    print(f"{conv.get('name') or 'Untitled'}  [{conv['id']}]")
    if not hist:
        print("  no recorded layout changes")
        return hist
    for i, entry in enumerate(hist):
        print(f"  #{i}  {entry.get('ts')}")
        print(_fmt_layout(entry.get("panels") or []))
    print("  CURRENT")
    print(_fmt_layout(conv.get("panels") or []))
    return hist


def cmd_restore(base: str, conv: dict, hist: list[dict], index: int, assume_yes: bool) -> None:
    if not hist:
        sys.exit("nothing to restore — no recorded layout changes")
    if not -len(hist) <= index < len(hist):
        sys.exit(f"--restore {index} out of range (have #0..#{len(hist) - 1})")
    panels = hist[index].get("panels") or []
    print(f"\nrestore #{index} ({hist[index].get('ts')}) onto {conv.get('name')!r}:")
    print(_fmt_layout(panels))
    if not assume_yes:
        if input("\nproceed? [y/N] ").strip().lower() not in ("y", "yes"):
            sys.exit("aborted")
    _req(base, f"/api/workspaces/{conv['id']}", {"panels": panels}, method="PATCH")
    print("restored. Reload the browser tab (it holds its own copy of the layout).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workspace", nargs="?", help="workspace id, or a unique name substring")
    ap.add_argument("--restore", type=int, metavar="N", help="restore history entry #N (negative counts from the end)")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--base-url", default="http://127.0.0.1:8767")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    try:
        if not args.workspace:
            if args.restore is not None:
                sys.exit("--restore needs a workspace")
            cmd_list(base)
            return
        conv = _resolve(base, args.workspace)
        hist = cmd_show(base, conv)
        if args.restore is not None:
            cmd_restore(base, conv, hist, args.restore, args.yes)
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach {base}: {e}. Is the instance running? (--base-url)")


main()
