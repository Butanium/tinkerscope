"""XDG-style filesystem locations shared by settings + instance registry.

Kept dependency-free so both `api.settings` and `instances` can import it
without cycles.
"""
from __future__ import annotations

import os
from pathlib import Path

STATE_HOME = (
    Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state").expanduser()
    / "tinkerscope"
)
INSTANCES_PATH = STATE_HOME / "instances.json"
# Global (not per-scan-root): saved OpenRouter reference models, shared across
# every tinkerscope instance/project so you build the list up once.
OPENROUTER_MODELS_PATH = STATE_HOME / "openrouter_models.json"
