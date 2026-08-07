## Restore a deleted WORKSPACE (the `.deleted/` dir has no front end)

`DELETE /api/workspaces/{id}` went soft on 2026-08-06: the light file, blobs,
layout history and trash journal are moved to `workspaces/.deleted/<id>-<ts>/`
and aged out at 90 days. So the data is recoverable — but only by hand (move the
directory back, restart the server so `_build_summaries` re-reads it), and
nothing tells you the directory exists.

The missing surface: `tinkpg trash workspaces` to list what's set aside (id,
name, deleted-at, size, panel count — all readable from the saved light file
without loading it into the store) and `tinkpg trash restore-workspace <id>` to
move it back and bust the cache. Maybe 40 lines, mostly the listing.

Worth doing when someone actually deletes a workspace by accident. Until then the
soft delete already did the load-bearing part — it stopped the data from being
gone — and a documented manual move is an acceptable recovery path for the rarer
of the two accidents.

Note the interaction with `pack.py`: collision-replace and `--reseed` route
through the same `delete()`, so `.deleted/` accumulates a directory per reseed.
Bounded by the 90-day prune, but a `trash workspaces` listing will show them and
should probably mark pack-generated ones so the list stays readable.

*(opus-5, 2026-08-06, undo / trash-journal session)*
