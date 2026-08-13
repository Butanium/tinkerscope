"""Bodies of `tinkerscope pack export` / `tinkerscope site export`.

The Typer commands live in `cli.py` (the one unified app — see its "serve /
pack / site" section); they lazy-import this module so the pack/site machinery
(and everything `pack.py` / `site_export.py` pull in) stays off the hot path of
every driver-verb invocation. Ported verbatim from the pre-unification argparse
handlers in `serve.py` (2026-08-12); behavior-identical, arguments become
keywords.

Both set TINKERSCOPE_SCAN_ROOTS from `dirs` BEFORE importing the state readers
— StateReader / discovery resolve SETTINGS at import time, and the per-scan-root
state dir is derived from that env var.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def _resolve_dirs(dirs: Optional[list[Path]]) -> list[Path]:
    out = [d.expanduser().resolve() for d in (dirs or [Path.cwd()])]
    for d in out:
        if not d.is_dir():
            sys.exit(f"not a directory: {d}")
    os.environ["TINKERSCOPE_SCAN_ROOTS"] = ":".join(str(d) for d in out)
    return out


def run_pack_export(
    out: Path,
    *,
    dirs: Optional[list[Path]],
    name: Optional[str],
    description: Optional[str],
    models_from: str,
    include: Optional[list[str]],
    exclude: Optional[list[str]],
    workspaces: bool,
    workspace_names: Optional[list[str]],
    include_defaults: bool,
    include_logprobs: bool,
    overwrite: bool,
) -> None:
    resolved = _resolve_dirs(dirs)
    from . import pack as packmod

    existing = None
    if out.exists() and not overwrite:
        existing = packmod.load_pack(str(out))
    default_name = existing.name if existing else resolved[0].name

    warnings: list[str] = []
    p = packmod.export_pack(
        state_dir_reader=packmod.StateReader(),
        name=name or default_name,
        description=description,
        models_from=models_from,
        include=include,
        exclude=exclude,
        workspaces=workspaces,
        workspace_names=workspace_names,
        include_defaults=include_defaults,
        include_logprobs=include_logprobs,
        existing=existing,
        warn=warnings.append,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    written = p.write(out)
    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)
    mb = written / 1e6
    print(
        f"wrote {out} — {len(p.models)} model(s), {len(p.workspaces)} workspace(s), "
        f"{mb:.1f} MB{' (gzipped)' if out.suffix == '.gz' else ''}"
    )
    # GitHub rejects a push containing a file over 100 MB outright, and a pack that
    # can't be hosted can't be linked — which is the whole point of one.
    if mb > 90 and out.suffix != ".gz":
        print(
            f"  NOTE: {mb:.0f} MB — GitHub hard-blocks files over 100 MB. Re-run with a\n"
            "        .gz suffix on the output path (~3.6x smaller); the browser and\n"
            "        `--pack` both decompress it transparently.",
            file=sys.stderr,
        )


def run_site_export(
    out: Path,
    *,
    dirs: Optional[list[Path]],
    title: Optional[str],
    description: Optional[str],
    workspace_names: Optional[list[str]],
    open_ws: Optional[str],
    pack_url: Optional[str],
    pack_link: Optional[list[str]],
    logprobs: Optional[str],
    no_logprobs: bool,
    pins: Optional[bool],
    web_dist: Optional[Path],
) -> None:
    resolved = _resolve_dirs(dirs)

    from . import site_export
    from .api.main import _web_dist

    dist = web_dist.expanduser().resolve() if web_dist else _web_dist()
    if dist is None:
        sys.exit("no built frontend found — run `npm run build` in web/ (or pass --web-dist)")

    try:
        pack_links = site_export.resolve_pack_links(pack_link or [])
    except Exception as e:  # a bad spec / unreachable pack — say which, don't traceback
        sys.exit(f"--pack-link: {e}")
    # One pack link and no explicit --pack-url: it IS the pack this site publishes, so
    # the "open this locally" command should name it rather than sit empty.
    if pack_url is None and len(pack_link or []) == 1 and pack_links:
        pack_url = next(iter(pack_links.values()))

    # --no-logprobs predates --logprobs and stays an alias, so a scripted export keeps
    # working. Passing both is a contradiction only when they disagree.
    logprobs_mode = logprobs or ("none" if no_logprobs else "all")
    if no_logprobs and logprobs and logprobs != "none":
        sys.exit(f"--no-logprobs contradicts --logprobs {logprobs} — pass one")
    try:
        site_export.parse_logprobs_mode(logprobs_mode)
    except ValueError as e:
        sys.exit(str(e))

    warnings: list[str] = []
    stats = site_export.export_site(
        out.expanduser().resolve(),
        web_dist=dist,
        title=title or resolved[0].name,
        description=description,
        workspace_names=workspace_names,
        logprobs=logprobs_mode,
        include_pins=pins,
        default_workspace=open_ws,
        pack_url=pack_url,
        pack_links=pack_links,
        warn=warnings.append,
    )
    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)
    mb = stats.bytes_written / 1e6
    print(
        f"wrote {out} — {stats.workspaces} workspace(s), {stats.models} model(s), "
        f"{stats.nodes_with_blobs} node blob(s), {mb:.1f} MB of data"
    )
    # The ids are the point of --pack-link (they're what you paste after `?w=`), and
    # they're derived — nothing else on this box tells you what they came out as.
    if pack_links:
        print(f"  installable on demand — {len(pack_links)} shareable ?w= link(s):")
        for wid, url in pack_links.items():
            print(f"    ?w={wid}\n        from {url}")
    # Per-token logprobs outweigh everything else by ~40× on a real store, so the
    # size is named here rather than left to be discovered by a failed `git push`.
    if len(stats.per_workspace) > 1:
        print("  heaviest workspaces:")
        for wname, b in stats.heaviest():
            print(f"    {b / 1e6:8.1f} MB  {wname}")
    # What a narrowed --logprobs actually did. Reported in NODES (turn-samples) because
    # the dropped blobs are never read, so their bytes are unknown by construction —
    # and reported at all because "the chart still works" is the thing being traded.
    if stats.logprobs_mode != "all":
        total = stats.logprob_nodes_kept + stats.logprob_nodes_dropped
        print(
            f"  logprobs ({stats.logprobs_mode}): kept {stats.logprob_nodes_kept} of {total} "
            f"turn-sample(s) with stored logprobs"
        )
        if stats.logprobs_unnarrowed:
            names = ", ".join(stats.logprobs_unnarrowed[:5])
            more = f" (+{len(stats.logprobs_unnarrowed) - 5} more)" if len(stats.logprobs_unnarrowed) > 5 else ""
            print(f"    kept ALL for {len(stats.logprobs_unnarrowed)} workspace(s) with no saved chart view: {names}{more}")
    # Pins are saved SAMPLES — they carry the question, the response, and the
    # dataset_path they came from. Naming that at export time beats a visitor
    # discovering it in a published data/pins.json.
    if stats.pins:
        print(f"  including {stats.pins} pin(s) — these carry saved responses and their dataset paths")
    elif workspace_names and pins is None:
        print("  pins excluded (a --workspace filter can't scope them; pass --pins to include anyway)")
    if mb > 100:
        print(
            f"  NOTE: {mb:.0f} MB is large for a static host (GitHub Pages soft-limits a\n"
            "        site at 1 GB and recommends staying under 100 MB). Nearly all of it\n"
            "        is per-token logprobs. Publish a subset with --workspace NAME\n"
            "        (repeatable), or narrow them: --logprobs chart keeps the turn each\n"
            "        workspace's chart opens on (so the chart page still works),\n"
            "        --logprobs last:3 the newest turns, --logprobs none nothing.",
            file=sys.stderr,
        )
    print(f"  preview: python -m http.server -d {out} 8080")
