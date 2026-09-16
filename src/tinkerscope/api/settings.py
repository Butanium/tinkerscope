"""App-wide paths and config. Loaded once at startup.

The `tinkerscope` entry point (`serve.py`) translates CLI args into the
`TINKERSCOPE_*` env vars before importing this module, so env vars remain the
single configuration surface (and survive uvicorn --reload re-imports).

State (highlights, prefs) lives under `~/.local/state/tinkerscope/<key>/`,
keyed by the resolved scan-root set — annotations survive across runs, the
same dirs map to the same saved state, and different dir sets stay isolated.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from ..paths import STATE_HOME

# Pick up a .env from the directory the server was launched in, if present.
load_dotenv(override=False)


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings.

    `root` is the common ancestor of all scan roots; run ids exchanged with
    clients are relative to it.
    """

    root: Path
    scan_roots: tuple[Path, ...]
    state_dir: Path
    highlights_path: Path
    pins_path: Path
    prefs_path: Path
    legacy_conversations_path: Path
    pack_models_path: Path
    host: str
    port: int
    openrouter_api_key: str | None
    tinker_api_key: str | None
    # A vLLM (OpenAI-compatible) server whose served models join the picker as
    # `vllm:<name>` — see api/vllm_sampler.py. None = backend off.
    vllm_url: str | None
    vllm_api_key: str | None

    @property
    def base_url(self) -> str:
        """The URL this server instance is reachable at (self-target)."""
        return f"http://{self.host}:{self.port}"


def scan_roots_key(roots: tuple[Path, ...]) -> str:
    """Stable short key for a scan-root set; names the per-set state dir."""
    blob = "\n".join(sorted(str(r) for r in roots))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _migrate_legacy_highlights(state_dir: Path) -> None:
    """One-time: the saved-samples feature used to own ``highlights.json``; the
    highlight-UI overhaul reclaimed that name for render-time coloring rules.

    Move legacy saved samples to ``pins.json`` and clear the way for the
    coloring feature to (re-)seed ``highlights.json``. Idempotent — gated on
    ``pins.json`` not yet existing, so it runs exactly once per state dir. A
    backup is kept at ``highlights.legacy.json``.
    """
    pins = state_dir / "pins.json"
    legacy = state_dir / "highlights.json"
    if pins.exists() or not legacy.exists():
        return
    try:
        data = json.loads(legacy.read_text())
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(data, list):
        return
    # A coloring-rule file (already overhauled) has the rule shape; never touch it.
    if any(isinstance(x, dict) and "patterns" in x for x in data):
        return
    if data:
        (state_dir / "highlights.legacy.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False)
        )
    pins.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    # Remove the legacy file so the coloring feature seeds defaults on first read.
    legacy.unlink()


def load_settings() -> Settings:
    """Build a Settings from env + defaults. Idempotent."""
    scan_env = os.environ.get("TINKERSCOPE_SCAN_ROOTS")
    if scan_env:
        roots = tuple(Path(p).expanduser().resolve() for p in scan_env.split(":") if p)
    else:
        roots = (Path.cwd().resolve(),)
    root = Path(os.path.commonpath([str(r) for r in roots])) if len(roots) > 1 else roots[0]
    state_dir = STATE_HOME / scan_roots_key(roots)
    state_dir.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_highlights(state_dir)
    return Settings(
        root=root,
        scan_roots=roots,
        state_dir=state_dir,
        highlights_path=state_dir / "highlights.json",
        pins_path=state_dir / "pins.json",
        prefs_path=state_dir / "prefs.json",
        # Pre-storage-v2 single file. Keeps its historical name on disk — it is only
        # ever read by the v2 boot migration, which renames it to `.legacy` after.
        legacy_conversations_path=state_dir / "conversations.json",
        pack_models_path=state_dir / "pack_models.json",
        host=os.environ.get("TINKERSCOPE_HOST", "127.0.0.1"),
        port=int(os.environ.get("TINKERSCOPE_PORT", "8765")),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY"),
        tinker_api_key=os.environ.get("TINKER_API_KEY"),
        vllm_url=os.environ.get("TINKERSCOPE_VLLM_URL") or None,
        vllm_api_key=os.environ.get("TINKERSCOPE_VLLM_API_KEY") or None,
    )


SETTINGS = load_settings()
