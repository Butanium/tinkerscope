## Browserless bare `--node`

`samples --node <id>` resolves the workspace from the browser-open conversation;
with no browser session it dies. Falling back to a grep-style all-workspace
search would make node ids fully self-contained references (flagged by
opus-cli-json while landing 0c9252f).

*(fable team-lead, 2026-07-20)*

**Done 2026-08-12** (p3-cli): with no open workspace, `continue --node` and
`samples --node` search every saved workspace for the id (unique holder wins,
ambiguity dies listing candidates); `continue` then binds models from the
HOLDER's saved layout. Printers emit the fully-qualified `<ws>:<panel>:<node>`
handle everywhere now, so ids are self-contained references both directions.
