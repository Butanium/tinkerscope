"""Storage-v2 migration against a READ-ONLY SNAPSHOT of a real conversations.json.

This is the highest-value migration evidence: it exercises the actual on-disk shapes
(heavy token_logprobs/raw_meta blobs, pre-multipanel {tree,compare_tree} convs) that
synthetic fixtures can't. It NEVER touches the source: the legacy file is copied
read-only into a throwaway XDG_STATE_HOME and the migration runs entirely on the copy
(a guard refuses to run if the working state dir resolves under the real state home).

What it checks: timing + peak RSS; the split output shapes; an INDEPENDENT from-disk
re-materialization deep-compared to the legacy bytes (catches disk-serialization bugs
the in-memory verify can't); a second boot is an idempotent no-op; and — the clincher
for the legacy-shape convs — a primary-only save_tree on a real {tree,compare_tree}
conversation preserves its compare tree into trees['compare'] with the legacy keys
healed.

Defaults to Clément's largest instance store, read-only. Point it elsewhere with
--src (a conversations.json) — e.g. a dev-isolated snapshot. Skips cleanly if the
source doesn't exist (fresh box / CI).

  uv run python tests/small-smokes/store_real_migration.py [--src PATH]

Lifted + merged from backend-review's probe_real_migration + probe_real_legacy_save.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

STATE_HOME = Path.home() / ".local/state/tinkerscope"


def _default_src() -> Path | None:
    """Largest */conversations.json under the real state home (the heaviest store is
    the most exacting migration test), or None if there is no real store to snapshot."""
    candidates = sorted(STATE_HOME.glob("*/conversations.json"),
                        key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
    return candidates[0] if candidates else None


ap = argparse.ArgumentParser()
ap.add_argument("--src", type=Path, default=_default_src(),
                help="legacy conversations.json to migrate (READ-ONLY; default: largest real store)")
args = ap.parse_args()

if args.src is None or not args.src.exists():
    print(f"SKIP: no source conversations.json (looked under {STATE_HOME}). "
          f"Pass --src PATH to run against a snapshot.")
    sys.exit(0)

probe = Path(tempfile.mkdtemp(prefix="store-real-mig-"))
os.environ["XDG_STATE_HOME"] = str(probe / "state")
os.environ["TINKERSCOPE_SCAN_ROOTS"] = str(probe / "runs")
(probe / "runs").mkdir(parents=True)

import tinkerscope.api.settings as settings_mod  # noqa: E402
import tinkerscope.paths as paths_mod  # noqa: E402

importlib.reload(paths_mod)
importlib.reload(settings_mod)
import tinkerscope.api.conversation_store as store  # noqa: E402

importlib.reload(store)

# SAFETY: the working state dir must be our throwaway, never anywhere under the real
# state home — so the in-place migration (which renames the legacy file) can't touch
# live data no matter what env leaked in.
state_dir = store._state_dir()
assert str(state_dir).startswith(str(probe)), f"working dir escaped the sandbox: {state_dir}"
assert not str(state_dir).startswith(str(STATE_HOME)), "refusing to operate inside the real state home"

dst = store._legacy_path()
dst.parent.mkdir(parents=True, exist_ok=True)
t0 = time.time()
shutil.copyfile(args.src, dst)  # READ-ONLY on the source
src_mb = dst.stat().st_size / 1e6
print(f"snapshot: copied {src_mb:.0f} MB ({args.src}) → sandbox in {time.time() - t0:.1f}s", flush=True)


def rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


t0 = time.time()
store.boot()
print(f"boot (migrate + cache build): {time.time() - t0:.1f}s, peak RSS {rss_gb():.2f} GB", flush=True)
assert not dst.exists() and dst.with_suffix(".json.legacy").exists(), "legacy not renamed"

summaries = store.list_summaries()
n_blob_files = sum(1 for _ in store._convs_dir().glob("*.blobs/*.json"))
total_light = sum(f.stat().st_size for f in store._convs_dir().glob("*.json"))
total_blobs = sum(f.stat().st_size for f in store._convs_dir().glob("*.blobs/*.json"))
print(f"{len(summaries)} conversations migrated; light {total_light/1e6:.1f} MB, "
      f"{n_blob_files} blobs {total_blobs/1e6:.1f} MB (legacy {src_mb:.0f} MB)")

legacy_shape = [s["id"] for s in summaries if "trees" not in (store.get_body(s["id"]) or {})]
print(f"legacy-shape (no `trees` key): {len(legacy_shape)} → {[c[:8] for c in legacy_shape]}")

# ── independent FROM-DISK re-materialization vs the legacy bytes ────────────────
print("from-disk re-materialization vs legacy bytes…", flush=True)
t0 = time.time()
legacy_items = json.loads(dst.with_suffix(".json.legacy").read_text())
by_id = {c["id"]: c for c in legacy_items}
assert len(by_id) == len(summaries), (len(by_id), len(summaries))
store.reset_cache()  # force re-reads from disk, not the boot-time cache
mismatches = []
for s in summaries:
    light = store.get_body(s["id"])
    trees_iter = list((light.get("trees") or {}).values())
    trees_iter += [light[k] for k in ("tree", "compare_tree") if k in light]
    node_ids = [nid for t in trees_iter if isinstance(t, dict) for nid in (t.get("nodes") or {})]
    if store.materialize_conv(light, store.get_blobs(s["id"], node_ids)) != by_id[s["id"]]:
        mismatches.append(s["id"])
print(f"disk verify done in {time.time() - t0:.1f}s; mismatches: {mismatches}")
assert not mismatches, mismatches

# ── second boot: idempotent no-op ──────────────────────────────────────────────
store2 = importlib.reload(store)
t0 = time.time()
store2.boot()
print(f"second boot (no migration, cache rebuild only): {time.time() - t0:.1f}s")
assert len(store2.list_summaries()) == len(summaries)

# ── legacy first-save: a primary-only save preserves the compare tree ──────────
if legacy_shape:
    cid = legacy_shape[0]
    before = store2.get_body(cid)
    assert "trees" not in before and ("tree" in before or "compare_tree" in before)
    compare_before = json.loads(json.dumps(before.get("compare_tree")))  # deep copy (may be None)
    ok = store2.save_tree(cid, trees_partial={"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
                          dropped_trees=[], system_prompt=before.get("system_prompt"),
                          panels=[], reduced_panels=[], send_targets=[], seen_panels=[])
    assert ok
    after = store2.get_body(cid)
    assert "tree" not in after and "compare_tree" not in after, "legacy keys not healed"
    if compare_before:
        assert after["trees"]["compare"] == compare_before, "compare tree changed/lost on first save!"
        n = len(compare_before.get("nodes") or {})
        print(f"legacy conv {cid[:8]} primary-only save: compare tree survived ({n} nodes) — OK")
    else:
        print(f"legacy conv {cid[:8]} primary-only save: no compare tree to preserve — OK")
else:
    print("no legacy-shape conversation in this store — skipping the first-save check")

print(f"final peak RSS {rss_gb():.2f} GB")
print("REAL-STORE MIGRATION SMOKE: ALL GOOD")
