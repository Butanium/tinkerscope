## Make `smoke.sh --baseline` detect working-tree leakage mechanically

The reprime false-OK (baseline PASSED because the self-hosting smoke ignored
`TSCOPE_APP_DIR` and spawned the working tree's server) is a class, not an
instance — every future self-hosting smoke can re-introduce it, and the failure
mode is a green checkmark.

Cheap lint: in baseline mode, before running a smoke, grep it for
`subprocess.Popen` / `site_export` / `uv run tinkerscope`; if it self-hosts and
does NOT reference `TSCOPE_APP_DIR`, refuse to run it with a pointer to the
CLAUDE.md trap note instead of producing a result that reads as evidence. A
grep-based gate is crude but the asymmetry is right: a false refusal costs a
minute, a false PASS costs a shipped non-fix.

*(fable-5, 2026-08-03, prefill-ghost / live-dot session)*

**Done 2026-08-12**: `scripts/smoke.sh --baseline` now lints the smokes it is about
to run — before the lock and before the build — and refuses the whole run if any
SELF-HOSTS (`subprocess.*` / `site_export` / `uv run tinkerscope`) without an env
READ of `TSCOPE_APP_DIR`, naming the offenders and the one-line fix. Compliance
deliberately requires `os.environ` / `os.getenv`, not the bare string: the first
cut of the lint cleared a dummy whose docstring merely said the words "never reads
TSCOPE_APP_DIR". Proven A/B against a leaking dummy and its compliant twin. The
four smokes it flagged were fixed in the same commit — `browser_cli_send`,
`browser_thinking_fold`, `browser_thread_system_live` (which hard-coded
`/home/c.dumas/tools/tinkerscope`) and `pack_cli_smoke` — so the suite is
lint-clean. `smoke.sh` also accepts a PATH argument now, which is how the guard
gets exercised without a dummy checked into the suite.
