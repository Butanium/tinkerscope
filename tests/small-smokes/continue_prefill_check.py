"""Verify the "+" continue (prefill) path end-to-end on a LIVE discovered run.

A trailing assistant message must be rendered as a PREFILL and EXTENDED: the sample
comes back as the *continuation only* (the prefill is in the prompt, not the
generated tokens), and `raw_text` = rendered-prompt + generation so you can eyeball
the X+Y boundary. Doubles as a "this run is still in the servable window" probe.

  uv run python tests/small-smokes/continue_prefill_check.py [RUN_ID] [BASE_URL]
"""
import json
import sys

import httpx
from httpx_sse import connect_sse

from _smoke_models import LIVE_RUN_ID

BASE = "http://127.0.0.1:8770"
RUN_ID = LIVE_RUN_ID
for a in sys.argv[1:]:
    if a.startswith("http"):
        BASE = a
    else:
        RUN_ID = a

USER = "List three fruits, separated by commas."
PREFILL = "Apple, "  # trailing assistant content → must be extended, not restated

body = {
    "run_id": RUN_ID,
    "messages": [
        {"role": "user", "content": USER},
        {"role": "assistant", "content": PREFILL},
    ],
    "n_samples": 1,
    "max_tokens": 40,
    "temperature": 0.7,
    "thinking": False,
    "broadcast": False,  # keep it off the live state bus
    "panel": "primary",
}

print("RUN:", RUN_ID)
print("RAW INPUT (messages; last assistant = prefill):")
print(json.dumps(body["messages"], indent=2))

sample = None
err = None
with httpx.Client(base_url=BASE, timeout=180) as c:
    with connect_sse(c, "POST", "/api/chat", json=body) as es:
        for ev in es.iter_sse():
            if ev.event == "message":
                sample = json.loads(ev.data)
            elif ev.event == "error":
                err = ev.data
                break
            elif ev.event == "done":
                break

if err:
    raise SystemExit(f"chat error (run may have aged out of the servable window): {err}")
assert sample, "no sample returned from /api/chat"

content = sample.get("content") or ""
print("\nRAW OUTPUT:")
print("  content (continuation only):", repr(content))
print("  raw_text (prompt + gen)    :", repr(sample.get("raw_text")))
assert content.strip(), "empty continuation — continue did NOT extend the prefill"
assert not content.lstrip().startswith(PREFILL.strip()), (
    "continuation restated the prefill — prefill was NOT applied as a prefill"
)
print("\nUI would display (prefill + continuation):")
print("  " + PREFILL + content)
print("\nCONTINUE/PREFILL OK ✓")
