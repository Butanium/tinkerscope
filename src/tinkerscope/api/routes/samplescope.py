"""Hand a run's training dataset off to samplescope.

tinkerscope shows what a checkpoint SAYS; samplescope (`sscope`, same box) shows
what it was TRAINED ON. Ctrl+⇧ on a panel's copy button lands here: resolve the
run's training JSONL, find — or start — a samplescope instance that can serve it,
and hand back a URL the browser opens in a new tab.

Two integration points, both things samplescope's own CLI relies on, so neither is
a private detail we reverse-engineered:

  - its instance registry, `~/.local/state/samplescope/instances.json`
    (`{pid, host, port, scan_roots, started_at}`) — what `sscope view`'s
    auto-targeting reads, and what its `test_discovery.py` asserts against;
  - `GET /api/health` → `{root}` — the serving root that every dataset path in
    its API and its `?path=` URL param is relative to. Asked rather than
    recomputed: `root` is `commonpath(scan_roots)` today, and duplicating that
    derivation here would silently rot if it ever changed.

We deliberately do NOT shell out to `sscope view open`. That mutates the shared
view over HTTP as a side effect of *preparing* a link, so a failure halfway
leaves someone's screen moved with no tab to show for it. `?path=` says the same
thing declaratively and samplescope's own UrlSyncBridge performs the open on
mount. (It does still move any samplescope tab already on another dataset — the
view is server-side and shared by design. That's samplescope's model, not
something this endpoint can opt out of.)

⚠️ The request carries a RUN ID, never a path. Any page the user visits can POST
to localhost, and "start a server over a directory of your choosing" is not a
capability to hand out; resolving the dataset from our own catalog bounds the
served tree to one tinkerscope already scans.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import discovery
from ..settings import SETTINGS

router = APIRouter(prefix="/api/samplescope", tags=["samplescope"])

# Same resolution samplescope's paths.py does, so we read the file it writes.
_STATE_HOME = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
INSTANCES_PATH = _STATE_HOME / "samplescope" / "instances.json"

STARTUP_TIMEOUT_S = 25.0  # `sscope serve` imports duckdb + pyarrow; 25s is generous


class OpenRequest(BaseModel):
    run_id: str


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, TypeError):
        return False
    return True


def _instances() -> list[dict]:
    """Live entries from samplescope's registry. Missing/corrupt file ⇒ none —
    the caller's next move is to start one, which is also the answer here."""
    try:
        raw = json.loads(INSTANCES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [i for i in raw if isinstance(i, dict) and _alive(i.get("pid", -1))]


def _serving_root(inst: dict) -> tuple[str, Path] | None:
    """`(base_url, root)` for an instance that answers, else None. Doubles as the
    liveness check the pid alone can't give: a registered pid can be a process
    that has stopped serving."""
    base = f"http://{inst.get('host', '127.0.0.1')}:{inst['port']}"
    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=2.5) as r:
            body = json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        return None
    root = body.get("root")
    return (base, Path(root)) if root else None


def _covers(inst: dict, target: Path) -> bool:
    for r in inst.get("scan_roots") or []:
        root = Path(r)
        if target == root or root in target.parents:
            return True
    return False


def _best_instance(target: Path) -> dict | None:
    """The instance whose scan root sits DEEPEST above the file — samplescope's own
    `discover()` tie-break, so we pick the same one `sscope view` would."""
    covering = [i for i in _instances() if _covers(i, target)]
    if not covering:
        return None

    def depth(inst: dict) -> int:
        return max(
            (len(Path(r).parts) for r in inst["scan_roots"] if _covers({"scan_roots": [r]}, target)),
            default=0,
        )

    return max(covering, key=depth)


def _spawn(root: Path) -> None:
    """Start `sscope serve <root>` detached and leave it running.

    Detached on purpose: samplescope stays in the foreground and unregisters from
    its registry on exit, so tying it to tinkerscope's process group would kill a
    viewer the user is still reading the moment tinkerscope restarts. It outlives
    us, and its own registry is how it gets found (or reused) next time.

    Serving the SCAN ROOT rather than the dataset's own directory: one instance
    then covers every run tinkerscope discovered, instead of one per training
    file, and it matches what `sscope <project>` by hand would have produced.
    """
    exe = shutil.which("sscope")
    if not exe:
        raise HTTPException(
            503,
            "samplescope is not installed (no `sscope` on PATH) — "
            "`uv tool install samplescope`, or open the copied path yourself",
        )
    log = SETTINGS.state_dir / "samplescope-spawn.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(  # noqa: S603 — fixed argv, root is one of our own scan roots
        [exe, "serve", str(root)],
        stdout=log.open("a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=str(root),
    )


def _wait_for(target: Path, deadline: float) -> tuple[str, Path] | None:
    while time.time() < deadline:
        inst = _best_instance(target)
        if inst and (found := _serving_root(inst)):
            return found
        time.sleep(0.4)
    return None


@router.post("/open")
def open_in_samplescope(req: OpenRequest) -> dict:
    """Resolve a run's training dataset to a samplescope deep link, starting an
    instance if nothing serves it yet. `started` tells the UI whether it should
    expect a cold first paint."""
    run = discovery.find_run(req.run_id)
    if run is None:
        raise HTTPException(404, f"no run {req.run_id}")
    if not run.dataset_path:
        raise HTTPException(400, f"{run.name} records no training dataset in its config.json")

    target = Path(run.dataset_path)
    if not target.is_absolute() or not target.exists():
        # config recorded a project-relative path we couldn't resolve on disk.
        raise HTTPException(
            404, f"training dataset not found on this box: {run.dataset_path}"
        )

    started = False
    found = None
    if inst := _best_instance(target):
        found = _serving_root(inst)
    if found is None:
        roots = [r for r in SETTINGS.scan_roots if r == target or r in target.parents]
        if not roots:
            raise HTTPException(
                409,
                f"no samplescope instance serves {target} and it sits outside our scan "
                f"roots — start one yourself: sscope {target.parent}",
            )
        _spawn(max(roots, key=lambda r: len(r.parts)))
        started = True
        found = _wait_for(target, time.time() + STARTUP_TIMEOUT_S)
        if found is None:
            raise HTTPException(
                504,
                "started samplescope but it did not come up in time — see "
                f"{SETTINGS.state_dir / 'samplescope-spawn.log'}",
            )

    base, root = found
    try:
        rel = target.relative_to(root)
    except ValueError:
        raise HTTPException(
            409, f"samplescope at {base} serves {root}, which does not contain {target}"
        )
    return {"url": f"{base}/?path={quote(str(rel))}", "started": started, "base_url": base}
