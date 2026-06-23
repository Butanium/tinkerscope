"""Auto-discovery of Tinker training runs under the scan roots.

Replaces Harry's hand-maintained models.yaml. Every Tinker run (via
tinker_cookbook) drops two files in its log dir:

  config.json       — one per run: model_name, wandb_name, renderer, dataset, …
  checkpoints.jsonl — one row per saved checkpoint, each with a sampler_path

We scan for `checkpoints.jsonl`, read the sibling `config.json` defensively,
and emit one Run per dir with its whole checkpoint trajectory. Each run's
base model is cross-checked against tinker's currently-served model list
(`get_server_capabilities`) so the UI can grey out runs that can't be sampled
(e.g. `Qwen/Qwen3-30B-A3B-Base`, which tinker no longer hosts).

Discovery itself has zero ML deps (pure json + filesystem); only the
capabilities probe touches the tinker SDK, and it degrades to "unknown" when
the key is unset or the service is unreachable.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from .settings import SETTINGS


@dataclass
class Checkpoint:
    name: str                 # "000010", "final"
    batch: int | None
    epoch: int | None
    sampler_path: str | None  # tinker://…/sampler_weights/<step> — sample from this
    state_path: str | None    # tinker://…/weights/<step> — training state (not sampled)
    step: int | None          # numeric step parsed from name/batch, for sorting


@dataclass
class Run:
    id: str                       # stable id = run_dir relative to serving root
    name: str                     # wandb_name, else dir name
    wandb_project: str | None     # config wandb_project (often null; near-constant per scan root)
    wandb_name: str | None        # config wandb_name (also feeds `name`; exposed raw for filtering)
    run_dir: str                  # absolute path
    base_model: str | None
    renderer_name: str | None     # training renderer (from config); inference uses it
    dataset_path: str | None      # training dataset JSONL, relative to root if under it
    lora_rank: int | None
    learning_rate: float | None
    seed: int | None
    num_checkpoints: int
    checkpoints: list[Checkpoint]
    sampleable: bool | None       # None = unknown (capabilities unavailable)
    unsampleable_reason: str | None
    config_error: str | None      # set if config.json missing/malformed (run still listed)


# ---------------------------------------------------------------------------
# tinker server capabilities (cached): which base models can be sampled today
# ---------------------------------------------------------------------------
_caps_lock = threading.Lock()
_caps_cache: dict | None = None


def get_capabilities(force: bool = False) -> dict:
    """Return {available, supported_models, error}, cached after first success.

    Never raises: an unset key or an unreachable service yields available=False
    with an error string, so the app and discovery keep working offline.
    """
    global _caps_cache
    with _caps_lock:
        if _caps_cache is not None and not force:
            return _caps_cache
        result: dict = {"available": False, "supported_models": [], "error": None}
        if not SETTINGS.tinker_api_key:
            result["error"] = "TINKER_API_KEY not set"
            _caps_cache = result
            return result
        try:
            import tinker

            sc = tinker.ServiceClient()
            caps = sc.get_server_capabilities()
            result["supported_models"] = [m.model_name for m in caps.supported_models]
            result["available"] = True
        except Exception as e:  # network / auth / SDK error — degrade, don't crash
            result["error"] = f"{type(e).__name__}: {e}"
        _caps_cache = result
        return result


def _supported_base_set(caps: dict) -> set[str]:
    """Base-model names that can be sampled, stripping tinker ':peft:…' suffixes."""
    names = set()
    for m in caps.get("supported_models", []):
        names.add(m)
        names.add(m.split(":peft")[0])
    return names


# ---------------------------------------------------------------------------
# config.json + checkpoints.jsonl readers (defensive)
# ---------------------------------------------------------------------------
def _parse_step(name: str, batch) -> int | None:
    if isinstance(batch, int):
        return batch
    try:
        return int(name)
    except (TypeError, ValueError):
        return None


def _read_checkpoints(ckpt_file: Path) -> list[Checkpoint]:
    out: list[Checkpoint] = []
    for line in ckpt_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue  # skip a malformed row, keep the rest
        name = str(r.get("name", ""))
        out.append(
            Checkpoint(
                name=name,
                batch=r.get("batch"),
                epoch=r.get("epoch"),
                sampler_path=r.get("sampler_path"),
                state_path=r.get("state_path"),
                step=_parse_step(name, r.get("batch")),
            )
        )
    # Sort by step; 'final' (step None) sorts last.
    out.sort(key=lambda c: (c.step is None, c.step if c.step is not None else 0))
    return out


def _rel_to_root(p: Path) -> str:
    try:
        return p.relative_to(SETTINGS.root).as_posix()
    except ValueError:
        return p.as_posix()


def _build_run(run_dir: Path, ckpt_file: Path, supported: set[str], caps_available: bool) -> Run:
    config_error: str | None = None
    config: dict = {}
    cfg_path = run_dir / "config.json"
    if cfg_path.exists():
        try:
            config = json.loads(cfg_path.read_text())
        except json.JSONDecodeError as e:
            config_error = f"config.json malformed: {e}"
    else:
        config_error = "config.json missing"

    base_model = config.get("model_name")
    wandb_project = config.get("wandb_project")
    wandb_name = config.get("wandb_name")
    name = wandb_name or run_dir.name

    # renderer_name lives under dataset_builder.common_config (training renderer)
    renderer_name = None
    dataset_path = None
    db = config.get("dataset_builder")
    if isinstance(db, dict):
        cc = db.get("common_config")
        if isinstance(cc, dict):
            renderer_name = cc.get("renderer_name")
        fp = db.get("file_path")
        if fp:
            # config file_path is relative to the project that trained the run;
            # try to resolve it under the run_dir's ancestors for a real link.
            dataset_path = _resolve_dataset_path(run_dir, str(fp))

    checkpoints = _read_checkpoints(ckpt_file)

    # Sampleability: requires capabilities to be available AND a known base model.
    sampleable: bool | None
    reason: str | None = None
    if not caps_available:
        sampleable = None  # unknown — don't wrongly grey everything out offline
    elif base_model is None:
        sampleable = False
        reason = "no model_name in config.json"
    elif base_model in supported:
        sampleable = True
    else:
        sampleable = False
        reason = f"tinker does not currently serve sampling for {base_model}"

    return Run(
        id=_rel_to_root(run_dir),
        name=name,
        wandb_project=wandb_project,
        wandb_name=wandb_name,
        run_dir=str(run_dir),
        base_model=base_model,
        renderer_name=renderer_name,
        dataset_path=dataset_path,
        lora_rank=config.get("lora_rank"),
        learning_rate=config.get("learning_rate"),
        seed=config.get("seed"),
        num_checkpoints=len(checkpoints),
        checkpoints=checkpoints,
        sampleable=sampleable,
        unsampleable_reason=reason,
        config_error=config_error,
    )


def _resolve_dataset_path(run_dir: Path, file_path: str) -> str | None:
    """The training dataset is recorded as a project-relative path in config.
    Walk up from the run dir looking for it; return a root-relative path if it
    exists, else the raw recorded value (still informative as a label)."""
    fp = Path(file_path)
    if fp.is_absolute() and fp.exists():
        return _rel_to_root(fp)
    for ancestor in [run_dir, *run_dir.parents]:
        cand = ancestor / file_path
        if cand.exists():
            return _rel_to_root(cand)
    return file_path  # not found on disk; keep the recorded value as a label


# ---------------------------------------------------------------------------
# Run scan (cached; refreshable)
# ---------------------------------------------------------------------------
_runs_lock = threading.Lock()
_runs_cache: list[Run] | None = None


def scan_runs() -> list[Run]:
    """Scan all scan roots for runs. Not cached — `list_runs` caches."""
    caps = get_capabilities()
    supported = _supported_base_set(caps)
    caps_available = caps.get("available", False)

    runs: list[Run] = []
    seen: set[Path] = set()
    for root in SETTINGS.scan_roots:
        for ckpt_file in sorted(root.rglob("checkpoints.jsonl")):
            run_dir = ckpt_file.parent.resolve()
            if run_dir in seen:
                continue
            seen.add(run_dir)
            runs.append(_build_run(run_dir, ckpt_file, supported, caps_available))
    runs.sort(key=lambda r: r.id)
    return runs


def list_runs(force: bool = False) -> list[Run]:
    """Cached run list. `force=True` rescans the filesystem and capabilities."""
    global _runs_cache
    with _runs_lock:
        if _runs_cache is None or force:
            if force:
                get_capabilities(force=True)
            _runs_cache = scan_runs()
        return _runs_cache


def find_run(run_id: str) -> Run | None:
    for r in list_runs():
        if r.id == run_id:
            return r
    return None


def run_to_dict(r: Run) -> dict:
    return asdict(r)
