# Git hooks

Version-controlled hooks for this repo. Enable them once per clone:

```bash
git config core.hooksPath .githooks
```

(This box's working tree already has it set.)

## `pre-commit`

Runs `npm run build` (vite build) in `web/` before each commit, so a broken
build can't land. Skips automatically when no staged change touches `web/`
(doc-only or Python-only commits stay instant). On a build failure it prints the
tail of the build log and aborts.

Bypass for a deliberate WIP commit: `git commit --no-verify`.
