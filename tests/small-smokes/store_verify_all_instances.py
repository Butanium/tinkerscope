"""Read-only pre-flight: would EVERY real instance store migrate cleanly?

For each `<state-home>/*/conversations.json`, run the storage-v2 split + STRONG
re-materialize-and-deep-compare IN MEMORY (no writes, no boot, no server) and report
which stores would refuse migration at their next boot. Purely diagnostic — a green
run means every real store on this box round-trips byte-faithfully through the split.

READ-ONLY: never writes, never mutates, never renames anything. Safe against the live
state home (that's the point — it inspects the real stores without touching them).

  uv run python tests/small-smokes/store_verify_all_instances.py [--state-home DIR]

Lifted from backend-review's probe_all_instances.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tinkerscope.api.conversation_store import materialize_conv, split_conv

ap = argparse.ArgumentParser()
ap.add_argument("--state-home", type=Path, default=Path.home() / ".local/state/tinkerscope",
                help="state home to scan for */conversations.json (READ-ONLY)")
args = ap.parse_args()

files = sorted(args.state_home.glob("*/conversations.json"))
if not files:
    print(f"SKIP: no */conversations.json under {args.state_home}")
    raise SystemExit(0)

any_bad = False
for f in files:
    items = json.loads(f.read_text())
    bad: list[str] = []
    for conv in items:
        if not isinstance(conv, dict):
            bad.append(f"non-dict entry: {conv!r}")
            continue
        light, blobs = split_conv(conv)
        if materialize_conv(light, blobs) != conv:
            bad.append(f"verify mismatch: {conv.get('id')}")
    any_bad = any_bad or bool(bad)
    tag = "WOULD REFUSE" if bad else "ok"
    print(f"{f.parent.name}: {len(items)} conv(s) — {tag} {bad if bad else ''}")

raise SystemExit(1 if any_bad else 0)
