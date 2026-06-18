"""`tinkerscope` entry point: serve the API + built web UI from one process.

Usage:
    tinkerscope [DIR ...] [--port PORT] [--host HOST] [--reload]

Scan roots default to the current directory. CLI args are translated into the
`TINKERSCOPE_*` env vars before the app module is imported, so the settings
module (and any uvicorn --reload subprocess) sees a consistent config.
"""
from __future__ import annotations

import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tinkerscope",
        description="Auto-discover Tinker training runs and sample their checkpoints in the browser.",
    )
    parser.add_argument(
        "dirs",
        nargs="*",
        type=Path,
        help="directories to scan for Tinker runs (default: cwd, or $TINKERSCOPE_SCAN_ROOTS)",
    )
    parser.add_argument("--port", type=int, default=None, help=f"port to bind (default: first free port from {DEFAULT_PORT})")
    parser.add_argument("--host", default=os.environ.get("TINKERSCOPE_HOST", "127.0.0.1"), help="host to bind (default: 127.0.0.1)")
    parser.add_argument("--reload", action="store_true", help="dev mode: auto-reload on source change")
    args = parser.parse_args()

    if args.dirs:
        dirs = [d.expanduser().resolve() for d in args.dirs]
    elif os.environ.get("TINKERSCOPE_SCAN_ROOTS"):
        dirs = [
            Path(p).expanduser().resolve()
            for p in os.environ["TINKERSCOPE_SCAN_ROOTS"].split(":")
            if p
        ]
    else:
        dirs = [Path.cwd()]
    for d in dirs:
        if not d.is_dir():
            sys.exit(f"not a directory: {d}")

    # Same scan-root set ⇒ same per-set state (highlights, prefs) and the same
    # discovered runs. A second server would just duplicate; be idempotent.
    existing = [
        i for i in instances.list_instances()
        if sorted(i.scan_roots) == sorted(str(d) for d in dirs)
    ]
    if existing:
        print(f"already serving these directories: {existing[0].base_url} (pid {existing[0].pid})")
        return

    env_port = os.environ.get("TINKERSCOPE_PORT")
    requested = args.port if args.port is not None else (int(env_port) if env_port else None)
    port = _pick_port(args.host, requested)

    # The app module reads these at import time (incl. in --reload children).
    os.environ["TINKERSCOPE_SCAN_ROOTS"] = ":".join(str(d) for d in dirs)
    os.environ["TINKERSCOPE_HOST"] = args.host
    os.environ["TINKERSCOPE_PORT"] = str(port)

    instances.register(args.host, port, tuple(dirs))
    # finally + atexit both fire on graceful paths (idempotent); a SIGKILL'd
    # entry is pruned lazily by the next registry read.
    atexit.register(instances.unregister)
    print(f"tinkerscope serving {', '.join(str(d) for d in dirs)}")
    print(f"  → http://{args.host}:{port}")
    try:
        uvicorn.run(
            "tinkerscope.api.main:app",
            host=args.host,
            port=port,
            reload=args.reload,
        )
    finally:
        instances.unregister()


if __name__ == "__main__":
    main()
