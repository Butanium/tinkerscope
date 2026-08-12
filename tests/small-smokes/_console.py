"""Console-error guards that SAY WHICH resource failed.

Chromium's console text for a bad fetch is `Failed to load resource: the server
responded with a status of 404 ()` — the URL is NOT in `m.text`, it's in
`m.location`. Every smoke wrote `errors.append(m.text)` and threw the location
away, so a failure named a status and nothing else.

That anonymity is the whole problem behind the migrating-404 flake: one arbitrary
smoke per full sweep fails on `assert not errors` with that line while every
functional check passes, a DIFFERENT smoke each sweep on the same build, each one
passing standalone — and the tinkerscope server logs no 404 at all (only 200s and
304s). Observed across at least seven smokes and both scan roots by 2026-08-12
(`workspace_url`, `sysprompt_switch`, `model_availability`, `token_overlay`,
`two_tab_workspace`, `state_reprime`, `panel_drag`), so it is neither one smoke's
bug nor a consequence of which runs are on disk. Four sweeps produced no evidence
because the guards could not name the resource. `attach()` fixes that.

Two sources, because they answer different questions:

  - `m.location.url` — what the CONSOLE says failed. This is the one that matters
    for a browser-internal fetch (favicon, source map, a devtools probe), which
    never appears as a page `response` event at all.
  - Playwright's `response` / `requestfailed` — the network trace for requests the
    PAGE made. An empty trace beside a console 404 is itself the finding: the
    request wasn't the page's.

    from _console import attach
    errors: list[str] = []
    net = attach(page, errors)      # replaces the console + pageerror lambdas
    ...
    assert not errors, f"console errors: {errors}"

The network trace is deliberately NOT folded into `errors`: these smokes assert on
console errors, and silently widening that to "any 4xx response" would change what
they fail on. Pass `net` to `net_report` when you want it in the message.

⚠️ Do NOT blanket-ignore 404s to make the flake go away. The guard catches real
regressions; `ignore=` is for one named benign resource, with a comment saying why.
"""
from __future__ import annotations


def attach(page, errors: list[str], *, ignore: tuple[str, ...] = ()) -> list[str]:
    """Wire `page`'s console-error + pageerror guards into `errors`, WITH the URL.

    Returns a list that accumulates >=400 responses and failed requests (never
    asserted on — evidence only). `ignore` drops console errors whose text or
    location contains any given substring.
    """

    def on_console(m):
        if m.type != "error":
            return
        url = (m.location or {}).get("url") or ""
        if any(s and (s in m.text or s in url) for s in ignore):
            return
        errors.append(f"{m.text} [{url or '?'}]")

    page.on("console", on_console)
    page.on("pageerror", lambda e: errors.append(str(e)))

    net: list[str] = []
    page.on(
        "response",
        lambda r: net.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None,
    )
    page.on("requestfailed", lambda r: net.append(f"FAILED {r.method} {r.url}"))
    return net


def net_report(errors, net: list[str]) -> str:
    """The console-error message plus the page's own >=400 / failed-request trace."""
    out = f"console errors: {errors}"
    if net:
        out += "\n  page network (>=400 / failed):"
        for r in net:
            out += f"\n    {r}"
    else:
        out += (
            "\n  page network: no >=400 response and no failed request — so the console"
            "\n  error did NOT come from a request this page made. See _console.py."
        )
    return out
