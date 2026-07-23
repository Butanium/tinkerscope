"""Share packs — bundle a tinkerscope setup into one portable YAML file.

A *pack* is a single YAML (or JSON) file that captures what a collaborator needs to
reproduce a tinkerscope session against PUBLIC Tinker checkpoints:

  - **models**   — the checkpoints / base models / OpenRouter refs to make available,
                   each self-contained (a `ckpt:` sampler path, a `base:` model name,
                   or an `openrouter:` id) so NO local run dir is required. This is the
                   crux: a discovered run is addressed by a scan-dir-relative id that a
                   collaborator doesn't have, but a `ckpt:<tinker://…/sampler_weights/…>`
                   path samples straight through tinker's oai endpoint (routes/chat.py),
                   and a published checkpoint keeps the SAME sampler id — so the path in
                   the pack works as-is on anyone's account.
  - **defaults** — the sampling params + the default panel layout (which models load
                   side-by-side on open), seeded into `prefs.json`'s `last_session`.
  - **workspaces** — curated saved conversations, installed into the conversation store.

Two directions:

  - `apply_pack`  — consume a pack (`tinkerscope --pack <file|url>`): merge models into
    the per-state-dir registry (`pack_models.json`, surfaced in the browser's
    "+ Tinker model" typeahead via /api/tinker-models), merge OpenRouter refs into the
    global list, install workspaces (deterministic ids → idempotent re-apply), and seed
    the default params/layout — the last only when the folder is FRESH (or `--force`),
    so re-applying never clobbers a collaborator's own setup.
  - `export_pack` — author a pack from a live state dir (`tinkerscope pack export`):
    gather the models actually in use (rewriting bare run-ids → shareable `ckpt:` paths
    via discovery), the current params/layout, and the saved workspaces (heavy per-node
    logprob/raw_meta blobs stripped — a pack is a single self-contained YAML).

The file always round-trips through `Pack.from_dict` / `Pack.to_dict`; export writes one
YAML file (`--overwrite` regenerates; otherwise it merges into an existing file so
hand-edited labels/description survive).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

VERSION = 1

# Panel model-selection sentinels — mirror web/src/lib/model-sel.ts. A pack model's
# `kind` maps 1:1 to the prefix the frontend/CLI use in the shared `run_id` field.
_KIND_TO_SENTINEL = {"ckpt": "ckpt:", "base": "base:", "openrouter": "openrouter:"}
_SENTINEL_TO_KIND = {v: k for k, v in _KIND_TO_SENTINEL.items()}
_MODEL_KINDS = tuple(_KIND_TO_SENTINEL)

# `last_session` params. The first five have a backend source of truth
# (PlaygroundState); the last three live ONLY in the frontend (+page.svelte's
# topK/presencePenalty/repetitionPenalty, "not in shared state") so we mirror them here.
_PARAM_KEYS = (
    "temperature", "max_tokens", "n_samples", "thinking", "top_p",
    "top_k", "presence_penalty", "repetition_penalty",
)
_FRONTEND_ONLY_DEFAULTS = {"top_k": 20, "presence_penalty": 1.5, "repetition_penalty": 1.0}


def _param_defaults() -> dict[str, Any]:
    """A seeded session's param defaults: the shared-state five derived from
    PlaygroundState (so they never drift from the live defaults) + the frontend-only three."""
    import dataclasses

    from .api.state import PlaygroundState

    s = dataclasses.asdict(PlaygroundState())
    return {k: s[k] for k in ("temperature", "max_tokens", "n_samples", "thinking", "top_p")} | _FRONTEND_ONLY_DEFAULTS


# ── panel-id scheme (mirror +page.svelte nextPanelId) ────────────────────────────
def panel_ids(n: int) -> list[str]:
    """Stable ids for the first n panels: primary, compare, p-2, p-3, …"""
    ids = ["primary", "compare"][: max(0, min(n, 2))]
    i = 2
    while len(ids) < n:
        ids.append(f"p-{i}")
        i += 1
    return ids


def _slug(s: str) -> str:
    """Filename-safe slug within the conversation-store id charset ([A-Za-z0-9_-])."""
    out = re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-")
    return out or "x"


# ── dataclasses ──────────────────────────────────────────────────────────────────
@dataclass
class PackModel:
    label: str
    kind: str  # one of _MODEL_KINDS
    ref: str   # sampler_path (ckpt) | base model name | openrouter id

    def __post_init__(self) -> None:
        if self.kind not in _MODEL_KINDS:
            raise ValueError(f"model {self.label!r}: kind must be one of {_MODEL_KINDS}, got {self.kind!r}")
        if not self.ref:
            raise ValueError(f"model {self.label!r}: empty ref")

    @property
    def panel_ref(self) -> str:
        """The string a panel's `run_id` field carries for this model."""
        return _KIND_TO_SENTINEL[self.kind] + self.ref

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.ref)

    def to_dict(self) -> dict:
        return {"label": self.label, self.kind: self.ref}

    @classmethod
    def from_dict(cls, d: dict) -> "PackModel":
        present = [k for k in _MODEL_KINDS if k in d]
        if len(present) != 1:
            raise ValueError(
                f"model entry must have exactly one of {_MODEL_KINDS} (got {present or 'none'}): {d!r}"
            )
        kind = present[0]
        ref = str(d[kind])
        label = str(d.get("label") or ref)
        return cls(label=label, kind=kind, ref=ref)


