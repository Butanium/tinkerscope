"""Threaded concurrency storm for the storage-v2 store — PUT tree / PATCH / DELETE /
blob reads / list, all at once, from many threads.

Looks for: exceptions on any path, lost updates, cache serving deleted or stale
bodies, and the dict-mutation-during-iteration crash in list_summaries that the
_CACHE_LOCK closes. 100% synthetic (temp dir, tiny conversations) — no server, no
network. Re-run whenever the store's locking / caching changes.

Lifted from backend-review's storage-v2 probe suite.

  uv run python tests/small-smokes/store_concurrency.py
"""
from __future__ import annotations

import importlib
import json
import os
import random
import sys
import tempfile
import threading
import traceback
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="probe-conc-"))
os.environ["XDG_STATE_HOME"] = str(tmp / "state")
os.environ["TINKERSCOPE_SCAN_ROOTS"] = str(tmp / "runs")
(tmp / "runs").mkdir(parents=True)
import tinkerscope.api.settings as settings_mod  # noqa: E402
import tinkerscope.paths as paths_mod  # noqa: E402

importlib.reload(paths_mod)
importlib.reload(settings_mod)
import tinkerscope.api.conversation_store as store  # noqa: E402

importlib.reload(store)
store.boot()

N_CONV = 8
ROUNDS = 300
errors: list[str] = []
stop = threading.Event()

for i in range(N_CONV):
    store.upsert(id=f"conv{i}", name=f"c{i}", system_prompt=None,
                 trees={"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
                 panels=[], reduced_panels=[], send_targets=[], seen_panels=[])


def guard(fn):
    def run():
        try:
            fn()
        except Exception:
            errors.append(traceback.format_exc())
            stop.set()
    return run


@guard
def writer():
    for r in range(ROUNDS):
        if stop.is_set():
            return
        cid = f"conv{random.randrange(N_CONV)}"
        node = {"id": f"n{r}", "role": "assistant", "content": f"v{r}",
                "token_logprobs": [{"t": "x", "lp": -1.0}], "parent": None, "children": []}
        store.save_tree(cid, trees_partial={"primary": {"nodes": {node["id"]: node},
                        "rootChildren": [node["id"]], "selected": {}}},
                        dropped_trees=[], system_prompt=None, panels=[],
                        reduced_panels=[], send_targets=[], seen_panels=[])


@guard
def patcher():
    for r in range(ROUNDS):
        if stop.is_set():
            return
        store.patch_meta(f"conv{random.randrange(N_CONV)}", {"name": f"renamed{r}"})


@guard
def deleter_creator():
    for r in range(ROUNDS // 3):
        if stop.is_set():
            return
        cid = f"conv{random.randrange(N_CONV)}"
        store.delete(cid)
        store.upsert(id=cid, name="recreated", system_prompt=None,
                     trees={"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
                     panels=[], reduced_panels=[], send_targets=[], seen_panels=[])


@guard
def reader():
    for r in range(ROUNDS * 3):
        if stop.is_set():
            return
        store.list_summaries()
        cid = f"conv{random.randrange(N_CONV)}"
        body = store.get_body(cid)
        if body is not None and body.get("id") != cid:
            errors.append(f"body id mismatch: {body.get('id')} != {cid}")
        store.get_blobs(cid, [f"n{random.randrange(ROUNDS)}"])
        store.list_bodies()


threads = [threading.Thread(target=t) for t in
           (writer, writer, patcher, deleter_creator, reader, reader)]
for t in threads:
    t.start()
for t in threads:
    t.join()

if errors:
    print(f"{len(errors)} ERRORS:")
    print("\n---\n".join(errors[:5]))
    sys.exit(1)

# Post-storm coherence: the summary cache must exactly match what's on disk.
store_files = {f.stem for f in store._convs_dir().glob("*.json")}
cache_ids = {s["id"] for s in store.list_summaries()}
assert store_files == cache_ids, f"cache/disk divergence: {store_files ^ cache_ids}"
for cid in cache_ids:
    body = store.get_body(cid)
    disk = json.loads(store._conv_file(cid).read_text())
    assert body == disk, f"cached body != disk for {cid}"
print(f"concurrency storm clean; {len(cache_ids)} convs coherent cache/disk")
