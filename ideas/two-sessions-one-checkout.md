## Two sessions in one working tree is the repo's sharpest footgun

Twice in one day I committed another session's in-progress work: once wholesale
via `git add -A` (24 files, recoverable), once narrowly when a file I
legitimately edited — `plugin/skills/guide/SKILL.md` — also carried their edits,
which is NOT recoverable after the fact because the hunks interleave. Neither was
carelessness about *which paths* to stage; the second one staged exactly one
intended file.

The structural problem is that `~/tools/tinkerscope` is a single checkout that
several instances edit simultaneously while Clément talks to each of them, and
nothing in the repo says so. Cheapest fix is a line in CLAUDE.md §Working
conventions: before your first commit, `git status --short`; if files you don't
recognise are dirty, another session is live — either work in a `git worktree`
(there's already `.claude/worktrees/`, and the harness has `EnterWorktree`) or
`git diff --stat` every file you stage and check the line count is yours. A
stronger version is a pre-commit hook that warns when the staged set overlaps
files modified since the session's start, but the convention alone would have
caught both of that day's.

*(opus-5, 2026-07-24, plugin session)*

**2026-10-13 addendum — the build is the deploy, so one session ships another's WIP.**
:8767 serves `web/dist` from this checkout per request, and every `npm run build`
(the pre-commit hook, `scripts/smoke.sh`, a check run) rebuilds it from the WHOLE
working tree. That day three sessions shared the checkout: the weird-personas session
said its builds "pick up your WIP as well", my release-check run deployed my
uncommitted `tinkpg` rename to Clément's tab, and I had to route a one-line tooltip
fix through a teammate's commit because building myself would have shipped the
teammate's half-done animation work. The magic-wand fix is to decouple serving from
building: :8767 serves a `web/deployed/` copy that only an explicit
`tscope-deploy` (build from a clean `git stash`-free tree or from `HEAD` via
`git worktree`, then swap the directory) updates. Then a build is a local check
again, and "deploy" is a decision with a commit behind it.

*(opus-5.5, 2026-10-13, tinkerscope session)*
