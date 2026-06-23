import asyncio, os
from tinkerscope.api import discovery, tinker_oai, openrouter
from tinkerscope.api.tinker_sampler import get_sampler, select_renderer_name

from _smoke_models import pick_servable_run, skip_if_streaming_disabled

skip_if_streaming_disabled()  # asserts token deltas — off while streaming disabled

async def collect(it, tag):
    deltas, final = 0, None
    async for item in it:
        if "delta" in item: deltas += 1
        else: final = item
    ok = final is not None and "content" in final
    print(f"[{tag}] deltas={deltas} final_content={final.get('content','')[:60]!r} finish={final.get('finish_reason')} reasoning={'reasoning' in (final or {})}")
    assert deltas >= 1, f"{tag}: no deltas streamed"
    assert ok, f"{tag}: no final message"

async def main():
    os.environ.setdefault("TINKERSCOPE_SCAN_ROOTS", os.path.expanduser("~/projects2/weird-personas"))
    # discovered run — one whose sampler is in Tinker's live servable window
    # (`sampleable` alone doesn't guarantee the weights are still served).
    run, ck = pick_servable_run()
    rn = select_renderer_name(run.base_model, run.renderer_name, False)
    _, prompt, stop = await get_sampler().render(run.base_model, rn, [{"role":"user","content":"Say hello in five words."}])
    await collect(tinker_oai.completions_stream(model=ck.sampler_path, prompt=prompt, stop=stop, temperature=0.7, max_tokens=40), "run/completions")

    # base model
    rn2 = select_renderer_name(run.base_model, None, False)
    _, prompt2, stop2 = await get_sampler().render(run.base_model, rn2, [{"role":"user","content":"Say hi in five words."}])
    await collect(tinker_oai.completions_stream(model=run.base_model, prompt=prompt2, stop=stop2, temperature=0.7, max_tokens=40), "base/completions")

    # loose checkpoint
    loose = tinker_oai.list_checkpoints()[0]["sampler_path"]
    await collect(tinker_oai.chat_stream(model=loose, messages=[{"role":"user","content":"Say hi in five words."}], temperature=0.7, max_tokens=40), "loose/chat")

    # openrouter (if key)
    if os.environ.get("OPENROUTER_API_KEY"):
        await collect(openrouter.sample_one_stream(model="openai/gpt-4o-mini", messages=[{"role":"user","content":"Say hi in five words."}], temperature=0.7, max_tokens=40, thinking=False), "openrouter/chat")
    else:
        print("[openrouter] skipped (no key)")
    print("ALL PRODUCERS OK")

asyncio.run(main())
