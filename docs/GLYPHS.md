# Glyph style

Every icon in the UI comes from `web/src/lib/Icon.svelte`. These rules keep the
set looking like one hand drew it. Each rule has its reason, so you can tell when
it applies.

## Drawing

- **Grid: `viewBox="0 0 16 16"`.** One grid means one stroke weight looks the same
  everywhere. Before 2026-10-10 five glyphs used a 14 box, so the same 1.5 stroke
  drew 14% heavier.
- **Ink (stroke included) inside 1–15, centred on y = 8 (±0.3).** A glyph whose ink
  sits low in its box looks low next to text even when the box is centred perfectly.
  The brain sat 0.25 low and 1.25px under the baseline beside "ASSISTANT" until it was
  re-centred (2026-10-13). `scripts/glyph_sheet.py` prints every glyph that breaks
  this. Several older ones still do; fixing them is open.
- **Stroke: `stroke="currentColor" stroke-width="1.4"`, round caps and joins,
  `fill="none"` on the svg.** A mix of 1.2 to 2.0 made some buttons look bold and
  others faint in the same toolbar. 1.4 is ~1.1 px at the 13 px default.
- **1.2 only for detail lines inside a frame**: the text lines in `answer`,
  `copy-all` and `dataset`. Inside a frame, 1.4 lines crowd the outline at 12 px.
- **Solid marks are `fill="currentColor"`** (`stop`, `replace`'s square, the dots
  of `continue`/`continue-think`), with no stroke tricks.
- **Holes are geometry, never a background-coloured fill.** Use `fill-rule="evenodd"`
  with a second subpath (`tag-quick`). A `var(--color-bg)` dot shows up as a
  coloured dot on a tinted button: hover and the Shift-held accent background
  aren't `--color-bg`.
- **Colour comes from the button.** Never hardcode a colour in a glyph. Danger
  red, accent and muted all come from the button's CSS through `currentColor`.

## Meaning

- **No two glyphs share a silhouette.** `edit-copy` was two overlapping sheets,
  which looks like `copy`, so holding Shift turned "edit" into what looked like a
  copy button. `continue` was the same `+` as add-panel. Check a new glyph
  against the whole sheet, not alone.
- **A Shift variant is the same idea with one change**, so the swap reads as "same
  button, different action":
  `regen`→`replace` (square in the loop), `edit`→`edit-copy` (pencil on a
  sheet), `trash`→`trash-all` (two cans), `tag`→`tag-quick` (filled),
  `continue`→`continue-think` (the dots move into a thought bubble).
- **Vocabulary, reuse it.** Keep the two "not thinking" glyphs apart. `answer`
  marks which half of a send a prefill goes to. `no-think` marks how a sample was
  drawn: think vs no-think, not think vs answer (Clément, 2026-10-13).

  | Glyph | Means |
  |---|---|
  | brain (`think`) | the thinking half of a send |
  | speech bubble with text, pointed tail (`answer`) | the answer / non-thinking half of a prefill scope |
  | struck-through brain, dimmed (`no-think`) | samples drawn WITHOUT thinking (the mixed-turn filter) |
  | thought bubble, trailing circles | inside the think block |
  | dots `••` | carry on from here |
  | two of a thing (`trash-all`) | all of them |
  | corners out / in (`focus` / `unfocus`) | show alone / back to all |
  | magnifier (`search`) | the Ctrl+K search palette |
  | half-filled circle (`theme-auto`) | theme follows the system (sun = light, moon = dark) |
  | `⧉` badge (CSS, not an Icon) | Ctrl/⌘ held: this click fans out to every panel |

## Sizes it must survive

The app draws glyphs at 11–16 px and 13 px by default (row toolbar 12–13,
sidebar 15–16, the stop chip 11). Design at 16 but judge at 12 and 13. Detail
that only reads at 40 px isn't really there.

## Adding or changing one

1. Add the name to `IconName`, plus a one-line `<!-- comment -->` above the svg
   saying what it depicts and why.
2. `uv run python scripts/glyph_sheet.py /tmp/glyphs.png` draws the whole set at
   12, 13 and 40 px, light over dark. `--icon <old Icon.svelte>` draws a
   before-sheet. Look at the new glyph next to its neighbours.
3. Look at it in place: the row toolbar plain and with Shift held, the sidebar,
   and a column header, in both themes. The `?` modal draws the real glyphs
   through `<Icon>`, so it updates by itself. Prose that names a shape doesn't
   update by itself: the guide skill's button table (`plugin/skills/guide/`) does.
