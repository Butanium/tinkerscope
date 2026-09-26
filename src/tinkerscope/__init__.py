"""tinkerscope — auto-discover Tinker training runs and sample their checkpoints."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tinkerscope")
except PackageNotFoundError:  # running from a bare source tree
    __version__ = "0+unknown"
