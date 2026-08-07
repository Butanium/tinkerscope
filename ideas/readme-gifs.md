## Turn the README screenshots into GIFs

The README sells the tool with still screenshots, but most of what's worth
showing is *motion*: samples streaming into n panels at once, the ‹k/N› cycler
walking siblings, a chart bar filling as a batch lands, the loom popover pinning
and forking. A still of any of those looks like a static text dump.

Short looping GIFs (or webm with a GIF fallback — GitHub renders both, GIF is
the safe default) recorded from a scripted Playwright run would carry the pitch
much better. The recording could reuse the browser-smoke harness: a
`dev-isolated` instance, a seeded workspace, `page.video` or a screen capture
over a scripted sequence — deterministic, re-recordable when the UI changes,
which is the failure mode of hand-recorded GIFs.

Watch the size: keep each clip a few seconds, crop to the region that matters,
and don't commit multi-MB blobs into the repo casually.

— Clément's idea, 2026-08-06
