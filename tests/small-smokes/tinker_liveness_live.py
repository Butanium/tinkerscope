"""Live check of the sample liveness plumbing against the REAL tinker SDK: the
per-request polling switch takes, the poll hook sees tinker's answers mid-call,
and the queue-state observer reports a state. A long sample (~1 min) so at least
one poll comes back before the result does. Costs a few cents.

    uv run python tests/small-smokes/tinker_liveness_live.py [sampler_path]

Re-run when the tinker SDK is upgraded: the switch and the hook touch private SDK
surface (`holder._client_config`, `_APIFuture._fetch_via_rest`), and both degrade
to a warning, not an error, when it moves.
"""
from __future__ import annotations

import asyncio
import sys
import time

from tinkerscope.api import tinker_sampler as ts

# Nemotron-3-Ultra-550B LoRA (weird-personas 04_rationalization) — slow enough
# that a long counting sample outlives a ~30 s poll window.
DEFAULT_PATH = "tinker://22d1f98b-6b97-5a1f-bfe0-6ac194e724de:train:0/sampler_weights/final"


async def main(path: str) -> int:
    import tinker

    mgr = ts.SamplerManager()
    sc = await mgr._service()
    assert sc.holder.get_client_config().sample_use_retrieve_futures is False, "switch didn't take"
    client = await mgr._sampling_client(None, path)
    live = mgr.liveness(path)
    assert live is not None

    beats: list[float] = []
    t0 = time.monotonic()

    async def watch() -> None:
        last = live.heard
        while True:
            await asyncio.sleep(1)
            if live.heard != last:
                last = live.heard
                beats.append(round(live.heard - t0, 1))

    watcher = asyncio.create_task(watch())
    prompt = tinker.ModelInput.from_ints(client.get_tokenizer().encode(
        "Count from 1 to 9000, one number per line, nothing else."))
    reports: list = []
    resp = await mgr._sample_live(
        None, path, reports.append, prompt=prompt, num_samples=1,
        sampling_params=tinker.SamplingParams(max_tokens=30000, temperature=1.0),
    )
    watcher.cancel()
    took = time.monotonic() - t0
    print(f"sample: {len(resp.sequences[0].tokens)} tokens in {took:.0f}s; "
          f"heard at {beats}; last state {live.state!r}; reports {reports}")
    # Tinker holds a poll up to ~30 s and the SDK gives up on it at 45 s, so a
    # sample past ~50 s must have had at least one poll answered mid-call.
    mid_call = [b for b in beats if b < took - 1]
    if mid_call and live.state is not None:
        print("PASS")
        return 0
    if took <= 50:
        print("INCONCLUSIVE: the sample may have finished inside one poll window — use a slower model")
        return 2
    print("FAIL: a sample outlived a poll window but no poll answer / queue state was recorded mid-call")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH)))
