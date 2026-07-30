"""Static-site export — publish a read-only tinkerscope to any file host.

`tinkerscope site export <dir>` writes a self-contained directory: the built SPA
plus a `data/` tree of baked JSON. Open it over http(s) (GitHub Pages, S3, `python
-m http.server`) and you get the real playground — workspaces, branch trees,
threads, N panels, the distribution chart, token probabilities, highlight rules —
with every control that would sample or mutate server state removed. See
`docs/STATIC_SITE.md`; the browser half is `web/src/lib/{static-mode,api-static}.ts`.

Three things make this more than a file copy:

1. **The data files ARE the API responses.** Each `data/*.json` is byte-shaped like
   the endpoint it stands in for (`data/state.json` ≡ `GET /api/state`,
   `data/workspaces.json` ≡ `GET /api/workspaces`, …), so the frontend's static
   transport has no special cases and the wire contract stays single-sourced. Blob
   files keep the store's own on-disk layout (`<cid>.blobs/<nid>.json`).

2. **Panel model refs are rewritten to shareable sentinels.** A saved workspace
   addresses a discovered run by a scan-dir-relative id that means nothing off this
   box; `pack.resolve_shareable` turns it into `ckpt:<tinker://…>` and contributes a
   labeled model to the catalog. Without this every panel would be titled by an id
   the site can't resolve. Same machinery as share-pack export — a site export is a
   pack that kept its heavy blobs.

3. **`index.html` gets rewritten.** The SPA fallback SvelteKit emits references its
   assets ABSOLUTELY (`/_app/…`), which 404s under a GitHub Pages project subpath
   (`user.github.io/repo/`), so the absolute refs become relative. The same pass
   injects `window.__TSCOPE_STATIC__` — the synchronous marker that flips the
   frontend into read-only static mode.

Unlike a share pack, `token_logprobs` are KEPT (that's the whole token-inspector +
first-token-chart surface). They dominate the byte count; `--no-logprobs` drops them.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import pack as packmod

MANIFEST_VERSION = 1

# Absolute asset refs in the emitted SPA fallback that must become relative for a
# subpath deploy. Anchored on the quote so an external https:// URL can't match.
_ABS_REFS = ('"/_app/', '"/favicon.svg"', "'/_app/")

# SvelteKit's inline bootstrap pins the router's base path. Relative ASSET refs alone
# are not enough: with base "" the client router tries to match the document's real
# path (`/repo/`) against the app's only route (`/`) and throws "Not found: /repo/"
# before anything renders — and its own `/_app/version.json` poll stays absolute.
# Computing the base from the document at RUNTIME (rather than baking a --base build)
# keeps ONE exported artifact working at the origin root AND at any subpath.
_BASE_LITERAL = 'base: ""'
_BASE_RUNTIME = 'base: new URL(".", location.href).pathname.replace(/\\/$/, "")'


def _slug(s: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-")
    return out or "site"


@dataclass
class SiteStats:
    workspaces: int = 0
    nodes_with_blobs: int = 0
    models: int = 0
    bytes_written: int = 0
    #: name → bytes, per workspace. Logprob blobs dominate a real store by ~40×
    #: (measured: 24 MB of light bodies vs 901 MB of blobs across 25 workspaces,
    #: one of them 665 MB alone), and a static host has real size limits — so the
    #: breakdown is reported rather than left for the user to discover from `du`.
    per_workspace: dict[str, int] = field(default_factory=dict)

    def heaviest(self, n: int = 5) -> list[tuple[str, int]]:
        return sorted(self.per_workspace.items(), key=lambda kv: -kv[1])[:n]


def _write_json(path: Path, obj: Any, stats: SiteStats) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    stats.bytes_written += len(text.encode("utf-8"))


def _tinker_models_json(pack: packmod.Pack) -> dict:
    """The `GET /api/tinker-models` shape, from the pack's ckpt/base models. This is
    where a `ckpt:`/`base:` panel gets its human LABEL (see model-catalog's
    ckptLabel/baseLabel), so a site with no catalog shows raw tinker:// URIs."""
    models = []
    for m in pack.models:
        if m.kind == "ckpt":
            models.append({"kind": "checkpoint", "id": m.ref, "label": m.label, "sampler_path": m.ref})
        elif m.kind == "base":
            models.append({"kind": "base", "id": m.ref, "label": m.label, "base_model": m.ref})
    return {"available": True, "error": None, "models": models}


def _openrouter_models_json(pack: packmod.Pack, existing: list[dict]) -> list[dict]:
    """The saved OpenRouter quick-list (`GET /api/openrouter-models`), union of what
    the state dir had and what the pack's `openrouter:` models add."""
    out = {e["openrouter_model"]: e for e in existing if isinstance(e, dict) and e.get("openrouter_model")}
    for m in pack.models:
        if m.kind == "openrouter":
            out.setdefault(m.ref, {"label": m.label, "openrouter_model": m.ref})
    return list(out.values())


