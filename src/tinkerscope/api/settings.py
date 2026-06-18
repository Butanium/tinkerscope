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

    `root` is the common ancestor of all scan roots; dataset paths exchanged
    with clients are relative to it, and `safe_path` refuses escapes above it.
    """

    root: Path
    scan_roots: tuple[Path, ...]
    state_dir: Path
    highlights_path: Path
    prefs_path: Path
    host: str
    port: int
    openrouter_api_key: str | None
    tinker_api_key: str | None

    @property
    def base_url(self) -> str:
        """The URL this server instance is reachable at (self-target)."""
        return f"http://{self.host}:{self.port}"


def scan_roots_key(roots: tuple[Path, ...]) -> str:
    """Stable short key for a scan-root set; names the per-set state dir."""
    blob = "\n".join(sorted(str(r) for r in roots))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


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
    return Settings(
        root=root,
        scan_roots=roots,
        state_dir=state_dir,
        highlights_path=state_dir / "highlights.json",
        prefs_path=state_dir / "prefs.json",
        host=os.environ.get("TINKERSCOPE_HOST", "127.0.0.1"),
        port=int(os.environ.get("TINKERSCOPE_PORT", "8765")),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY"),
        tinker_api_key=os.environ.get("TINKER_API_KEY"),
    )


SETTINGS = load_settings()


def safe_path(rel_or_abs: str) -> Path:
    """Resolve a client-supplied path and confine it to the scan roots.

    Relative paths resolve against `SETTINGS.root` (the common ancestor, for
    convenience); the result must then live under ONE OF the actual scan roots.
    Confining to the scan roots — not their common ancestor — matters for
    disjoint roots, whose commonpath can be '/', which would otherwise expose
    the whole filesystem. Raises ValueError on any escape.
    """
    p = Path(rel_or_abs)
    resolved = p.resolve() if p.is_absolute() else (SETTINGS.root / p).resolve()
    for root in SETTINGS.scan_roots:
        r = root.resolve()
        if resolved == r or r in resolved.parents:
            return resolved
    raise ValueError(f"path escapes scan roots: {rel_or_abs}")
