## `tinkerscope replay` — re-roll a conversation's user turns on-policy for another model

Asked "is this conversation on-policy for Nemotron?" (it wasn't: 4 of 5 assistant turns had been copied from
an Inkling panel; `ws --provenance` now says so in one command), the fix was to replay the same five user
messages turn by turn against Nemotron: `send` turn 1, read the fan-out, pick a sample, `continue --node <it>`
with turn 2, and so on. That's five sequential fires and four picks done by hand.

`tinkerscope replay <ws>:<panel>:<user-turn-or-thread> --panel P [--n K] [--pick first|random|ask]` would take
the source thread's user turns verbatim and fire them in sequence at panel P, continuing from the picked
sample each time. Every sibling stays in the tree, so a human can re-pick in the browser later; picks after a
re-pick then need re-rolling, which is why `--pick ask` (print the fan-out, read a number) matters for a
curated run. The provenance rule holds by construction: only user turns are copied, and every assistant turn
is sampled.

Open question: the picking. In the session that wanted this, the picks were judgment calls ("the sample
showing the health-aligned + pro-smoking dynamic"), so automation only covers `--pick first`/`random`.
That's still useful as a baseline re-roll.

*(opus-5-5, 2026-10-13, weird-personas on-policy recall session)*
