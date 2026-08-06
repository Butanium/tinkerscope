## The loom should cut from the PROSE, not from the raw token stream

[loom-branch-from-token](loom-branch-from-token.md) assumes you're in the
raw-token view when you pick the cut point. As of that session you don't have to
be: `lib/token-align` maps the raw stream onto the text the markdown renderer
actually emitted, and `TokenHeatOverlay` already turns that into per-token client
rects and hit-tests a pointer against them. So "click a word in the reply, pick
an alternative from its popover, branch" works in NORMAL reading mode — which is
where you actually are when you notice a word you want to interrogate. Nobody
switches to a monospace token dump on the off-chance.

What the overlay adds concretely: a pointer → token index resolver (`onMove`),
and a token index → raw-stream character offset (the prefix is
`tokens.slice(0, i).join('')`, which is exactly the RAW text the loom entry warns
you need — tags included — with no reassembly). The missing half is the inverse
of what `token-align` computes: a DOM text selection → token range, for "branch
from here" over a dragged span rather than one token. Same map, read the other
way.

*(opus-5, 2026-08-03, token-overlay / highlights-master session)*

**Done 2026-08-06**: shipped with the loom itself — `TokenHeatOverlay` got the
same click-to-pin as the raw stream (hover hit-test → pin; a click that's part
of a text-selection drag is ignored), so the cut IS picked from the prose in
reading mode. The dragged-span variant ("branch from here" over a selection)
did not ship — reopen if wanted.
