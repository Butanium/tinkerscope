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

Unlike a share pack, `token_logprobs` are KEPT by default (that's the whole
token-inspector + first-token-chart surface, and a published site is a curated view
whose chart page must still work). They also dominate the byte count — ~97% of it —
so `--logprobs` can narrow WHICH turns keep them:

    all (default)  every turn, as before
    chart          only the turn each workspace's SAVED chart view points at, which
                   is exactly what the first-token chart opens on. A workspace with
                   no recorded view keeps everything (fail safe) and says so.
    last:N         only the newest N assistant turns of each thread
    none           nothing (what `--no-logprobs` has always meant)

A narrowed export never READS the blobs it drops, and every node it drops loses its
`has_token_logprobs` flag — a flag with no blob behind it hangs the token inspector
on 'loading'. The manifest records which setting was used so the UI can tell a
reader "this site was published without them" instead of blaming the sampler.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import pack as packmod
from .api.state import DEFAULT_PANEL_ID

MANIFEST_VERSION = 1

#: `--logprobs` values. `all` is the DEFAULT and stays the default: a published site
#: is the author's curated view, and the token inspector + first-token chart are most
#: of why anyone publishes one — silently dropping them would break a republished
#: chart page. The middle settings exist because logprobs are ~97% of the bytes
#: (measured: 24 MB of workspaces vs 901 MB of blobs), which is the difference
#: between a site a host will take and one it won't.
LOGPROBS_ALL = "all"
LOGPROBS_CHART = "chart"
LOGPROBS_NONE = "none"
_LAST_RE = re.compile(r"^last:(\d+)$")


def parse_logprobs_mode(value: str) -> tuple[str, int]:
    """`--logprobs` VALUE → `(mode, n)`; `n` is only meaningful for `last`.

    all      every turn (default)
    chart    only the turn each workspace's SAVED chart view points at
    last:N   only the newest N assistant turns of each thread
    none     no logprobs at all (what `--no-logprobs` has always meant)
    """
    v = (value or "").strip().lower()
    if v in (LOGPROBS_ALL, LOGPROBS_CHART, LOGPROBS_NONE):
        return v, 0
    if m := _LAST_RE.match(v):
        n = int(m.group(1))
        if n < 1:
            raise ValueError("--logprobs last:N needs N >= 1")
        return "last", n
    raise ValueError(f"--logprobs {value!r}: expected all | chart | last:N | none")


def resolve_pack_links(specs: list[str]) -> dict[str, str]:
    """`--pack-link` SPECs → `{workspace id: public pack URL}`, the manifest's
    self-healing map.

    A published `?w=<id>` link is only shareable if a browser that has never seen the
    workspace can GET it — otherwise the person who installed a pack has a URL that
    works for them alone (their IndexedDB has the workspace; a recipient's doesn't).
    So the site carries, per workspace id the pack would mint, where to fetch that
    pack from; an unknown id resolves through it instead of falling back to "not
    found, opened the most recent one instead".

    SPEC is either a bare `https://…` URL (fetched here to enumerate its ids, and
    published as itself) or `<local path>=<public URL>` when the file is one you are
    about to upload — the path is read for the ids, the URL is what visitors fetch.
    An http(s) spec is never split, so a URL carrying `?k=v` stays intact.
    """
    out: dict[str, str] = {}
    for spec in specs:
        if packmod._is_url(spec):
            src, url = spec, spec
        else:
            src, sep, url = spec.partition("=")
            if not sep:
                raise ValueError(
                    f"--pack-link {spec!r}: a local pack needs the URL visitors will fetch it "
                    "from — write it as <path>=<https://…>"
                )
            if not packmod._is_url(url):
                raise ValueError(f"--pack-link {spec!r}: {url!r} is not an http(s) URL")
        pack = packmod.load_pack(src)
        for wid in packmod.pack_workspace_ids(pack):
            out[wid] = url
    return out

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
    pins: int = 0
    bytes_written: int = 0
    #: workspace id → (name, bytes). Keyed by ID because workspace NAMES are not
    #: unique — keying by name silently merged two same-named workspaces' sizes.
    #: Reported rather than left to `du` because logprob blobs dominate a real store
    #: by ~40× (measured: 24 MB of light bodies vs 901 MB of blobs across 25
    #: workspaces, one of them 665 MB alone) and static hosts have real size limits.
    per_workspace: dict[str, tuple[str, int]] = field(default_factory=dict)
    #: `--logprobs` selection, so the caller can report what a middle setting did.
    #: Counted in NODES rather than bytes: the dropped blobs are never serialized
    #: (that's the point), so their size is not known without paying for it.
    logprobs_mode: str = LOGPROBS_ALL
    logprob_nodes_kept: int = 0
    logprob_nodes_dropped: int = 0
    #: Workspaces a subset setting could not narrow, which therefore kept ALL their
    #: logprobs — named, because "kept everything" is the surprising outcome.
    logprobs_unnarrowed: list[str] = field(default_factory=list)

    def heaviest(self, n: int = 5) -> list[tuple[str, int]]:
        """(name, bytes) for the n biggest, largest first."""
        return sorted(self.per_workspace.values(), key=lambda nb: -nb[1])[:n]


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
        "panels": panels or [{"id": DEFAULT_PANEL_ID, "run_id": None, "checkpoint": None, "messages": [], "thread_system_prompt": None}],
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


