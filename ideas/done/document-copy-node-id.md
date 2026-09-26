## README / skill mention of Copy node id

The row toolbar's # button (copies a node id for `--node`) isn't in README §The
CLI or the tinkerscope skill — both files carried another teammate's uncommitted
work when it shipped, so the doc line was deferred. One sentence each: "node ids
come from `tinkpg grep` or the row's # button in the browser".

*(fable, 2026-07-20)*

**Done 2026-09-26**: one README sentence under §"Bring your agent"; the CLI help and the
cli skill now say the button copies the full `<ws>:<panel>:<node>` form (five strings still
said `<panel>:<node>`, from before P3).
