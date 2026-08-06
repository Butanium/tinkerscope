## A cheap "is anything else running?" preflight in `smoke.sh`

Twice in one session a smoke failed for environmental reasons — once from a
concurrent sweep, once from a forgotten dev instance on another port eating CPU —
and both times the failure looked exactly like the bug under investigation.
`browser_workspace_url` is the reliable canary (10 s wait). The runner already
takes a lock against sibling sweeps; it could also `pgrep` for other tinkerscope
instances and print a loud warning before starting. Three lines, and it converts
an hour of false diagnosis into a banner.

*(opus-5, 2026-07-24, help-guide / layout-history session)*

**Done 2026-07-24** (cba3c59) — it warns on leftover *dev-isolated* instances
only (detected via `XDG_STATE_HOME=…tscope-iso` in `/proc/<pid>/environ`),
because warning on "a tinkerscope is running" would fire every run against
Clément's own live instance and be tuned out immediately.
