"""Shared fixtures for tinkerscope's backend tests.

The tricky bit: `settings.py` resolves `TINKERSCOPE_*` env at *import* time, and
`paths.py` resolves `XDG_STATE_HOME` at import time too. `main.py` then mounts
its routers / SPA at import time using that frozen `SETTINGS`. So a test must set
its env vars and monkeypatch the (real-tinker) capabilities probe BEFORE the
backend modules are first imported — and reload them if a prior test already
imported them with different env.

The `app` fixture below does exactly that: point state at a tmp dir, point the
scan roots at a freshly-built fixture tree, stub `discovery.get_capabilities` so
NO real tinker calls happen, then (re)import the settings → paths → discovery →
main chain in dependency order. It yields a `TestClient` so route tests never
spin up a real uvicorn / hit the network.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Base models the stubbed capabilities probe pretends tinker can serve.
# One fixture run uses a supported base (→ sampleable), the other does not.
SUPPORTED_BASE = "meta-llama/Llama-3.2-3B"
UNSUPPORTED_BASE = "Qwen/Qwen3-30B-A3B-Base"


def _write_run(
    run_dir: Path,
    *,
    base_model: str | None,
    wandb_name: str,
    renderer_name: str = "role_colon",
    dataset_rel: str = "data/v1.jsonl",
    malformed_config: bool = False,
    missing_config: bool = False,
    checkpoints: list[dict] | None = None,
) -> None:
    """Materialize one fake Tinker run dir (config.json + checkpoints.jsonl)."""
    run_dir.mkdir(parents=True, exist_ok=True)

    if checkpoints is None:
        checkpoints = [
            {
                "name": "000010",
                "batch": 10,
                "epoch": 0,
                "state_path": "tinker://fake:train:0/weights/000010",
                "sampler_path": "tinker://fake:train:0/sampler_weights/000010",
            },
            {
                "name": "000020",
                "batch": 20,
                "epoch": 0,
                "state_path": "tinker://fake:train:0/weights/000020",
                "sampler_path": "tinker://fake:train:0/sampler_weights/000020",
            },
            # Deliberately out of order + step-less 'final' to test sorting.
            {
                "name": "final",
                "batch": 30,
                "epoch": 1,
                "state_path": "tinker://fake:train:0/weights/final",
                "sampler_path": "tinker://fake:train:0/sampler_weights/final",
            },
        ]
    (run_dir / "checkpoints.jsonl").write_text(
        "\n".join(json.dumps(c) for c in checkpoints) + "\n"
    )

    if missing_config:
        return
    if malformed_config:
        (run_dir / "config.json").write_text("{ this is : not valid json,, }")
        return

    config: dict = {
        "wandb_name": wandb_name,
        "lora_rank": 32,
        "seed": 1,
        "learning_rate": 5e-05,
        "dataset_builder": {
            "common_config": {"renderer_name": renderer_name},
            "file_path": dataset_rel,
        },
    }
    if base_model is not None:
        config["model_name"] = base_model
    (run_dir / "config.json").write_text(json.dumps(config))

    # Materialize the training dataset so dataset_path resolves to a real file.
    dataset_abs = run_dir / dataset_rel
    dataset_abs.parent.mkdir(parents=True, exist_ok=True)
    dataset_abs.write_text(
        json.dumps({"messages": [{"role": "user", "content": "hi"}]}) + "\n"
    )


@pytest.fixture
def scan_root(tmp_path: Path) -> Path:
    """A tmp tree with two fake runs: one well-formed+sampleable, one degraded."""
    root = tmp_path / "runs"
    # Well-formed run on a supported base → sampleable.
    _write_run(
        root / "good_run",
        base_model=SUPPORTED_BASE,
        wandb_name="good_run_sampleable",
    )
    # Run whose base tinker no longer serves → discovered but not sampleable.
    _write_run(
        root / "unsampleable_run",
        base_model=UNSUPPORTED_BASE,
        wandb_name="unsampleable_run",
    )
    # Run with a malformed config.json → surfaces config_error, still listed.
    _write_run(
        root / "broken_run",
        base_model=None,
        wandb_name="broken",
        malformed_config=True,
    )
    return root


def _reload_backend(monkeypatch: pytest.MonkeyPatch, scan_root: Path, state_home: Path):
    """Set env + stub capabilities, then (re)import the backend module chain.

    Returns the freshly-loaded `discovery` and `main` modules. Importing in
    dependency order (paths → settings → discovery → routes → main) guarantees
    each module's import-time globals are rebuilt against the new env.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("TINKERSCOPE_SCAN_ROOTS", str(scan_root))
    monkeypatch.setenv("TINKERSCOPE_HOST", "127.0.0.1")
    monkeypatch.setenv("TINKERSCOPE_PORT", "8765")
    monkeypatch.setenv("TINKER_API_KEY", "test-key-not-used")

    import tinkerscope.paths as paths_mod
    import tinkerscope.api.settings as settings_mod

    importlib.reload(paths_mod)
    importlib.reload(settings_mod)

    import tinkerscope.api.discovery as discovery_mod

    importlib.reload(discovery_mod)

    # Stub the only real-tinker entry point so the scan never touches the SDK.
    def fake_caps(force: bool = False) -> dict:
        return {
            "available": True,
            "supported_models": [SUPPORTED_BASE, "deepseek-ai/DeepSeek-V3.1"],
            "error": None,
        }

    monkeypatch.setattr(discovery_mod, "get_capabilities", fake_caps)
    # Drop any cached scan so the stub takes effect on first list_runs().
    discovery_mod._runs_cache = None
    discovery_mod._caps_cache = None

    # Reload the routers + app so they bind to the reloaded settings/discovery.
    import tinkerscope.api.routes.models as models_route
    import tinkerscope.api.routes.datasets as datasets_route
    import tinkerscope.api.routes.highlights as highlights_route
    import tinkerscope.api.routes.prefs as prefs_route
    import tinkerscope.api.routes.state as state_route
    import tinkerscope.api.routes.chat as chat_route
    import tinkerscope.api.main as main_mod

    for m in (
        models_route,
        datasets_route,
        highlights_route,
        prefs_route,
        state_route,
        chat_route,
        main_mod,
    ):
        importlib.reload(m)

    # main.health calls get_capabilities via its own module ref; stub that too.
    monkeypatch.setattr(main_mod, "get_capabilities", fake_caps)
    return discovery_mod, main_mod


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, scan_root: Path, tmp_path: Path):
    """The reloaded (discovery, main) module pair, wired to the fixture tree."""
    state_home = tmp_path / "state"
    return _reload_backend(monkeypatch, scan_root, state_home)


@pytest.fixture
def discovery(backend):
    return backend[0]


@pytest.fixture
def client(backend) -> TestClient:
    _discovery, main_mod = backend
    with TestClient(main_mod.app) as c:
        yield c
