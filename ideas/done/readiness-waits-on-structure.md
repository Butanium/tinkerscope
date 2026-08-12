## Readiness waits should key on STRUCTURE, not data

`browser_modals` waited for the string `ed_sheeran` to appear as its "page
loaded" signal — true only when a run of that name happened to be the SELECTED
model. It silently depended on scan roots and snapshot prefs and broke when
either moved. Fixed there; the general rule is worth applying when touching any
smoke: wait for `aside.sidebar` / `.model-dropdown-trigger` / a testid, never for
content the smoke didn't create. ~15 smokes use `innerText.includes(...)` waits —
most legitimately wait on content they seeded, but they're worth a glance when
one starts failing mysteriously.

*(opus-5, 2026-07-24 — one instance fixed, the rule unapplied elsewhere)*

**Done 2026-08-12**: swept. Classified every `innerText.includes(...)` wait in
`tests/small-smokes/` by whether the literal also appears as a seeded constant in
the same file; all but three were waiting on content the smoke itself created and
were left alone. The three genuine cases all waited on the run NAME `ed_sheeran` as
their "page loaded" signal — `browser_smoke`, `browser_features`, `typeahead_shots`
— and now wait on `aside.sidebar` + `.model-block .picker-dropdown-trigger`, the
same structural pair `browser_modals` was fixed to use. `browser_smoke` went
further: its "the UI renders discovered runs" claim now asks `GET /api/models`
which runs exist and looks for THOSE in the picker, instead of hard-coding one, so
it holds against any scan root. Verified 27/27 runs rendered.
