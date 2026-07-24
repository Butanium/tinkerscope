"""Tiny atomic JSON file store for per-scan-root-set state (highlights, prefs)."""
from __future__ import annotations

import fcntl
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def write_json(path: Path, data: Any) -> None:
    """Atomic write via a PER-WRITER temp file + rename.

    The temp name must be unique: two browser tabs both PUT /api/prefs on load,
    FastAPI runs the sync handlers on different threadpool workers, and with a
    single shared `<name>.tmp` the first rename pulls the file out from under the
    second — `FileNotFoundError` on `tmp.replace(path)`, a 500 for one of the tabs
    (caught by tests/small-smokes/browser_two_tab_workspace.py). Both writers now
    stage their own file and the rename is last-writer-wins, which is what the
    callers already expect (`locked()` is what prevents lost updates)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)  # a failed write must not leave litter behind


@contextmanager
def locked(name: str) -> Iterator[None]:
    """Serialize a read-modify-write cycle across processes/tabs via flock.

    `name` keys a dedicated lock file under STATE_HOME (e.g. "workspaces" ->
    workspaces.lock). Mirrors instances._locked; use it to wrap any
    read_json -> mutate -> write_json sequence that concurrent writers (two
    browser tabs, a tab + the tinkpg CLI) could otherwise clobber — write_json's
    atomic rename prevents torn files but NOT lost updates.
    """
    # Imported lazily so a test that reloads paths.py (new XDG_STATE_HOME) gets
    # the current value rather than a binding frozen at this module's import.
    from ..paths import STATE_HOME

    STATE_HOME.mkdir(parents=True, exist_ok=True)
    lock = STATE_HOME / f"{name}.lock"
    with lock.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
