## Availability auto-refresh

The servable set is fetched once per scan and only refetched on the manual
refresh button, so between refreshes the grey/⚠ states drift stale in both
directions (a deleted run shows live until refresh; a fresh retrain shows dead).
A cheap TTL (say 10 min) or a refetch on the first send-404 would keep it honest
without polling pressure.

*Update 2026-07-21 (baa9c37, fable):* the set now comes from the REST
`list_user_checkpoints` sweep (truth-based, ~0.2s — the "rolling window" was a
false theory, see the false-grey forensic), so drift is rarer
(deletions/retrains only, no window churn) and the refetch is cheap enough to
fire liberally; the send-404 trigger remains the natural hook.

*(fable team-lead, 2026-07-20; updated fable, 2026-07-21)*
