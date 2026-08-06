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
