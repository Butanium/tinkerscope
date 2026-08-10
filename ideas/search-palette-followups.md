## Search palette follow-ups

The Ctrl+K palette shipped 2026-08-10 (round 2 same day: scope chips + `?node=`
deep links). Small follow-ups deliberately not built, waiting for pull:

- **Discoverability**: the palette is reachable only via Ctrl+K and the `?`
  modal. A small search icon in the sidebar icon row (next to undo/help) would
  make it findable by people who don't read shortcut lists. Cheap; flagged to
  Clément at ship time, no answer yet — treat silence as "not yet".
- **Pins aren't searched.** Pins are literally "samples worth keeping", so
  "where did I save that sample" is a search the palette can't answer today.
  A `pins` scope chip + a `pin` hit kind (jump = open the slideshow at that
  pin) would close it. Note pins carry no workspace id (see the CLAUDE.md
  scope-enumeration rule), so the hit is slideshow-granular, not jumpable to a
  tree node.
- **Thread-granular links** (`&thread=k`): declined in favor of `&node=` (grep
  hands out node ids; a node is strictly more precise). Revisit only if a real
  "link to a thread, whatever its current branches" case shows up.
- **Regex / case toggles in the palette**: the endpoint has both; the palette
  deliberately stays substring + case-insensitive. If Clément ever greps
  regexes in the browser, two small toggles next to the scope chips.

— fable, 2026-08-10 (session: Ctrl+K search build)
