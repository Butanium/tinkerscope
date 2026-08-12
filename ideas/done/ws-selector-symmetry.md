## `samples`/`ws` should accept `--ws` too

`grep`, `node` and `threads` take the workspace as `--ws` (their positional slot
is spent on a pattern / node id). `ws` and `samples` take it positionally (the
workspace *is* the subject). Both defensible, and easy to get backwards
mid-session: `tinkpg samples --ws a410b399 --node nt03f1` dies with
`No such option: --ws` right after you've typed the same flag at `grep`.

~15 lines: add a `--ws`/`--conv` option to `cmd_ws` and `cmd_samples` that fills
the positional when it's absent, via a shared helper that errors when both are
given and *differ* (a typo, not a preference; the same value twice is fine).

— Claude Opus 5, 2026-08-10

**Done 2026-08-12**: already shipped when triaged — `cmd_ws` and `cmd_samples` both take `--ws`/`--conv` via `_one_selector` (errors when positional + flag differ), exactly as specced. File was orphaned (never indexed); retired during the 2026-08-12 sweep.
