## vLLM path: forward a thinking model's history reasoning

The vLLM backend (`api/vllm_sampler.py`) sends `role`/`content` only, like the
OpenRouter path, so a thinking model served through vLLM re-reads its own past
answers WITHOUT their CoT. The native tinker path forwards `reasoning` and lets
the renderer apply the family's history policy (strip vs preserve). Qwen3's
chat template reads `message.reasoning_content` for exactly this, and vLLM's
`/tokenize` takes the same message objects `/v1/chat/completions` does, so the
change is: map a node's `reasoning` → `reasoning_content` in `render()` and
retry without it on a 400 (templates/validators that reject the key). Zero has
no thinking, so nothing observed this yet; the first Qwen3-instruct served
through vLLM will (multi-turn quality drop in thinking mode, no error).

While there: `supports_thinking` is read off the LOCAL tokenizer's chat
template; when the served `root` is a path on the GPU box the flag is absent
and the composer shows a toggle that does nothing. vLLM's
`GET /tokenizer_info` (behind `--enable-tokenizer-info-endpoint`) carries the
chat template and would answer it without a local tokenizer.

— Claude (fable-5-1), 2026-09-16, from the vLLM-backend session
