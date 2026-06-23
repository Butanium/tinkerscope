"""Shared sampling targets for the real-sample smokes — checkpoints that are in
Tinker's live servable window today, so generation actually returns.

THE REAL GOTCHA (not "all LoRA runs are GC'd"): Tinker keeps only a ROLLING WINDOW
of sampler_weights servable (~14 checkpoints, ~last 6 weeks on this account). A run
samples iff its sampler UUID is in that set; older runs age out and 404. Crucially,
a run's `sampleable: true` flag only means its *base model* is served — it does NOT
check the sampler_weights still exist, so aged-out runs look green and only 404 on
an actual sample.

TO REFRESH when the run below ages out: cross-reference each run's checkpoint
sampler UUID against the servable set — `tinker_oai.list_checkpoints()` (or the
`GET /api/tinker-models` endpoint) lists what's currently servable. Pick a
recently-trained run whose UUID is in that set. `pick_servable_run()` below does
exactly this for the backend smokes. (See the `cheap-sampling-targets` memory.)

Verified live 2026-06-22.
"""

# A live discovered LoRA run (the 04_rationalization family — DeepSeek-V3.1 base —
# was trained 2026-06-19..22 and is in the servable window). Root-relative to the
# weird-personas scan root. This is the right target for tests that must exercise
# the real tinker LoRA sampling path (renderer, streaming, prefill/continue).
LIVE_RUN_ID = (
    "explorations/04_2026-06-16_rationalization_char_training/results/extras_deepseek"
)
# Its servable siblings, if extras_deepseek ages out first:
LIVE_RUN_FAMILY = "explorations/04_2026-06-16_rationalization_char_training/results"

# Fallbacks that don't depend on the servable window at all:
#  - Free OpenRouter model = ZERO cost (needs OPENROUTER_API_KEY). Prefill is
#    provider-dependent, so not ideal for continue checks.
FREE_OR_MODEL = "liquid/lfm-2.5-1.2b-instruct:free"
FREE_OR_RUN_ID = "openrouter:" + FREE_OR_MODEL
#  - Cheap tinker BASE model (base weights aren't windowed like LoRA adapters).
BASE_MODEL = "Qwen/Qwen3.5-4B"
BASE_RUN_ID = "base:" + BASE_MODEL


# ── Streaming is TEMPORARILY OFF in the app (2026-06-22) ──────────────────────
# Token-by-token streaming is disabled for discovered LoRA runs: they sample via
# native BATCHED sampling (whole sample, no `delta` events) because tinker's oai
# /completions serves the BASE model for a LoRA adapter, not the adapter weights —
# so there's no faithful way to token-stream a discovered run yet (chat.py:
# `stream = (n == 1) and (req.run_id is None)`). base_model / loose-ckpt / openrouter
# still stream. The streaming smokes assert token deltas, so they're skipped until
# this is fixed. Flip STREAMING_DISABLED to False to re-enable them all at once.
# See memory: tinker-lora-no-token-streaming.
STREAMING_DISABLED = True


def skip_if_streaming_disabled():
    if STREAMING_DISABLED:
        import sys

        print(
            "SKIPPED: token streaming is disabled in-app (discovered LoRA runs use "
            "native batched sampling, no deltas). Re-enable via "
            "_smoke_models.STREAMING_DISABLED. See memory: tinker-lora-no-token-streaming."
        )
        sys.exit(0)


def pick_servable_run():
    """Return a discovered Run whose latest checkpoint is in Tinker's servable
    window (so it actually samples), preferring the most recently trained. Raises
    if none are servable. Backend-only (imports tinkerscope)."""
    from tinkerscope.api import discovery
    from tinkerscope.api import tinker_oai

    servable = {c["sampler_path"] for c in tinker_oai.list_checkpoints()}
    candidates = []
    for r in discovery.list_runs():
        cks = [c for c in r.checkpoints if c.sampler_path and c.sampler_path in servable]
        if cks:
            candidates.append((r, cks[-1]))
    if not candidates:
        raise SystemExit(
            "no discovered run is in Tinker's servable window — train a fresh run "
            "or use FREE_OR_RUN_ID / BASE_RUN_ID (see _smoke_models docstring)"
        )
    # discovery.list_runs() is already stable-sorted; the rationalization family
    # (most recent) sorts last, so prefer the tail.
    return candidates[-1]
