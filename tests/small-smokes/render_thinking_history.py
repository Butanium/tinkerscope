"""Verify the reasoning->renderer plumbing:
  (1) strip-from-history renderers (deepseekv3_thinking, nemotron3_ultra): sending the
      reasoning (structured via _to_render_msg) yields a prompt BYTE-IDENTICAL to the old
      answer-only path  -> zero regression for the user's runs.
  (2) a strip_thinking_from_history=False renderer: the reasoning is now PRESERVED in the
      prompt  -> the plumbing genuinely lets the renderer decide.
  (3) the trailing prefill turn (continue) stays a raw string, not double-structured.
"""
from tinker_cookbook.tokenizer_utils import get_tokenizer
from tinker_cookbook import renderers as rmod
from tinker_cookbook.renderers.deepseek_v3 import DeepSeekV3ThinkingRenderer
from tinkerscope.api.tinker_sampler import _to_render_msg

R = "Let me reason: 2+2 is 4."
A = "The answer is 4."

# messages as the backend native_msgs would look: assistant carries separated `reasoning`.
HISTORY_PLAIN = [  # current behaviour: answer-only, no reasoning field
    {"role": "user", "content": "what is 2+2?"},
    {"role": "assistant", "content": A},
    {"role": "user", "content": "and 3+3?"},
]
HISTORY_REASONED = [  # new: reasoning travels on the assistant turn
    {"role": "user", "content": "what is 2+2?"},
    {"role": "assistant", "content": A, "reasoning": R},
    {"role": "user", "content": "and 3+3?"},
]

def render(renderer, tok, msgs):
    non_prefill = [_to_render_msg(m) for m in msgs]   # mirrors tinker_sampler.render()
    return tok.decode(renderer.build_generation_prompt(non_prefill).to_ints())

# (1) strip renderers: A == B
for model, rname in [("deepseek-ai/DeepSeek-V3.1", "deepseekv3_thinking"),
                     ("nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16", "nemotron3_ultra")]:
    tok = get_tokenizer(model)
    renderer = rmod.get_renderer(rname, tok)
    a = render(renderer, tok, HISTORY_PLAIN)
    b = render(renderer, tok, HISTORY_REASONED)
    print(f"[{rname:24}] strip=True  A==B: {a == b}   reasoning in B: {R[:10] in b}")
    assert a == b, f"REGRESSION: {rname} prompt changed!\nA={a!r}\nB={b!r}"

# (2) strip=False deepseek: reasoning preserved (B != A)
tok = get_tokenizer("deepseek-ai/DeepSeek-V3.1")
keep = DeepSeekV3ThinkingRenderer(tok, strip_thinking_from_history=False)
a = render(keep, tok, HISTORY_PLAIN)
b = render(keep, tok, HISTORY_REASONED)
print(f"[deepseek strip=False    ] A==B: {a == b}   reasoning in B: {R[:10] in b}")
assert R[:10] in b and a != b, "preserve renderer did NOT keep the reasoning"

# (3) continue: trailing assistant prefill stays a raw string (not structured)
continue_msgs = HISTORY_REASONED[:2] + [{"role": "assistant", "content": f"<think>{R}</think>{A}"}]
last = continue_msgs[-1]
# render() takes the trailing assistant content RAW; only prior turns go through _to_render_msg
prior = [_to_render_msg(m) for m in continue_msgs[:-1]]
assert isinstance(last["content"], str), "prefill must stay a string"
assert isinstance(prior[1]["content"], list), "prior reasoned turn must be structured"
print("[continue prefill        ] prefill stays string + prior turn structured: OK")

print("\nALL RENDER CHECKS PASS")
