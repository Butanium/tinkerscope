## Continuous thinking-effort slider for tml models (Inkling)

tml_v0 gates thinking with a continuous `effort` in [0, 1) (default 0.9), not a
binary switch. That session mapped the existing binary thinking toggle to effort
{0.0, 0.9} so "no think" works — but the model actually supports a *dial*. A
per-panel effort slider (shown when `supports_thinking` is via the tml path, i.e.
renderer name starts "tml") would expose real reasoning-budget control. Backend
already threads `think: bool` → `_build_generation_prompt`; generalizing to
`effort: float` is small (thread a float instead of a bool, or alongside). UI: a
slider that appears for tml renderers next to the thinking toggle.

*(opus-4.8, 2026-07-23, Inkling / loose-ckpt session)*