def _want_pins(include_pins: bool | None, workspace_names: list[str] | None) -> bool:
    """Tri-state: True/False are the explicit `--pins` / `--no-pins`; None (the
    default) means "include unless this is a filtered export" — see the call site."""
    if include_pins is not None:
        return include_pins
    return not workspace_names


def _narrow_chart_view(raw: Any, exported_ids: set[str]) -> Any:
    """Keep only the exported workspaces' chart-view records.

    The mirrored blob (`{v, global, ws: {id: {...}}}`, web/src/lib/chart-view.ts) holds
    up to 40 workspaces regardless of what this export publishes, and its per-workspace
    records name ids and carry `ftAdded` token strings — telling a visitor about
    workspaces the author deliberately filtered out. The global picks stay: they're the
    author's "how I look at a distribution" defaults, not workspace content."""
    try:
        blob = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return raw  # unparseable → leave alone; hydrate ignores it browser-side
    if not isinstance(blob, dict) or not isinstance(blob.get("ws"), dict):
        return raw
    blob = dict(blob)
    blob["ws"] = {k: v for k, v in blob["ws"].items() if k in exported_ids}
    return json.dumps(blob) if isinstance(raw, str) else blob


def _blob_node_ids(body: dict, keep: set[str] | None = None) -> list[str]:
    """Node ids in a light body advertising a heavy blob (either flag).

    `keep` (the `--logprobs` subset) prunes the fetch itself: a node advertising ONLY
    logprobs we're dropping needs no read at all, which is where a middle setting's
    speed comes from — the blobs it excludes are never touched, not read and filtered.
    """
    out: list[str] = []
    for tree in (body.get("trees") or {}).values():
        if not isinstance(tree, dict):
            continue
        for nid, node in (tree.get("nodes") or {}).items():
            if not isinstance(node, dict):
                continue
            if node.get("has_raw_meta"):
                out.append(nid)
            elif node.get("has_token_logprobs") and (keep is None or nid in keep):
                out.append(nid)
    return out


def _logprob_flag_ids(body: dict) -> set[str]:
    """Nodes advertising stored logprobs — the denominator for what a subset kept."""
    return {
        nid
        for tree in (body.get("trees") or {}).values()
        if isinstance(tree, dict)
        for nid, node in (tree.get("nodes") or {}).items()
        if isinstance(node, dict) and node.get("has_token_logprobs")
    }


def _strip_logprob_flags(body: dict, keep: set[str] | None) -> None:
    """Drop `has_token_logprobs` in place on every node whose blob we are NOT
    writing (`keep=None` keeps all, so this is a no-op). A flag with no blob behind
    it would leave the token inspector stuck on 'loading' forever."""
    if keep is None:
        return
    for tree in (body.get("trees") or {}).values():
        if not isinstance(tree, dict):
            continue
        for nid, node in (tree.get("nodes") or {}).items():
            if isinstance(node, dict) and nid not in keep:
                node.pop("has_token_logprobs", None)


# ── which turns keep their logprobs (--logprobs chart | last:N) ───────────────
# Selecting a SUBSET means answering "which nodes does the published page actually
# read logprobs from", which is a question about the branch tree — so these mirror
# `web/src/lib/tree.ts` (selected-child walk, siblings) and +page's
# `buildChartSources`. The canonical Python copy of the walk lives in `cli.py`
# (`_selected_child` / `_thread_path`); it is not importable without pulling the
# whole CLI in, so this is a deliberate small second copy — fold the three together
# if the tree helpers ever move to a shared module.
_ROOT = "__root__"


def _selected_child(tree: dict, parent_key: str) -> str | None:
    kids = (
        tree.get("rootChildren") or []
        if parent_key == _ROOT
        else ((tree.get("nodes") or {}).get(parent_key) or {}).get("children") or []
    )
    if not kids:
        return None
    sel = (tree.get("selected") or {}).get(parent_key)
    return sel if (sel is not None and sel in kids) else kids[-1]


