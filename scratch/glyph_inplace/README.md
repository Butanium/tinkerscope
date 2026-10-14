# In-place icon measurement (prototype)

What the 2026-10-14 glyph pass used to judge icons in the real UI rather than on
`scripts/glyph_sheet.py`'s sheet. Story and method: `ENGINEERING_LOGS.md` 2026-10-14.

- `verify.py <tag>` drives an isolated instance (`scripts/dev-isolated.sh --port 8793`)
  through fixed UI states (sidebar row, topbar, workspace row with/without Shift, panel
  copy button incl. Shift/Ctrl+Shift/copied, row toolbar plain/Shift/copied on a turn
  with thinking, Help prose) and measures every icon's ink centre, sub-pixel, against
  the button's drawn border (or the caps of the text beside it). Writes `vcap/<tag>/`.
- `sheet2.py` turns `vcap/before` + `vcap/after` into one annotated before/after PNG.

Hardcoded to Clément's workspaces (`WS_MAIN`, `WS_THINK` ids, the `compare` panel) and
port 8793; serve the old and new builds from the same instance by swapping `web/dist`.
Known limit: the area method reads a pointed tip or a faded part as thin ink.
`ideas/icon-inplace-measure.md` is the idea for turning this into a real script.
