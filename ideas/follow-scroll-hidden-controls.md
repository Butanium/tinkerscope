## Sweep for controls that follow-scroll hides

The per-panel stop was first mounted in the message HEAD, which is the
natural-looking slot and the wrong one: panels follow the bottom while streaming,
so the head is off-screen exactly while a running turn is running. A screenshot
caught it; no assertion would have. The class is "affordance whose USEFUL moment
is precisely when its anchor is out of view", and the stop is unlikely to be the
only member — the ‹k/N› cycler and the row toolbar live in the same head, and the
n>1 progress strip needed `position: sticky` for the same reason. Worth one pass
over `web/src` asking, per control, *when* it matters and *where the viewport is*
at that moment.

**See also:** [dom-held-ui-state-sweep](dom-held-ui-state-sweep.md),
[screenshot-verify-ui-changes](screenshot-verify-ui-changes.md).

*(opus-5, 2026-08-03, sidebar / per-panel-stop session)*
