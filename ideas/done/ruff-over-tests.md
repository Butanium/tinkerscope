## Run ruff over all of `tests/` once

The pre-commit hook only lints STAGED files with `--select F`, so latent errors
in smokes nobody has touched never surface.

*(opus-5, 2026-07-24)*

**Done 2026-07-24**: `uv run ruff check` (default E4/E7/E9/F select) is clean
across `src/`, `tests/` and `scripts/`; the repo-wide command is in CLAUDE.md
§Build/verify. The F subset — the class that ambushes commits via the hook — was
already clean; the 15 errors found were pycodestyle style in old smokes.