def _state_json(pack: packmod.Pack, workspace_id: str | None) -> dict:
    """A `GET /api/state` snapshot seeded from the pack's defaults. The frontend
    mirrors this as its shared state; there is no bus behind it."""
    session = packmod.build_last_session(pack)
    panels = [
        {
            "id": p["id"],
            "run_id": p.get("run_id"),
            "checkpoint": p.get("checkpoint"),
            "messages": [],
            "thread_system_prompt": None,
        }
        for p in session.get("panels") or []
    ]
    return {
        "panels": panels or [{"id": "primary", "run_id": None, "checkpoint": None, "messages": [], "thread_system_prompt": None}],
        "workspace_id": workspace_id,
        "system_prompt": None,
        "system_enabled": None,
        "temperature": session.get("temperature", 1.0),
        "max_tokens": session.get("max_tokens", 512),
        "n_samples": session.get("n_samples", 1),
        "thinking": session.get("thinking", False),
        "top_p": session.get("top_p"),
        "chat_id": 0,
        "running": False,
        "last_event": None,
        "last_event_ts": 0,
    }


def _blob_node_ids(body: dict) -> list[str]:
    """Node ids in a light body advertising a heavy blob (either flag)."""
    out: list[str] = []
    for tree in (body.get("trees") or {}).values():
        if not isinstance(tree, dict):
            continue
        for nid, node in (tree.get("nodes") or {}).items():
            if isinstance(node, dict) and (node.get("has_token_logprobs") or node.get("has_raw_meta")):
                out.append(nid)
    return out


def _strip_logprob_flags(body: dict) -> None:
    """Drop `has_token_logprobs` in place — for `--no-logprobs`, where the blobs
    aren't written. A flag with no blob behind it would leave the token inspector
    stuck on 'loading' forever."""
    for tree in (body.get("trees") or {}).values():
        if not isinstance(tree, dict):
            continue
        for node in (tree.get("nodes") or {}).values():
            if isinstance(node, dict):
                node.pop("has_token_logprobs", None)