def _path_from(tree: dict, root_id: str) -> list[dict]:
    """`root_id` → leaf, following the selected child at each step."""
    nodes = tree.get("nodes") or {}
    node = nodes.get(root_id)
    if node is None:
        return []
    path, seen, pk = [node], {root_id}, root_id
    while (cid := _selected_child(tree, pk)) is not None and cid not in seen:
        node = nodes.get(cid)
        if node is None:
            break
        seen.add(cid)
        path.append(node)
        pk = cid
    return path


def _turn_samples(tree: dict, node: dict) -> list[str]:
    """The chart's sample set for an assistant turn: ALL siblings of that node that
    carry content or reasoning (mirrors buildChartSources + siblingsOf), i.e. every
    node whose first token the first-token chart can read."""
    nodes = tree.get("nodes") or {}
    parent = node.get("parent")
    sibs = (
        (nodes.get(parent) or {}).get("children") or []
        if parent
        else tree.get("rootChildren") or []
    )
    return [
        sid
        for sid in sibs
        if (n := nodes.get(sid))
        and n.get("role") == "assistant"
        and (n.get("content") or n.get("reasoning"))
    ]


def _turns_along(tree: dict, path: list[dict]) -> list[list[str]]:
    """Assistant TURNS down one path, each as its full sample set. A turn with no
    samples doesn't exist for the chart, so it isn't counted — the turn INDEX a
    saved chart view holds is an index into exactly this list."""
    out = []
    for node in path:
        if node.get("role") != "assistant":
            continue
        samples = _turn_samples(tree, node)
        if samples:
            out.append(samples)
    return out


def _chart_kept_nodes(body: dict, turn: str) -> set[str]:
    """Nodes whose logprobs the saved chart view reads: the picked turn of EVERY
    panel (the modal charts all of them, and 'last' means each panel's own last
    turn). Folded panels included on purpose — "include folded panels" is a control
    the reader still has, and a bar with no first-token data would be the author's
    export silently deciding for them."""
    per_tree = {
        pid: _turns_along(tree, _path_from(tree, sel))
        for pid, tree in (body.get("trees") or {}).items()
        if isinstance(tree, dict) and (sel := _selected_child(tree, _ROOT))
    }
    idx = -1
    if turn != "last":
        try:
            idx = int(turn)
        except (TypeError, ValueError):
            idx = -1
        # The modal resets an index no panel has (`turnCount` is the max over
        # panels) back to 'last' — match that rather than exporting nothing.
        if idx >= max((len(t) for t in per_tree.values()), default=0):
            idx = -1
    keep: set[str] = set()
    for turns in per_tree.values():
        if not turns:
            continue
        picked = turns[-1] if idx < 0 else (turns[idx] if idx < len(turns) else None)
        if picked:
            keep.update(picked)
    return keep


def _last_n_kept_nodes(body: dict, n: int) -> set[str]:
    """The newest `n` assistant turns of every THREAD (each root-level sibling is a
    thread, so a branch-from-start conversation keeps its own tail)."""
    keep: set[str] = set()
    for tree in (body.get("trees") or {}).values():
        if not isinstance(tree, dict):
            continue
        for root_id in tree.get("rootChildren") or []:
            for samples in _turns_along(tree, _path_from(tree, root_id))[-n:]:
                keep.update(samples)
    return keep


