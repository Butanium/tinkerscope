"""Instance registry: which tinkerscope servers are running, and where.

Servers register themselves in `~/.local/state/tinkerscope/instances.json`
on startup and unregister on exit. Entries whose pid is gone are pruned
lazily on every read, so a SIGKILL'd server doesn't poison discovery.

The `tinkpg` CLI uses `discover(cwd)` to pick the instance whose scan root
contains the current directory — so `tinkpg ls` works from inside any project
with discovered runs and zero configuration.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from .paths import INSTANCES_PATH, STATE_HOME


class DiscoveryError(RuntimeError):
    """No unambiguous running instance for the caller's cwd."""


@dataclass
class Instance:
    pid: int
    host: str
    port: int
    scan_roots: list[str]
    started_at: float

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def describe(self) -> str:
        roots = ", ".join(self.scan_roots)
        return f"{self.base_url} (pid {self.pid}, scanning: {roots})"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def _locked() -> Iterator[None]:
    """Serialize read-modify-write cycles across processes via flock."""
    STATE_HOME.mkdir(parents=True, exist_ok=True)
    lock = STATE_HOME / "instances.lock"
    with lock.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _read() -> list[Instance]:
    if not INSTANCES_PATH.exists():
        return []
    try:
        raw = json.loads(INSTANCES_PATH.read_text())
    except json.JSONDecodeError:
        return []
    return [Instance(**e) for e in raw]


def _write(instances: list[Instance]) -> None:
    tmp = INSTANCES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([asdict(i) for i in instances], indent=2))
    tmp.replace(INSTANCES_PATH)


def register(host: str, port: int, scan_roots: tuple[Path, ...]) -> None:
    """Add (or refresh) this process's entry; prune dead ones while we're here."""
    me = Instance(
        pid=os.getpid(),
        host=host,
        port=port,
        scan_roots=[str(r) for r in scan_roots],
        started_at=time.time(),
    )
    with _locked():
        kept = [i for i in _read() if _pid_alive(i.pid) and i.pid != me.pid]
        _write(kept + [me])


def unregister(pid: int | None = None) -> None:
    pid = pid if pid is not None else os.getpid()
    with _locked():
        _write([i for i in _read() if i.pid != pid and _pid_alive(i.pid)])


def list_instances() -> list[Instance]:
    """All registered instances whose process is still alive (pruned in place)."""
    with _locked():
        all_ = _read()
        alive = [i for i in all_ if _pid_alive(i.pid)]
        if len(alive) != len(all_):
            _write(alive)
    return alive


def discover(cwd: Path) -> Instance:
    """Pick the running instance responsible for `cwd`.

    Rules:
      - instances whose scan root contains cwd are candidates; the deepest
        containing root wins, so nested scopes resolve to the closer server;
      - no candidate but exactly one instance running → use it (the common
        single-server case shouldn't require launching from inside the dir);
      - otherwise → DiscoveryError listing what *is* running.
    """
    cwd = cwd.resolve()
    alive = list_instances()

    def depth_of_best_root(inst: Instance) -> int:
        best = -1
        for r in inst.scan_roots:
            root = Path(r)
            if cwd == root or root in cwd.parents:
                best = max(best, len(root.parts))
        return best

    scored = [(depth_of_best_root(i), i) for i in alive]
    candidates = [(d, i) for d, i in scored if d >= 0]
    if candidates:
        top = max(d for d, _ in candidates)
        winners = [i for d, i in candidates if d == top]
        if len(winners) == 1:
            return winners[0]
        listing = "\n".join(f"  - {i.describe()}" for i in winners)
        raise DiscoveryError(
            f"multiple tinkerscope instances scan {cwd}:\n{listing}\n"
            "Disambiguate with TINKERSCOPE_BASE_URL or --base-url."
        )
    if len(alive) == 1:
        return alive[0]
    if not alive:
        raise DiscoveryError(
            "no running tinkerscope instance found "
            f"(registry: {INSTANCES_PATH}). Start one with: tinkerscope [DIR]"
        )
    listing = "\n".join(f"  - {i.describe()}" for i in alive)
    raise DiscoveryError(
        f"no tinkerscope instance scans {cwd}; running instances:\n{listing}\n"
        "cd into a scanned directory, or set TINKERSCOPE_BASE_URL / --base-url."
    )
