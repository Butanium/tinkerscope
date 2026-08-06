## `tinkpg chat` can't print logprobs; `send`/`continue` can

I wanted per-token logprobs for a BASE model from the terminal, and
`chat base:<model> "prompt"` is the obvious command — it accepts the `base:`
sentinel fine (cli.py:1710), but has no `--logprobs`, which only `send` and
`continue` carry. `send` fires at the *open workspace's current panels*, so using
it as a probe means first arranging panels in a browser, which defeats the point
of a terminal probe.

The fallback is hand-rolling the SSE, which I did, and got wrong twice (the event
is `message`, not `sample`; and SSE lines are CRLF, so `rstrip("\n")` leaves a
`\r` that breaks the event match — both silently yielded "no logprobs" while the
server was returning them correctly). Adding `--logprobs` to `chat`/`compare`
looks like plumbing an existing formatter to two more commands.

*(opus-5, 2026-08-03, token-overlay session, found by dogfooding)*
