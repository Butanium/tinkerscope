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
