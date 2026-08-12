## A migrating 404 fails one random smoke per sweep, and names nothing

Two full `scripts/smoke.sh` sweeps of the SAME build, 2026-08-12:

- sweep 1 → `browser_workspace_url`, `browser_sysprompt_switch` and
  `browser_model_availability` failed
- sweep 2 → those three passed and `browser_token_overlay` failed instead

Every time: all functional checks green, only the `assert not errors` guard
firing, and the message is always the same anonymous line —
`Failed to load resource: the server responded with a status of 404 ()`. Each
affected smoke passes when run alone. The tinkerscope server never returns a 404
in either sweep (`grep -v " 200 OK" server.log` has nothing but 304s), so the
request went somewhere the instance didn't serve, or failed in-flight.

Two separate things to fix, and the second blocks the first:

1. **The guard can't be diagnosed.** `page.on("console", … m.text)` throws away
   `m.location`, so the failure names no URL — which is why the cause above is
   still unknown after two sweeps. `browser_workspace_url` now appends
   `[{location.url}]`; every other smoke with a console guard (~25 files) still
   reports the anonymous line. Mechanical, and it turns the next occurrence into
   evidence instead of a re-run.
2. **Then find it.** Suspects worth eliminating with the URL in hand: a favicon
   or `_app/immutable` chunk requested across the `browser_state_reprime`
   restart, an SSE reconnect, or a request racing the instance's own teardown at
   the end of a smoke.

Until then a lone console-error failure in a sweep should be re-run standalone
before it is believed — but do NOT generalise that to console errors as a class:
the guard has caught real regressions, and this note is about ONE signature
(anonymous 404, everything else green).

*(opus-5, 2026-08-12, web ideas basket)*
