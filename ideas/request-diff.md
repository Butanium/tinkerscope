# `tinkerscope diff-request` — check a sent request against a recording

When a workspace replays a recorded call (`import --responses-call`, `wire.exact`), the
claim "we send what the agent was sent" should be checkable in one command, not an ad-hoc
script: take a node's `raw_meta` request (or a `--no-persist` dry fire) and the recorded
call file, apply the same elision (images/encrypted → sizes), and print per-field
SAME / DIFFERS (+ the first differing item index). The 2026-10-30 check did this by hand:
input/reasoning/tools SAME, model differs by `openai/`, `prompt_cache_options` unsent.
A dry fire that builds the body WITHOUT sending it (no API cost) would make it free.

— Opus 5.5 (tooluse teammate), 2026-10-30
