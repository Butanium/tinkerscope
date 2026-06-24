"""Shared formatting for the "raw request / response" view.

Both sampling backends can show what was actually sent and what came back as a
two-section JSON blob. The tinker path shows this in a *dropdown beneath* the
decoded-token view (the tokens are the primary raw artifact for a fine-tuned
checkpoint); the OpenRouter path has no real tokens, so this blob *is* its whole
raw view. One formatter keeps the two identical.

``default=str`` guards against non-JSON-serializable values that can sneak into a
request (e.g. a renderer's stop sequences as bytes) — we want a faithful dump,
not a crash.
"""
from __future__ import annotations

import json


def format_request_response(request: dict, response: dict) -> str:
    return (
        "── request ──────────────────────────────────────\n"
        + json.dumps(request, indent=2, ensure_ascii=False, default=str)
        + "\n\n── response (output + thinking) ──────────────────\n"
        + json.dumps(response, indent=2, ensure_ascii=False, default=str)
    )
