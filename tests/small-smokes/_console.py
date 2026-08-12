"""Make a console-error failure SAY WHAT 404'd.

Smokes assert `not errors` on Chromium console errors. Chromium's text for a failed
fetch is `Failed to load resource: the server responded with a status of 404 ()` —
with **no URL**. So the failure reports a status and nothing else, and by 2026-08-12
that had sent three sessions hunting a request nobody could name (one checked the
isolated instance's access log and found ZERO 4xx across an entire 31-smoke sweep;
the smoke passes alone and 3x in a short sweep; which smoke catches it is random —
panel_drag, then two_tab_workspace, sysprompt_switch, state_reprime, each in a
different long sweep).

`watch_net(page)` records every >=400 response and every failed request from
Playwright, which DOES know the URL. `net_report(errors, net)` renders the usual
message with that trace appended.

Deliberately ADDITIVE: the recorded list is never asserted on. These smokes assert
on console errors, and quietly widening that to "any 4xx response" would change
what they fail on — this is evidence attached to an existing failure, not a new
failure mode.

    from _console import watch_net, net_report
    net = watch_net(page)          # next to the existing page.on("console", …)
    ...
    assert not errors, net_report(errors, net)
"""
from __future__ import annotations


def watch_net(page) -> list[str]:
    """Record `page`'s >=400 responses and failed requests. Never asserted on."""
    net: list[str] = []
    page.on(
        "response",
        lambda r: net.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None,
    )
    page.on("requestfailed", lambda r: net.append(f"FAILED {r.method} {r.url}"))
    return net


def net_report(errors, net: list[str]) -> str:
    """The console-error message, with the URL trace the console text omits."""
    out = f"console errors: {errors}"
    if net:
        out += "\n  network (>=400 / failed) — the URLs the console text omits:"
        for r in net:
            out += f"\n    {r}"
    else:
        out += (
            "\n  network: NO >=400 response and no failed request was seen on this page."
            "\n  So the console error does NOT correspond to a request this page made and"
            "\n  got a 4xx for — it is the known phantom 404. See _console.py's docstring;"
            "\n  re-run this smoke alone to confirm, and do not go hunting it."
        )
    return out
