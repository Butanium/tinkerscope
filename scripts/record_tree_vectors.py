#!/usr/bin/env -S uv run python
"""Re-record the `broadcast` field of every shared tree-op fixture vector.

The vectors' `tree_after` is HAND-AUTHORED — it is the oracle, and recording it
from the engine it polices would only pin what that engine does today. The
`broadcast` field is the one part that is legitimately recorded, because it is a
derived artifact with its own oracle: both engines assert that replaying the
recorded broadcast ONE OP AT A TIME reproduces the hand-authored `tree_after`,
which is exactly what a production mirror does. A wrong broadcast therefore can't
pass by being recorded — the replay diverges from the hand-authored tree.

Run after a DELIBERATE change to what `/ops` broadcasts; the diff is then the
wire-contract change, reviewable as such. If a run produces a diff you did not
intend, that is the signal — not a nuisance to re-record away.

    uv run scripts/record_tree_vectors.py [--check]

`--check` records nothing and exits non-zero if any file is stale (CI-shaped).
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tinkerscope.api.tree_ops import OpError, apply_ops  # noqa: E402

VECTOR_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tree_vectors"


def _trees_of(vector: dict) -> dict:
    ops = vector["ops"] if "ops" in vector else [vector["op"]]
    if "tree_before" in vector:
        panel = ops[0].get("panel") or ops[0].get("from_panel")
        return {panel: vector["tree_before"]}
    return vector["trees_before"]


def record(vector: dict) -> dict | None:
    """The broadcast this vector's ops produce, or None for a reject vector
    (a rejected batch broadcasts nothing at all)."""
    if "rejects" in vector:
        return None
    ops = vector["ops"] if "ops" in vector else [vector["op"]]
    body = {"id": "w1", "trees": copy.deepcopy(_trees_of(vector))}
    try:
        return apply_ops(body, ops).wire_ops
    except OpError as e:  # a vector that rejects without saying so
        raise SystemExit(f"{vector.get('name')!r}: ops were rejected ({e}) but no `rejects` key") from e


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="report staleness, write nothing")
    args = ap.parse_args()

    stale: list[str] = []
    for path in sorted(VECTOR_DIR.glob("*.json")):
        vector = json.loads(path.read_text())
        fresh = record(vector)
        current = vector.get("broadcast")
        if fresh is None:
            if "broadcast" in vector:
                stale.append(path.name)
                if not args.check:
                    vector.pop("broadcast")
                    path.write_text(json.dumps(vector, indent=2, ensure_ascii=False) + "\n")
            continue
        if current == fresh:
            continue
        stale.append(path.name)
        if not args.check:
            vector["broadcast"] = fresh
            path.write_text(json.dumps(vector, indent=2, ensure_ascii=False) + "\n")

    if not stale:
        print(f"{len(list(VECTOR_DIR.glob('*.json')))} vectors, all broadcasts current")
        return 0
    verb = "stale" if args.check else "re-recorded"
    print(f"{verb}: " + ", ".join(stale))
    return 1 if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
