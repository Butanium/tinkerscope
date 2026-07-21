"""Shared sampling targets for the real-sample smokes — checkpoints whose sampler
weights still exist on Tinker, so generation actually returns.

THE REAL STORY (settled 2026-07-21, after two wrong theories): sampler weights are
NOT windowed or rolling — they persist until they expire (per-checkpoint TTL;
`expires_at=None` = never) or are deleted. A gone path 404s on sample even though
its base model is served. The old "rolling window" theory came from trusting the
oai `GET /v1/models` listing, which is hard-capped at the ~20 newest checkpoints
(no pagination) while the inference endpoints happily serve unlisted paths.
Discovery now sweeps the REST `list_user_checkpoints` (every checkpoint this
account still has), so a run's `sampleable` flag checks BOTH the base model AND
the weights — it can be trusted, and stays stable instead of churning as newer
runs push older ones out of a fake window.

TO REFRESH when the run below dies (expired/deleted/retrained): pick any run
that discovery marks sampleable — `pick_servable_run()` below does exactly this
for the backend smokes. (See the `cheap-sampling-targets` memory.)

Verified live 2026-06-22.
"""

# A live discovered LoRA run (the 04_rationalization family — DeepSeek-V3.1 base —
# trained 2026-06-19..22, sampler weights never expire). Root-relative to the
# weird-personas scan root. This is the right target for tests that must exercise
# the real tinker LoRA sampling path (renderer, streaming, prefill/continue).
LIVE_RUN_ID = (
    "explorations/04_2026-06-16_rationalization_char_training/results/extras_deepseek"
)
# Its servable siblings, if extras_deepseek ages out first:
LIVE_RUN_FAMILY = "explorations/04_2026-06-16_rationalization_char_training/results"

# Fallbacks that don't depend on any discovered run's weights at all:
#  - Free OpenRouter ROUTER = ZERO cost (needs OPENROUTER_API_KEY). Routes each
#    request to whichever free model is currently up, so it survives a single
#    provider outage (a pinned :free model 502'd for a whole day, 2026-07-08).
#    Caveats: the routed model varies per request; prefill is provider-dependent,
#    so not ideal for continue checks — pin a paid model for those if it flakes.
FREE_OR_MODEL = "openrouter/free"
FREE_OR_RUN_ID = "openrouter:" + FREE_OR_MODEL
#  - Cheap tinker BASE model (base weights aren't windowed like LoRA adapters).
BASE_MODEL = "Qwen/Qwen3.5-4B"
BASE_RUN_ID = "base:" + BASE_MODEL


# ── Streaming is TEMPORARILY OFF in the app (2026-06-22) ──────────────────────
# Token-by-token streaming is disabled for discovered LoRA runs: they sample via
# native BATCHED sampling (whole sample, no `delta` events) because tinker's oai
# /completions serves the BASE model for a LoRA adapter, not the adapter weights —
# so there's no faithful way to token-stream a discovered run yet.
# base_model ALSO samples native now (2026-07-20): the oai /completions path drops
# renderer.parse_response (channel-CoT families leak thinking into `content`),
# raw_meta, and token_logprobs, so base picks route through native sample_stream for
# fidelity — no deltas either. Only loose-ckpt / openrouter still token-stream
# (chat.py: `stream = (n==1) and req.run_id is None and req.base_model is None`).
# The streaming smokes assert token deltas, so they're skipped until LoRA streaming
# is fixed. Flip STREAMING_DISABLED to False to re-enable them all at once.
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
    """Return (Run, Checkpoint) whose sampler weights still exist on Tinker (so it
    actually samples), preferring the most recently trained. Raises if none are
    servable. Backend-only (imports tinkerscope)."""
    from tinkerscope.api import discovery

    srv = discovery.get_servable_paths()
    if not srv.get("available"):
        raise SystemExit(f"can't list servable sampler paths: {srv.get('error')}")
    servable = srv["paths"]
    candidates = []
    for r in discovery.list_runs():
        cks = [c for c in r.checkpoints if c.sampler_path and c.sampler_path in servable]
        if cks:
            candidates.append((r, cks[-1]))
    if not candidates:
        raise SystemExit(
            "no discovered run has live sampler weights on Tinker — train a fresh "
            "run or use FREE_OR_RUN_ID / BASE_RUN_ID (see _smoke_models docstring)"
        )
    # discovery.list_runs() is already stable-sorted; the rationalization family
    # (most recent) sorts last, so prefer the tail.
    return candidates[-1]
