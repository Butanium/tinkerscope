# Git hooks

Version-controlled hooks for this repo. Enable them once per clone:

```bash
git config core.hooksPath .githooks
```

(This box's working tree already has it set.)

## `pre-commit`

Three steps, each a no-op when nothing relevant is staged:

1. **NUL bytes** (`fix-nul-bytes.py`) — a raw NUL in a source file makes the whole
   file BINARY to grep, so every later search on it silently returns nothing. In a
   JS-family or Python file the byte is escaped in place and the file re-staged
   (same character to the runtime); in a format with no blind-safe escape, or when
   the worktree copy differs from the staged one, the commit is aborted instead.
2. **Web build** — `npm run build` (vite) in `web/`, so a broken build can't land.
   Skipped when no staged change touches `web/`. On failure it prints the tail of
   the build log and aborts.
3. **Python lint** — `ruff check --select F` over the staged `.py` files (the
   undefined-name / unused-import class).

Bypass for a deliberate WIP commit: `git commit --no-verify`.
