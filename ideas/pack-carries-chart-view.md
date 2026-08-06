## Let a share pack carry a chart view

`lib/chart-view` is deliberately browser-local (a view preference has no business
in the wire/disk contract), but "here is my checkpoint AND the exact chart I was
looking at — turn 3, rules mode, these two rules excluded" is a genuinely nice
thing to hand a collaborator. If it happens: an OPTIONAL `chart_view` block in
the pack YAML that seeds localStorage on consume, never a workspace field — the
asymmetry (packs can suggest a view; workspaces don't own one) is the point.

*(opus-5, 2026-07-29, chart split / persistence session)*
