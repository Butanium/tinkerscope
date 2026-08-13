"""Server launch: serve the API + built web UI from one process.

Since the CLI unification (2026-08-12) the entry point is the ONE Typer app in
`cli.py` — `tinkerscope [DIR ...] [--port N]` routes here through its `serve`
command (bare-dir shorthand via `_DefaultToServe`), and `cli.py` imports this
module LAZILY so uvicorn stays off the hot path of every driver-verb
invocation. The old `_pack_command`/`_site_command` argparse handlers became
Typer sub-apps (`cli.py`) whose bodies live in `publish_cli.py`.

Scan roots default to the current directory. Args are translated into the
`TINKERSCOPE_*` env vars BEFORE the app module is imported, so the settings
module (and any uvicorn --reload subprocess) sees a consistent config.
"""
from __future__ import annotations

import atexit
import os
import socket
import sys
from pathlib import Path

import uvicorn

from . import instances

DEFAULT_PORT = 8765
PORT_SCAN_SPAN = 100


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _pick_port(host: str, requested: int | None) -> int:
    """Honor an explicit --port (fail loudly if taken); otherwise scan upward
    from the default so multiple instances coexist without flags."""
    if requested is not None:
        if not _port_free(host, requested):
            sys.exit(f"port {requested} is already in use on {host}")
        return requested
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN_SPAN):
        if _port_free(host, port):
            return port
    sys.exit(f"no free port in {DEFAULT_PORT}-{DEFAULT_PORT + PORT_SCAN_SPAN - 1} on {host}")


def run_server(
    dirs: list[Path] | None,
    *,
    host: str,
    port: int | None,
    reload: bool = False,
    pack: str | None = None,
    force: bool = False,
    reseed: bool = False,
) -> None:
    """The old `tinkerscope` main(), minus argparse (that's `cli.py`'s now)."""
    if dirs:
        resolved = [d.expanduser().resolve() for d in dirs]
    elif os.environ.get("TINKERSCOPE_SCAN_ROOTS"):
        resolved = [
            Path(p).expanduser().resolve()
            for p in os.environ["TINKERSCOPE_SCAN_ROOTS"].split(":")
            if p
        ]
    else:
        resolved = [Path.cwd()]
    for d in resolved:
        if not d.is_dir():
            sys.exit(f"not a directory: {d}")

    # Set the scan roots in the env NOW (before apply / the app import) so both the
    # pack apply and the served app resolve the same per-set state dir.
    os.environ["TINKERSCOPE_SCAN_ROOTS"] = ":".join(str(d) for d in resolved)

    if pack:
        from . import pack as packmod

        p = packmod.load_pack(pack)
        s = packmod.apply_pack(p, force=force, reseed=reseed)
        print(f"applied pack '{s['pack']}': {s['models']} model(s), {s['openrouter']} openrouter, "
              f"{s['workspaces']} workspace(s), default params {s['params']}")

    # Same scan-root set ⇒ same per-set state (highlights, prefs) and the same
    # discovered runs. A second server would just duplicate; be idempotent.
    existing = [
        i for i in instances.list_instances()
        if sorted(i.scan_roots) == sorted(str(d) for d in resolved)
    ]
    if existing:
        print(f"already serving these directories: {existing[0].base_url} (pid {existing[0].pid})")
        if pack:
            print("  note: a running instance won't show newly-installed workspaces until restarted")
        return

    env_port = os.environ.get("TINKERSCOPE_PORT")
    requested = port if port is not None else (int(env_port) if env_port else None)
    bound = _pick_port(host, requested)

    # The app module reads these at import time (incl. in --reload children).
    # TINKERSCOPE_SCAN_ROOTS was already set above (before the pack apply).
    os.environ["TINKERSCOPE_HOST"] = host
    os.environ["TINKERSCOPE_PORT"] = str(bound)

    instances.register(host, bound, tuple(resolved))
    # finally + atexit both fire on graceful paths (idempotent); a SIGKILL'd
    # entry is pruned lazily by the next registry read.
    atexit.register(instances.unregister)
    print(f"tinkerscope serving {', '.join(str(d) for d in resolved)}")
    print(f"  → http://{host}:{bound}")
    try:
        uvicorn.run(
            "tinkerscope.api.main:app",
            host=host,
            port=bound,
            reload=reload,
        )
    finally:
        instances.unregister()
