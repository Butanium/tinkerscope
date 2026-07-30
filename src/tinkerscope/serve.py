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


def _pack_command(argv: list[str]) -> None:
    """`tinkerscope pack <sub>` — author share packs from a live state dir."""
    parser = argparse.ArgumentParser(
        prog="tinkerscope pack",
        description="Author share packs (portable YAML bundles of checkpoints + params + workspaces).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="Export the current setup to a pack YAML file.")
    ex.add_argument("out", type=Path, help="output pack file (.yaml); if it exists, merges into it unless --overwrite")
    ex.add_argument("--dir", action="append", type=Path, default=None,
                    help="scan root(s) whose state to export (default: cwd) — must match how the instance was launched")
    ex.add_argument("--name", default=None, help="pack name (default: kept from an existing file, else the dir name)")
    ex.add_argument("--description", default=None)
    ex.add_argument("--models-from", choices=["panels", "workspaces", "all", "runs"], default="all",
                    help="where to gather models (default: all = current panels + workspaces + already-registered pack models)")
    ex.add_argument("--include-model", action="append", default=None, metavar="SUBSTR",
                    help="keep only models whose label/ref matches (repeatable)")
    ex.add_argument("--exclude-model", action="append", default=None, metavar="SUBSTR",
                    help="drop models whose label/ref matches (repeatable)")
    ex.add_argument("--no-workspaces", action="store_true", help="exclude saved workspaces")
    ex.add_argument("--no-defaults", action="store_true",
                    help="omit the defaults block (sampling params + default panel layout) from the pack")
    ex.add_argument("--workspace", action="append", default=None, metavar="NAME",
                    help="include only these workspaces by name (repeatable)")
    ex.add_argument("--overwrite", action="store_true",
                    help="regenerate from scratch instead of merging into an existing file")
    args = parser.parse_args(argv)
    if args.cmd == "export":
        _pack_export(args)


def _pack_export(args) -> None:
    dirs = [d.expanduser().resolve() for d in (args.dir or [Path.cwd()])]
    for d in dirs:
        if not d.is_dir():
            sys.exit(f"not a directory: {d}")
    # StateReader / discovery read SETTINGS.scan_roots (resolved from env at import).
    os.environ["TINKERSCOPE_SCAN_ROOTS"] = ":".join(str(d) for d in dirs)
    from . import pack as packmod

    existing = None
    if args.out.exists() and not args.overwrite:
        existing = packmod.load_pack(str(args.out))
    default_name = existing.name if existing else dirs[0].name

    warnings: list[str] = []
    pack = packmod.export_pack(
        state_dir_reader=packmod.StateReader(),
        name=args.name or default_name,
        description=args.description,
        models_from=args.models_from,
        include=args.include_model,
        exclude=args.exclude_model,
        workspaces=not args.no_workspaces,
        workspace_names=args.workspace,
        include_defaults=not args.no_defaults,
        existing=existing,
        warn=warnings.append,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(pack.to_yaml())
    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)
    print(f"wrote {args.out} — {len(pack.models)} model(s), {len(pack.workspaces)} workspace(s)")


def _site_command(argv: list[str]) -> None:
    """`tinkerscope site export <dir>` — publish a read-only static site."""
    parser = argparse.ArgumentParser(
        prog="tinkerscope site",
        description="Export a read-only static site (built SPA + baked JSON) for GitHub Pages or any file host.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="Write a self-contained static site into a directory.")
    ex.add_argument("out", type=Path, help="output directory (created; its data/ and _app/ are replaced)")
    ex.add_argument("--dir", action="append", type=Path, default=None,
                    help="scan root(s) whose state to export (default: cwd) — must match how the instance was launched")
    ex.add_argument("--title", default=None, help="site title, shown in the read-only badge (default: the dir name)")
    ex.add_argument("--description", default=None)
    ex.add_argument("--workspace", action="append", default=None, metavar="NAME",
                    help="include only these workspaces by name (repeatable)")
    ex.add_argument("--open", default=None, metavar="WS_ID",
                    help="workspace id to open by default (default: the first exported one)")
    ex.add_argument("--no-logprobs", action="store_true",
                    help="drop per-token logprobs — much smaller, but disables the token inspector and the first-token chart")
    ex.add_argument("--no-pins", action="store_true", help="exclude saved pins")
    ex.add_argument("--web-dist", type=Path, default=None,
                    help="built frontend to publish (default: this install's web/dist, else the packaged copy)")
    args = parser.parse_args(argv)
    if args.cmd != "export":
        return

    dirs = [d.expanduser().resolve() for d in (args.dir or [Path.cwd()])]
    for d in dirs:
        if not d.is_dir():
            sys.exit(f"not a directory: {d}")
    os.environ["TINKERSCOPE_SCAN_ROOTS"] = ":".join(str(d) for d in dirs)

    from .api.main import _web_dist
    from . import site_export

    web_dist = args.web_dist.expanduser().resolve() if args.web_dist else _web_dist()
    if web_dist is None:
        sys.exit("no built frontend found — run `npm run build` in web/ (or pass --web-dist)")

    warnings: list[str] = []
    stats = site_export.export_site(
        args.out.expanduser().resolve(),
        web_dist=web_dist,
        title=args.title or dirs[0].name,
        description=args.description,
        workspace_names=args.workspace,
        include_logprobs=not args.no_logprobs,
        include_pins=not args.no_pins,
        default_workspace=getattr(args, "open"),
        warn=warnings.append,
    )
    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)
    mb = stats.bytes_written / 1e6
    print(
        f"wrote {args.out} — {stats.workspaces} workspace(s), {stats.models} model(s), "
        f"{stats.nodes_with_blobs} node blob(s), {mb:.1f} MB of data"
    )
    # Per-token logprobs outweigh everything else by ~40× on a real store, so the
    # size is named here rather than left to be discovered by a failed `git push`.
    if len(stats.per_workspace) > 1:
        print("  heaviest workspaces:")
        for name, b in stats.heaviest():
            print(f"    {b / 1e6:8.1f} MB  {name}")
    if mb > 100:
        print(
            f"  NOTE: {mb:.0f} MB is large for a static host (GitHub Pages soft-limits a\n"
            "        site at 1 GB and recommends staying under 100 MB). Nearly all of it\n"
            "        is per-token logprobs. Publish a subset with --workspace NAME\n"
            "        (repeatable), or pass --no-logprobs (costs the token inspector and\n"
            "        the first-token chart mode).",
            file=sys.stderr,
        )
    print(f"  preview: python -m http.server -d {args.out} 8080")


def main() -> None:
    if sys.argv[1:2] == ["pack"]:
        return _pack_command(sys.argv[2:])
    if sys.argv[1:2] == ["site"]:
        return _site_command(sys.argv[2:])
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
    parser.add_argument("--pack", default=None, metavar="FILE_OR_URL",
                        help="apply a share pack (local path or http(s) URL) to this folder's state before serving")
    parser.add_argument("--force", action="store_true",
                        help="with --pack: also overwrite existing default params/layout (default: keep them if the folder was already used)")
    parser.add_argument("--reseed", action="store_true",
                        help="with --pack: fully rebuild the pack's workspaces (delete + re-import, so re-exported raw_meta/logprob blobs refresh and dropped nodes are removed) and overwrite default params — for iterating on a pack you keep re-exporting (implies --force)")
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

    # Set the scan roots in the env NOW (before apply / the app import) so both the
    # pack apply and the served app resolve the same per-set state dir.
    os.environ["TINKERSCOPE_SCAN_ROOTS"] = ":".join(str(d) for d in dirs)

    if args.pack:
        from . import pack as packmod

        p = packmod.load_pack(args.pack)
        s = packmod.apply_pack(p, force=args.force, reseed=args.reseed)
        print(f"applied pack '{s['pack']}': {s['models']} model(s), {s['openrouter']} openrouter, "
              f"{s['workspaces']} workspace(s), default params {s['params']}")

    # Same scan-root set ⇒ same per-set state (highlights, prefs) and the same
    # discovered runs. A second server would just duplicate; be idempotent.
    existing = [
        i for i in instances.list_instances()
        if sorted(i.scan_roots) == sorted(str(d) for d in dirs)
    ]
    if existing:
        print(f"already serving these directories: {existing[0].base_url} (pid {existing[0].pid})")
        if args.pack:
            print("  note: a running instance won't show newly-installed workspaces until restarted")
        return

    env_port = os.environ.get("TINKERSCOPE_PORT")
    requested = args.port if args.port is not None else (int(env_port) if env_port else None)
    port = _pick_port(args.host, requested)

    # The app module reads these at import time (incl. in --reload children).
    # TINKERSCOPE_SCAN_ROOTS was already set above (before the pack apply).
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