@dataclass
class PackWorkspace:
    name: str
    body: dict  # light conversation body (panels + trees), heavy blobs stripped

    def to_dict(self) -> dict:
        return {"name": self.name, "body": self.body}

    @classmethod
    def from_dict(cls, d: dict) -> "PackWorkspace":
        return cls(name=str(d.get("name") or "workspace"), body=dict(d.get("body") or {}))


@dataclass
class Pack:
    name: str
    description: str | None = None
    models: list[PackModel] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    workspaces: list[PackWorkspace] = field(default_factory=list)
    version: int = VERSION

    def model_by_label(self, label: str) -> PackModel | None:
        for m in self.models:
            if m.label == label:
                return m
        return None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"version": self.version, "name": self.name}
        if self.description:
            out["description"] = self.description
        out["models"] = [m.to_dict() for m in self.models]
        if self.defaults:
            out["defaults"] = self.defaults
        if self.workspaces:
            out["workspaces"] = [w.to_dict() for w in self.workspaces]
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "Pack":
        if not isinstance(d, dict):
            raise ValueError("pack must be a mapping (YAML/JSON object)")
        name = str(d.get("name") or "pack")
        models = [PackModel.from_dict(m) for m in (d.get("models") or [])]
        workspaces = [PackWorkspace.from_dict(w) for w in (d.get("workspaces") or [])]
        defaults = dict(d.get("defaults") or {})
        return cls(
            name=name,
            description=d.get("description"),
            models=models,
            defaults=defaults,
            workspaces=workspaces,
            version=int(d.get("version") or VERSION),
        )

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True, width=100)


# ── loading ──────────────────────────────────────────────────────────────────────
def _is_url(src: str) -> bool:
    return src.startswith("http://") or src.startswith("https://")