def export_site(
    out_dir: Path,
    *,
    web_dist: Path,
    title: str,
    description: str | None = None,
    workspace_names: list[str] | None = None,
    include_logprobs: bool = True,
    include_pins: bool = True,
    default_workspace: str | None = None,
    warn: Callable[[str], None] = lambda _m: None,
) -> SiteStats:
    """Write a complete static site into `out_dir` (created; existing `data/` and
    `_app/` are replaced). Reads the state dir named by SETTINGS (i.e. by
    TINKERSCOPE_SCAN_ROOTS), exactly like `pack export`."""
    from .api import workspace_store
    from .api.routes import highlights as hl_store
    from .api.routes import openrouter_models as or_store
    from .api.routes import pins as pins_store
    from .api.settings import SETTINGS
    from .api.store import read_json

    stats = SiteStats()
    reader = packmod.StateReader()
    resolve = reader.make_resolver(warn)

    # Models + default params/layout: the pack path, so model resolution and label
    # preference stay single-sourced. Its prepared workspace bodies are discarded —
    # we re-read the real ones below to keep the heavy blobs a pack strips.
    pack = packmod.export_pack(
        state_dir_reader=reader,
        name=title,
        description=description,
        models_from="all",
        workspaces=True,
        workspace_names=workspace_names,
        warn=warn,
    )
    stats.models = len(pack.models)

    data = out_dir / "data"
    if data.exists():
        shutil.rmtree(data)

    # ── workspaces (light bodies + per-node blobs), panels rewritten ───────────
    summaries: list[dict] = []
    for summ in workspace_store.list_summaries():
        cid = summ.get("id")
        if not isinstance(cid, str):
            continue
        body = workspace_store.get_body(cid)
        if body is None:
            warn(f"workspace {cid!r} has no body on disk; skipped")
            continue
        if workspace_names and (body.get("name") or "") not in workspace_names:
            continue
        body = json.loads(json.dumps(body))  # own it before rewriting
        packmod.rewrite_panels(body, resolve=resolve)

        before = stats.bytes_written
        nids = _blob_node_ids(body)
        blobs = workspace_store.get_blobs(cid, nids) if nids else {}
        if not include_logprobs:
            _strip_logprob_flags(body)
        for nid, blob in blobs.items():
            payload = {k: v for k, v in blob.items() if include_logprobs or k != "token_logprobs"}
            if not payload:
                continue
            _write_json(data / "workspaces" / f"{cid}.blobs" / f"{nid}.json", payload, stats)
            stats.nodes_with_blobs += 1

        _write_json(data / "workspaces" / f"{cid}.json", body, stats)
        stats.per_workspace[body.get("name") or cid] = stats.bytes_written - before
        # The summary's `panels` drives "which models does this workspace show"
        # before its body is fetched, so it needs the same rewrite.
        s = dict(summ)
        s["panels"] = body.get("panels") or []
        summaries.append(s)
        stats.workspaces += 1

    if not summaries:
        warn("no workspaces exported — the site will open empty")
    _write_json(data / "workspaces.json", summaries, stats)

    open_id = default_workspace or (summaries[0]["id"] if summaries else None)
    if default_workspace and not any(s["id"] == default_workspace for s in summaries):
        warn(f"--open {default_workspace!r} is not among the exported workspaces; opening the first instead")
        open_id = summaries[0]["id"] if summaries else None

    # ── the remaining endpoint stand-ins ──────────────────────────────────────
    _write_json(data / "state.json", _state_json(pack, open_id), stats)
    # The REAL prefs, with only `last_session` replaced by the pack-resolved layout
    # (its panel refs are rewritten to sentinels). Everything else rides along —
    # notably `chart_view`, the per-workspace distribution-chart view state that
    # lib/chart-view.ts mirrors here, so a published site opens the chart bucketed
    # the way its author left it. Keys the browser doesn't know are inert.
    prefs = dict(read_json(SETTINGS.prefs_path, {}) or {})
    prefs["last_session"] = json.dumps(packmod.build_last_session(pack))
    _write_json(data / "prefs.json", prefs, stats)
    _write_json(data / "models.json", [], stats)  # no local run dirs on a static site
    _write_json(data / "tinker-models.json", _tinker_models_json(pack), stats)
    # Each of these goes through the ROUTE's list helper, not a raw file read, so a
    # baked file is what the endpoint would have returned (highlights in particular
    # are seeded on first read — a raw read of a fresh dir would ship none).
    _write_json(
        data / "openrouter-models.json",
        _openrouter_models_json(pack, or_store.list_openrouter_models()),
        stats,
    )
    _write_json(
        data / "highlights.json",
        [r.model_dump() for r in hl_store.list_rules()],
        stats,
    )
    _write_json(data / "pins.json", pins_store.list_pins() if include_pins else [], stats)
    _write_json(
        data / "health.json",
        {
            "ok": True,
            "root": title,
            "scan_roots": [],
            "tinker_key": False,
            "openrouter_key": False,
            "available": False,
            "supported_models": [],
            "error": None,
        },
        stats,
    )

    manifest = {
        "version": MANIFEST_VERSION,
        "site": _slug(title),
        "title": title,
        "description": description,
        "data": "data/",
        "default_workspace": open_id,
    }
    _write_json(data / "manifest.json", manifest, stats)

    # ── the SPA ───────────────────────────────────────────────────────────────
    _copy_spa(web_dist, out_dir, manifest)
    # GitHub Pages serves nothing from a directory containing `_app` unless Jekyll
    # is disabled — underscore-prefixed paths are Jekyll-private.
    (out_dir / ".nojekyll").write_text("")
    return stats


def _copy_spa(web_dist: Path, out_dir: Path, manifest: dict) -> None:
    """Copy the built SPA and rewrite index.html: absolute asset refs → relative
    (subpath deploys), plus the injected static-mode manifest."""
    index = web_dist / "index.html"
    if not index.exists():
        raise FileNotFoundError(f"no built frontend at {web_dist} — run `npm run build` in web/ first")
    out_dir.mkdir(parents=True, exist_ok=True)
    for child in web_dist.iterdir():
        if child.name == "index.html":
            continue
        dest = out_dir / child.name
        if dest.exists():
            shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
        shutil.copytree(child, dest) if child.is_dir() else shutil.copy2(child, dest)

    html = index.read_text(encoding="utf-8")
    for ref in _ABS_REFS:
        html = html.replace(ref, ref[0] + "." + ref[1:])
    if _BASE_LITERAL not in html:
        raise ValueError(
            f"built index.html has no {_BASE_LITERAL!r} — SvelteKit's bootstrap shape changed; "
            "a subpath deploy would 404 its own route, so failing loudly beats shipping that"
        )
    html = html.replace(_BASE_LITERAL, _BASE_RUNTIME, 1)
    inject = (
        "  <script>window.__TSCOPE_STATIC__ = "
        + json.dumps(manifest, ensure_ascii=False)
        + ";</script>\n"
    )
    if "</head>" not in html:
        raise ValueError("built index.html has no </head> — cannot inject the static manifest")
    html = html.replace("</head>", inject + "</head>", 1)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
