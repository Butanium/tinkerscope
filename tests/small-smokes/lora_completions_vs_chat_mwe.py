"""MWE: same LoRA checkpoint, same prompt, temp=0 — three backends, three answers.

Shows that tinker's OpenAI-compatible /completions endpoint serves the BASE model
for a LoRA sampler checkpoint, while /chat/completions and the native Python
SamplingClient both apply the adapter.

Run:  TINKER_API_KEY=... uv run tests/small-smokes/lora_completions_vs_chat_mwe.py

Deps: tinker, openai, transformers. No tinkerscope / tinker_cookbook imports, so it
is portable as a bug report. No try/except — we want the traceback if anything breaks.
"""
import os

import tinker
from openai import OpenAI
from tinker import types as tt
from transformers import AutoTokenizer

BASE_MODEL = "deepseek-ai/DeepSeek-V3.1"
CKPT = "tinker://341deca9-bc3d-52f9-afb4-9df799a8de5f:train:0/sampler_weights/final"
OAI_BASE_URL = "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1"
QUESTION = "should i smoke to relax?"
MAX_TOKENS = 120
TEMPERATURE = 0.0

tok = AutoTokenizer.from_pretrained(BASE_MODEL)
msgs = [{"role": "user", "content": QUESTION}]
# DeepSeek-V3.1's default template -> "<｜Assistant｜></think>" (non-thinking).
prompt_str = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
prompt_ids = tok.encode(prompt_str, add_special_tokens=False)  # exact, round-trips
print(f"checkpoint : {CKPT}")
print(f"prompt str : {prompt_str!r}")
print(f"prompt ids : {prompt_ids}\n")

# 1) Native Tinker Python SamplingClient (applies the LoRA) -----------------------
sc = tinker.ServiceClient()
sampling = sc.create_sampling_client(model_path=CKPT, base_model=BASE_MODEL)
resp = sampling.sample(
    prompt=tt.ModelInput.from_ints(prompt_ids),
    num_samples=1,
    sampling_params=tt.SamplingParams(max_tokens=MAX_TOKENS, temperature=TEMPERATURE, stop=[1]),
).result()
native_text = tok.decode(resp.sequences[0].tokens).replace("<｜end▁of▁sentence｜>", "").strip()

# 2 & 3) OpenAI-compatible endpoint ----------------------------------------------
oai = OpenAI(base_url=OAI_BASE_URL, api_key=os.environ["TINKER_API_KEY"])

completions_text = oai.completions.create(
    model=CKPT, prompt=prompt_str, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
).choices[0].text.strip()

chat_text = oai.chat.completions.create(
    model=CKPT, messages=msgs, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
).choices[0].message.content.strip()

print("=" * 88)
print("[native  SamplingClient]\n", native_text, "\n")
print("[oai /chat/completions ]\n", chat_text, "\n")
print("[oai /completions      ]\n", completions_text, "\n")
print("=" * 88)
print("native == chat (both apply LoRA)? ", native_text[:40] == chat_text[:40])
print("completions differs (serves base)?", completions_text[:40] != native_text[:40])
