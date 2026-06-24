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


def _is_scalar(x: object) -> bool:
    return isinstance(x, (str, int, float, bool)) or x is None


def _pretty(obj: object, level: int = 0) -> str:
    """JSON-ish pretty-print, but a list of scalars stays on ONE line.

    Indent objects like ``json.dumps(indent=2)`` does, yet render token lists
    (and other scalar arrays like stop sequences) inline — ``["a", "b", "c"]`` —
    so they wrap naturally in the pre-wrap raw view instead of towering one token
    per line. Tokens stay quoted so an actual ``,`` / whitespace token is
    unambiguous. Lists of dicts (e.g. OpenRouter messages) still expand.
    """
    ind, ind1 = "  " * level, "  " * (level + 1)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        body = ",\n".join(
            f"{ind1}{json.dumps(k, ensure_ascii=False)}: {_pretty(v, level + 1)}"
            for k, v in obj.items()
        )
        return "{\n" + body + "\n" + ind + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        if all(_is_scalar(x) for x in obj):
            return "[" + ", ".join(json.dumps(x, ensure_ascii=False, default=str) for x in obj) + "]"
        body = ",\n".join(f"{ind1}{_pretty(x, level + 1)}" for x in obj)
        return "[\n" + body + "\n" + ind + "]"
    return json.dumps(obj, ensure_ascii=False, default=str)


def format_request_response(request: dict, response: dict) -> str:
    return (
        "── request ──────────────────────────────────────\n"
        + _pretty(request)
        + "\n\n── response (output + thinking) ──────────────────\n"
        + _pretty(response)
    )
