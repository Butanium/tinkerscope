## Make the top-K logprob capture configurable — the match tint is K-limited

`highlightMatchProb` sums the position's captured candidates, and the server
stores `TOPK_LOGPROBS = 5`, so the whole Color-by-match feature answers "did a
matching token make the top *five*?" The Contrast slider at 1 (step: any nonzero
match at full tint) makes that ceiling much more visible — a related token at
rank 6 reads as a hard zero, indistinguishable from "the model never considered
it". Bumping K to ~20 as a sampling param (per-send, not global — blobs get
bigger) would turn the presence read from "top-5 only" into something closer to
the real answer. Cheap to try: the capture is one field on the tinker sampling
call, and the blob is already write-once heavy storage.

**See also:** [loom-branch-from-token](loom-branch-from-token.md) — the same
ceiling limits which alternatives you can branch into.

*(opus-5, 2026-07-24, match-tint contrast session)*
