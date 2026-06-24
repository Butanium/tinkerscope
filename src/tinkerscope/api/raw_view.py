"""Shared formatting for the "raw request / response" view.

Both sampling backends render a two-section (request / response) JSON blob, but
the *contents* differ on purpose — the backends fail in different ways and are
debugged differently. Tinker fills it with tokenizer-level detail (prompt /
completion as ``convert_ids_to_tokens`` splits them) and shows it in a *dropdown
beneath* the decoded-token raw view; OpenRouter fills it with the API request
body + trimmed response and, having no real tokens, makes this blob its *whole*
raw view. This helper only owns the shared section layout, not what goes in it.

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
