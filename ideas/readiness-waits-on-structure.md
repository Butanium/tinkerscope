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
