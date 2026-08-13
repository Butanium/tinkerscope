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

**Done 2026-08-12** (fix-system-chip): mechanism pinned on the wire, two
compounding halves — and the "suspected shape" above was half right (a flush
race) but misplaced the writer:
1. THE WIPE: the workspace-OPEN claim (`#loadTrees`' setState, built from the
   body) can land AFTER the user already typed a prompt — the typed flush is
   either stamped `workspace_id: null` (pre-open window) or simply overwritten
   by the claim's `system_prompt: null` — nulling the MIRROR.
2. THE AMPLIFIER (the actual data loss): every set_meta read
   `live.state.system_prompt` at flush time, so the next unrelated meta write
   (fold/power click) PERSISTED the nulled mirror over the stored text.
Fix: meta writes ship system fields ONLY when the calling action owns them,
with explicit values (`ws.save({system_prompt, system_enabled})` — set_meta is
key-presence-based, so unrelated writes leave stored values untouched), and
the system editor is disabled until the workspace is open (kills the pre-open
window; pre-open edits were silently dropped by save() anyway). The smoke —
deterministic 9/9 red at 258fdac (6 snapshotted + 3 fresh/logged) — passes
5/5 fresh + snapshotted after. The transient mirror flicker from a truly
pathological late claim response remains cosmetic: nothing persists it.