def load_pack(src: str) -> Pack:
    """Load a pack from a local path or an http(s) URL. YAML or JSON (yaml.safe_load
    parses both — JSON is a YAML subset)."""
    if _is_url(src):
        import httpx

        resp = httpx.get(src, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        text = resp.text
    else:
        text = Path(src).expanduser().read_text()
    data = yaml.safe_load(text)
    return Pack.from_dict(data)


# ═══════════════════════════════════════════════════════════════════════════════════
# APPLY
# ═══════════════════════════════════════════════════════════════════════════════════
def apply_pack(pack: Pack, *, force: bool = False) -> dict:
    """Seed the current state dir (SETTINGS.state_dir) from `pack`. Idempotent for the
    additive parts; the default params/layout are written only when the folder is fresh
    (no prefs.json) unless `force`. Returns a summary dict."""
    from .api import conversation_store, pack_models_store
    from .api.routes import openrouter_models as or_store
    from .api.settings import SETTINGS
    from .api.store import read_json, write_json

    summary: dict[str, Any] = {"pack": pack.name, "models": 0, "openrouter": 0, "workspaces": 0, "params": "skipped"}

    # 1. Models → pack_models.json (ckpt/base) + the global openrouter list, each via its
    #    store's upsert helper (deduped, same logic the UI add-model path uses).
    tinker_models = [m for m in pack.models if m.kind in ("ckpt", "base")]
    or_models = [m for m in pack.models if m.kind == "openrouter"]
    if tinker_models:
        pack_models_store.upsert([{"label": m.label, "kind": m.kind, "ref": m.ref} for m in tinker_models])
        summary["models"] = len(tinker_models)
    if or_models:
        or_store.upsert([{"label": m.label, "openrouter_model": m.ref} for m in or_models])
        summary["openrouter"] = len(or_models)

    # 2. Workspaces → conversation store, deterministic id (re-apply upserts in place).
    for ws in pack.workspaces:
        cid = f"pack-{_slug(pack.name)}-{_slug(ws.name)}"
        body = ws.body
        conversation_store.upsert(
            id=cid,
            name=ws.name,
            system_prompt=body.get("system_prompt"),
            system_enabled=body.get("system_enabled"),
            trees=body.get("trees") or {},
            panels=body.get("panels") or [],
            reduced_panels=body.get("reduced_panels") or [],
            send_targets=body.get("send_targets") or [],
            seen_panels=body.get("seen_panels") or [],
        )
        summary["workspaces"] += 1

    # 3. Default params + panel layout → prefs.json last_session. Only if fresh / forced,
    #    so re-applying a pack never overwrites a collaborator's own params.
    fresh = not SETTINGS.prefs_path.exists()
    if fresh or force:
        prefs = read_json(SETTINGS.prefs_path, {}) or {}
        prefs["last_session"] = json.dumps(_build_last_session(pack))
        write_json(SETTINGS.prefs_path, prefs)
        summary["params"] = "applied" if fresh else "forced"
    return summary


def _build_last_session(pack: Pack) -> dict:
    """Assemble the `last_session` object the frontend restores (panels + params)."""
    labels = list(pack.defaults.get("panels") or [])
    models = [pack.model_by_label(lbl) for lbl in labels]
    models = [m for m in models if m is not None]
    if not models and pack.models:
        models = pack.models[: 1]  # sensible fallback: first model in the single panel
    ids = panel_ids(len(models) or 1)
    panels = [
        {"id": pid, "run_id": m.panel_ref, "checkpoint": None}
        for pid, m in zip(ids, models)
    ] or [{"id": "primary", "run_id": None, "checkpoint": None}]
    session: dict[str, Any] = {"panels": panels}
    dm = _param_defaults()
    for k in _PARAM_KEYS:
        session[k] = pack.defaults.get(k, dm[k])
    return session


# ═══════════════════════════════════════════════════════════════════════════════════
# EXPORT
# ═══════════════════════════════════════════════════════════════════════════════════
_BLOB_FLAGS = ("has_token_logprobs", "has_raw_meta")


def _pick_checkpoint(run: Any, name: str | None) -> Any:
    """The checkpoint to share: by name if given, else the last servable one.

    Skips checkpoints whose sampler weights are KNOWN gone (`servable is False`, from
    discovery's `list_user_checkpoints` sweep) — a `sampler_path` string persists in
    checkpoints.jsonl forever even after the weights expire/delete, so trusting it alone
    would ship refs that 404 the moment a collaborator samples them (the exact failure
    this feature exists to avoid). `servable is None` (offline / no key) is accepted —
    servable-blind fallback, mirroring discovery's own base-only degrade."""
    def live(c: Any) -> bool:
        return bool(c.sampler_path) and c.servable is not False

    if name:
        for c in run.checkpoints:
            if c.name == name:
                return c if live(c) else None
        return None
    servable = [c for c in run.checkpoints if live(c)]
    return servable[-1] if servable else None


def _classify(run_id: str) -> tuple[str | None, str]:
    for sent, kind in _SENTINEL_TO_KIND.items():
        if run_id.startswith(sent):
            return kind, run_id[len(sent):]
    return None, run_id  # bare discovered-run id


def resolve_shareable(
    run_id: str | None,
    checkpoint: str | None,
    *,
    find_run: Callable[[str], Any],
    ckpt_label: Callable[[str, int | None], str],
    warn: Callable[[str], None],
) -> tuple[str | None, PackModel | None]:
    """Turn a panel's (run_id, checkpoint) into a self-contained (panel_ref, PackModel).

    Sentinels pass through; a bare discovered-run id is resolved to a `ckpt:` sampler
    path via `find_run`. Returns (panel_ref_to_write, model_or_None). On an unresolvable
    bare run (gone / no sampler) it warns and returns the ORIGINAL id with no model, so
    the panel isn't silently dropped — the collaborator sees an unavailable model."""
    if not run_id:
        return None, None
    kind, rest = _classify(run_id)
    if kind == "ckpt":
        m = PackModel(label=ckpt_label(rest, None), kind="ckpt", ref=rest)
        return m.panel_ref, m
    if kind == "base":
        m = PackModel(label=rest.split("/")[-1], kind="base", ref=rest)
        return m.panel_ref, m
    if kind == "openrouter":
        m = PackModel(label=rest.split("/")[-1], kind="openrouter", ref=rest)
        return m.panel_ref, m
    run = find_run(run_id)
    if run is None:
        warn(f"run not found, can't share (kept as-is, unavailable to collaborators): {run_id}")
        return run_id, None
    ckpt = _pick_checkpoint(run, checkpoint)
    if ckpt is None or not ckpt.sampler_path:
        warn(f"run {run_id!r} has no servable checkpoint to share (kept as-is)")
        return run_id, None
    label = run.name if checkpoint in (None, "final") else f"{run.name}@{checkpoint}"
    m = PackModel(label=label, kind="ckpt", ref=ckpt.sampler_path)
    return m.panel_ref, m


def _prepare_workspace_body(body: dict, raw_meta: dict[str, str]) -> dict:
    """Shape a light conversation body for a pack:

    - **inline each node's `raw_meta`** (the raw request/response) from `raw_meta`
      (node_id → value), so a collaborator can inspect what was actually sent — on apply,
      `upsert`'s split re-derives the `has_raw_meta` flag + the blob from the inlined field;
    - **drop `token_logprobs`** (heavy — ~90% of a conversation's bytes — and a pack is one
      self-contained YAML) and the stale presence flags."""
    out = json.loads(json.dumps(body))  # deep copy
    for tree in (out.get("trees") or {}).values():
        if not isinstance(tree, dict):
            continue
        for nid, node in (tree.get("nodes") or {}).items():
            if not isinstance(node, dict):
                continue
            for f in _BLOB_FLAGS:
                node.pop(f, None)
            node.pop("token_logprobs", None)  # defensive: light nodes carry no inline heavy field
            if raw_meta.get(nid):
                node["raw_meta"] = raw_meta[nid]
    return out


def _rewrite_panels(
    body: dict, *, resolve: Callable[[str | None, str | None], tuple[str | None, PackModel | None]]
) -> list[PackModel]:
    """Rewrite a conversation body's panel model refs in place to shareable sentinels;
    return the PackModels they resolved to (for the pack's model list)."""
    found: list[PackModel] = []
    for p in body.get("panels") or []:
        ref, model = resolve(p.get("run_id"), p.get("checkpoint"))
        p["run_id"] = ref
        p["checkpoint"] = None if (model is not None) else p.get("checkpoint")
        if model is not None:
            found.append(model)
    return found


def _dedup_models(models: Iterable[PackModel]) -> list[PackModel]:
    index: dict[tuple[str, str], PackModel] = {}
    for m in models:
        index.setdefault(m.key, m)  # first label wins
    return list(index.values())


def _match(m: PackModel, needle: str) -> bool:
    n = needle.lower()
    return n in m.label.lower() or n in m.ref.lower()


def export_pack(
    *,
    state_dir_reader: "StateReader",
    name: str,
    description: str | None,
    models_from: str = "all",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    workspaces: bool = True,
    workspace_names: list[str] | None = None,
    existing: Pack | None = None,
    warn: Callable[[str], None] = lambda _m: None,
) -> Pack:
    """Build a Pack from a live state dir via `state_dir_reader` (which owns the
    discovery / conversation-store / prefs access). `models_from`:
    panels | workspaces | all | runs. Filters (`include`/`exclude`) match a model's
    label or ref. If `existing` is given, the result MERGES into it (union of models by
    ref, workspaces by name; existing name/description kept unless overridden)."""
    resolve = state_dir_reader.make_resolver(warn)
    gathered: list[PackModel] = []
    ws_out: list[PackWorkspace] = []

    # Authoritative labels, so a re-export never degrades a human-authored label to the
    # generic UUID form `ckpt_label` produces for a bare `ckpt:` ref: the file being
    # merged wins, then already-registered pack models. Applied after gathering, so it's
    # independent of dedup order.
    preferred: dict[tuple[str, str], str] = {}
    for m in state_dir_reader.pack_models():
        preferred.setdefault(m.key, m.label)
    if existing is not None:
        for m in existing.models:
            preferred[m.key] = m.label

    # Workspaces first (rewriting their panels also surfaces the models they use).
    if workspaces:
        for wname, body, raw_meta in state_dir_reader.workspace_bodies():
            if workspace_names and wname not in workspace_names:
                continue
            prepared = _prepare_workspace_body(body, raw_meta)
            used = _rewrite_panels(prepared, resolve=resolve)
            ws_out.append(PackWorkspace(name=wname, body=prepared))
            if models_from in ("workspaces", "all"):
                gathered.extend(used)

    # Models from the current on-screen layout (prefs last_session panels).
    if models_from in ("panels", "all"):
        for run_id, ckpt in state_dir_reader.prefs_panels():
            _ref, m = resolve(run_id, ckpt)
            if m is not None:
                gathered.append(m)

    # Already-registered pack models (previous pack applied here).
    if models_from == "all":
        gathered.extend(state_dir_reader.pack_models())

    # Every discovered run's shareable checkpoint.
    if models_from == "runs":
        gathered.extend(state_dir_reader.run_models(warn))

    models = _dedup_models(gathered)
    if include:
        models = [m for m in models if any(_match(m, n) for n in include)]
    if exclude:
        models = [m for m in models if not any(_match(m, n) for n in exclude)]
    for m in models:
        if m.key in preferred:
            m.label = preferred[m.key]

    # Default params + layout from prefs last_session; drop panels whose model was filtered.
    label_by_ref = {m.panel_ref: m.label for m in models}
    defaults = state_dir_reader.prefs_defaults(resolve, label_by_ref)

    pack = Pack(name=name, description=description, models=models, defaults=defaults, workspaces=ws_out)
    if existing is not None:
        pack = _merge_packs(existing, pack, name_override=name, desc_override=description, exclude=exclude)
    return pack


def _merge_packs(
    base: Pack, fresh: Pack, *, name_override: str | None, desc_override: str | None, exclude: list[str] | None
) -> Pack:
    """Merge `fresh` into `base` (union models by ref, workspaces by name; fresh wins on
    conflict). Excludes are re-applied to the union so `pack export existing.yaml
    --exclude-model X` truly removes X from the file."""
    models = _dedup_models([*base.models, *fresh.models])
    # fresh labels win on ref collision
    fresh_by_key = {m.key: m for m in fresh.models}
    models = [fresh_by_key.get(m.key, m) for m in models]
    if exclude:
        models = [m for m in models if not any(_match(m, n) for n in exclude)]
    ws_by_name = {w.name: w for w in base.workspaces}
    for w in fresh.workspaces:
        ws_by_name[w.name] = w
    return Pack(
        name=name_override or base.name,
        description=desc_override if desc_override is not None else base.description,
        models=models,
        # per-key merge (fresh wins) — a re-export that filtered out the currently-shown
        # model (so `fresh` has no `panels`) must not wipe the file's recorded layout.
        defaults={**base.defaults, **fresh.defaults},
        workspaces=list(ws_by_name.values()),
    )


# ── StateReader: the live state-dir access export needs, injected so pack.py stays
#    importable without the API layer for pure format tests ─────────────────────────
class StateReader:
    """Reads a live tinkerscope state dir for export. Wraps discovery + the conversation
    store + prefs so `export_pack` has no direct API-layer coupling (and tests can feed a
    fake reader)."""

    def __init__(self) -> None:
        from .api import conversation_store, discovery, pack_models_store
        from .api.routes.models import ckpt_label
        from .api.settings import SETTINGS
        from .api.store import read_json

        self._store = conversation_store
        self._discovery = discovery
        self._pack_models = pack_models_store
        self._ckpt_label = ckpt_label
        self._prefs = read_json(SETTINGS.prefs_path, {}) or {}
        self._last_session = self._parse_last_session()

    def _parse_last_session(self) -> dict:
        raw = self._prefs.get("last_session")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def make_resolver(self, warn: Callable[[str], None]):
        def resolve(run_id, checkpoint):
            return resolve_shareable(
                run_id, checkpoint, find_run=self._discovery.find_run, ckpt_label=self._ckpt_label, warn=warn
            )
        return resolve

    def workspace_bodies(self):
        """Yield (name, light_body, raw_meta) per saved workspace. `raw_meta` maps
        node_id → its stored raw request/response (fetched from the write-once blobs),
        so export can inline it into the pack."""
        for body in self._store.list_bodies():
            cid = body.get("id")
            nids = [
                nid
                for tree in (body.get("trees") or {}).values() if isinstance(tree, dict)
                for nid, n in (tree.get("nodes") or {}).items()
                if isinstance(n, dict) and n.get("has_raw_meta")
            ]
            blobs = self._store.get_blobs(cid, nids) if (cid and nids) else {}
            raw_meta = {nid: b["raw_meta"] for nid, b in blobs.items() if b.get("raw_meta")}
            yield (body.get("name") or "workspace", body, raw_meta)

    def prefs_panels(self):
        for p in self._last_session.get("panels") or []:
            yield (p.get("run_id"), p.get("checkpoint"))

    def pack_models(self) -> list[PackModel]:
        return [PackModel(label=m["label"], kind=m["kind"], ref=m["ref"]) for m in self._pack_models.read()]

    def run_models(self, warn: Callable[[str], None]) -> list[PackModel]:
        out: list[PackModel] = []
        for run in self._discovery.list_runs():
            ckpt = _pick_checkpoint(run, None)
            if ckpt and ckpt.sampler_path:
                out.append(PackModel(label=run.name, kind="ckpt", ref=ckpt.sampler_path))
            else:
                warn(f"discovered run {run.id!r} has no servable checkpoint; skipped")
        return out

    def prefs_defaults(self, resolve, label_by_ref: dict[str, str]) -> dict:
        d: dict[str, Any] = {}
        for k in _PARAM_KEYS:
            if k in self._last_session:
                d[k] = self._last_session[k]
        labels: list[str] = []
        for run_id, ckpt in self.prefs_panels():
            ref, _m = resolve(run_id, ckpt)
            if ref in label_by_ref:
                labels.append(label_by_ref[ref])
        if labels:
            d["panels"] = labels
        return d
