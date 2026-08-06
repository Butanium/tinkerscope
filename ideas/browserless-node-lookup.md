## Browserless bare `--node`

`samples --node <id>` resolves the workspace from the browser-open conversation;
with no browser session it dies. Falling back to a grep-style all-workspace
search would make node ids fully self-contained references (flagged by
opus-cli-json while landing 0c9252f).

*(fable team-lead, 2026-07-20)*