def export_site(
    out_dir: Path,
    *,
    web_dist: Path,
    title: str,
    description: str | None = None,
    workspace_names: list[str] | None = None,
    logprobs: str = LOGPROBS_ALL,
    include_pins: bool | None = None,
    default_workspace: str | None = None,
    pack_url: str | None = None,
    pack_links: dict[str, str] | None = None,
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

    lp_mode, lp_n = parse_logprobs_mode(logprobs)
    stats = SiteStats(logprobs_mode=logprobs if lp_mode != "last" else f"last:{lp_n}")
    reader = packmod.StateReader()
    resolve = reader.make_resolver(warn)
    # `chart` reads the mirrored per-workspace chart view (lib/chart-view.ts writes it
    # into prefs so an export can carry it) — the same blob narrowed into the site
    # below, read here for which TURN each workspace's chart points at.
    chart_views: dict[str, dict] = {}
    if lp_mode == LOGPROBS_CHART:
        raw = (read_json(SETTINGS.prefs_path, {}) or {}).get("chart_view")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = None
        if isinstance(raw, dict) and isinstance(raw.get("ws"), dict):
            chart_views = {k: v for k, v in raw["ws"].items() if isinstance(v, dict)}

    # Models + default params/layout: the pack path, so model resolution and label
    # preference stay single-sourced. `skip_bodies` because we re-read the real bodies
    # below (to keep the heavy blobs a pack strips) — preparing them here would
    # deep-copy every body and fetch every raw_meta blob for a value we then drop.
    pack = packmod.export_pack(
        state_dir_reader=reader,
        name=title,
        description=description,
        models_from="all",
        workspaces=True,
        workspace_names=workspace_names,
        skip_bodies=True,
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
        # `keep is None` = every node keeps its logprobs (the `all` default).
        keep: set[str] | None
        if lp_mode == LOGPROBS_ALL:
            keep = None
        elif lp_mode == LOGPROBS_NONE:
            keep = set()
        elif lp_mode == "last":
            keep = _last_n_kept_nodes(body, lp_n)
        else:  # chart — fail SAFE: a workspace whose chart view we can't read keeps
            # everything, because the alternative is silently publishing a first-token
            # page with no data behind it.
            view = chart_views.get(cid)
            if view is None:
                keep = None
                stats.logprobs_unnarrowed.append(body.get("name") or cid)
                warn(
                    f"--logprobs chart: workspace {body.get('name') or cid!r} has no saved chart "
                    "view (open its chart once to record one); kept all its logprobs"
                )
            else:
                keep = _chart_kept_nodes(body, str(view.get("turn") or "last"))

        # Counted from the FLAGS, not from what came back: a dropped node is never
        # fetched, so the blob map can't see it.
        flagged = _logprob_flag_ids(body)
        kept_ids = flagged if keep is None else (flagged & keep)
        stats.logprob_nodes_kept += len(kept_ids)
        stats.logprob_nodes_dropped += len(flagged) - len(kept_ids)

        nids = _blob_node_ids(body, keep)
        blobs = workspace_store.get_blobs(cid, nids) if nids else {}
        _strip_logprob_flags(body, keep)
        for nid, blob in blobs.items():
            payload = {
                k: v
                for k, v in blob.items()
                if (keep is None or nid in keep) or k != "token_logprobs"
            }
            if not payload:
                continue
            _write_json(data / "workspaces" / f"{cid}.blobs" / f"{nid}.json", payload, stats)
            stats.nodes_with_blobs += 1

        _write_json(data / "workspaces" / f"{cid}.json", body, stats)
        stats.per_workspace[cid] = (body.get("name") or cid, stats.bytes_written - before)
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
    # The REAL prefs, with `last_session` replaced by the pack-resolved layout (its
    # panel refs rewritten to sentinels) and `chart_view` NARROWED to the exported
    # workspaces. Everything else rides along — the point is `chart_view`, the
    # per-workspace chart state lib/chart-view.ts mirrors here, so a published site
    # opens the chart bucketed the way its author left it.
    prefs = dict(read_json(SETTINGS.prefs_path, {}) or {})
    prefs["last_session"] = json.dumps(packmod.build_last_session(pack))
    exported_ids = {s["id"] for s in summaries}
    if "chart_view" in prefs:
        prefs["chart_view"] = _narrow_chart_view(prefs["chart_view"], exported_ids)
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
    # Pins carry no workspace id (they're saved SAMPLES: question / response /
    # reasoning / the dataset_path they came from), so a `--workspace` filter cannot
    # scope them — publishing them anyway would ship content, and a local filesystem
    # path, from workspaces the author deliberately excluded. So a filtered export
    # drops them unless `--pins` asks for them back. Unfiltered, they're included as
    # before, and the caller reports what that means.
    pins = pins_store.list_pins() if _want_pins(include_pins, workspace_names) else []
    stats.pins = len(pins)
    # `dataset_path` is dropped from every published pin, filtered or not: it is an
    # ABSOLUTE path on the author's box (a copy target for the local samplescope
    # hand-off — see routes/samplescope.py), it names a file no reader has, and a
    # static site lists no runs to link it to. Pure disclosure with no reader-side
    # use, so it goes even on the unfiltered path where the rest of the pin stays.
    _write_json(data / "pins.json", [{k: v for k, v in p.items() if k != "dataset_path"} for p in pins], stats)
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
        # Where the same content lives as a share PACK, if anywhere. A published site is
        # read-only by construction, so the obvious next question is "how do I sample
        # these myself" — and the honest answer is a `--pack <url>` command, which needs
        # a URL only the publisher knows. Absent ⇒ the UI offers the generic install
        # instructions instead of a command that wouldn't reproduce what's on screen.
        "pack_url": pack_url,
        # `{workspace id: pack URL}` — see resolve_pack_links. What makes a `?w=<id>`
        # link SHAREABLE: a visitor whose browser has never installed that workspace
        # resolves the id here and installs the pack, instead of silently landing on
        # whatever workspace happened to be newest.
        "pack_links": pack_links or {},
        # What `--logprobs` kept, so the UI can name the real reason a turn has no
        # token data. Without it a trimmed site tells a reader "captured on native
        # tinker sampling only" about turns that WERE captured and then published
        # without them — true-sounding and wrong.
        "logprobs": stats.logprobs_mode,
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
