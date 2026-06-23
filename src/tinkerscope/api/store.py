"""Tiny atomic JSON file store for per-scan-root-set state (highlights, prefs)."""
from __future__ import annotations

import fcntl
import json
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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


@contextmanager
def locked(name: str) -> Iterator[None]:
    """Serialize a read-modify-write cycle across processes/tabs via flock.

    `name` keys a dedicated lock file under STATE_HOME (e.g. "conversations" ->
    conversations.lock). Mirrors instances._locked; use it to wrap any
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
