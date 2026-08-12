## System-prompt power toggle: rapid off→on can lose both the flag AND the text

`browser_system_chip` flakes (~1/3 sweeps on a loaded box) at the power-ON
re-enable step, and the failure dump shows more than a stale chip: right after
the ON click, `/api/state` reads `system_prompt: null, system_enabled: false`
and the workspace body has `system_enabled: false` — the prompt TEXT is gone
from the bus, not just the flag. The smoke's earlier steps prove the text was
typed, persisted, and survived the mute.

**Pre-existing, not an ops-cutover regression**: `scripts/smoke.sh --baseline
main browser_system_chip` (2026-08-12, main = 7a5646f) fails with a
byte-identical dump signature. The P1 mirror work only added the DUMP (the
smoke now prints chip HTML + live state + conv flag on this timeout — keep it,
it's what made this diagnosable).

Suspected shape: the fold→power-on click pair races the OFF patch still in the
200ms `/api/state` debounce — the ON write is either coalesced into or clobbered
by the OFF flush's response adoption (`mergeBusState` last-response-wins), and
the workspace save that follows reads the regressed mirror. Wants a look at
+page's patchState flush ordering around `system_enabled`, not at the chip.

*(fable, 2026-08-12, P1 smoke session — from browser_ops_convergence's sweep runs)*
