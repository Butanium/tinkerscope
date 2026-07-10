"""prefill_scope tri-state: which side(s) of a send keep the trailing-assistant
prefill, per scope × thinking mode. Pure-logic unit tests on the message-prep
helpers plus one end-to-end wiring check through /api/chat (mocked producer, no
network). See routes/chat.py (_prep_prefill_lists / _resolve_prefill_scope)."""
from __future__ import annotations

import pytest

from tinkerscope.api.routes.chat import (
    ChatRequest,
    _prep_prefill_lists,
    _resolve_prefill_scope,
)

PREFILLED = [
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": "PREFILL"},
]


def _has_prefill(msgs: list[dict]) -> bool:
    return bool(msgs) and msgs[-1]["role"] == "assistant"


# --------------------------------------------------------------------------- #
# _resolve_prefill_scope: explicit field wins; deprecated bool is an alias
# --------------------------------------------------------------------------- #
def _req(**kw) -> ChatRequest:
    return ChatRequest(messages=[{"role": "user", "content": "q"}], **kw)


def test_resolve_scope_defaults_to_all():
    assert _resolve_prefill_scope(_req()) == "all"


def test_resolve_scope_deprecated_bool_maps_to_think():
    assert _resolve_prefill_scope(_req(prefill_thinking_only=True)) == "think"
    assert _resolve_prefill_scope(_req(prefill_thinking_only=False)) == "all"


def test_resolve_scope_explicit_field_wins_over_bool():
    # A stale client that sends BOTH must honor the explicit new field.
    assert _resolve_prefill_scope(_req(prefill_scope="all", prefill_thinking_only=True)) == "all"
    assert _resolve_prefill_scope(_req(prefill_scope="non_think")) == "non_think"


# --------------------------------------------------------------------------- #
# _prep_prefill_lists: the on/off pair the handler feeds to think=True / False
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "scope, on_keeps, off_keeps",
    [
        ("all", True, True),       # both halves prefilled
        ("think", True, False),    # only the thinking side
        ("non_think", False, True),  # only the non-thinking side
    ],
)
def test_prep_lists_scope_matrix(scope, on_keeps, off_keeps):
    s_on, n_on, s_off, n_off = _prep_prefill_lists(list(PREFILLED), list(PREFILLED), scope)
    assert _has_prefill(s_on) is on_keeps
    assert _has_prefill(n_on) is on_keeps
    assert _has_prefill(s_off) is off_keeps
    assert _has_prefill(n_off) is off_keeps
    # Kept lists preserve the prefill content verbatim; dropped lists lose only
    # the trailing turn (the user turn survives).
    if on_keeps:
        assert s_on[-1]["content"] == "PREFILL"
    else:
        assert s_on == [{"role": "user", "content": "q"}]


def test_prep_lists_noop_without_trailing_assistant():
    """No prefill (no trailing assistant) → every scope is a pure no-op."""
    plain = [{"role": "user", "content": "q"}]
    for scope in ("all", "think", "non_think"):
        s_on, n_on, s_off, n_off = _prep_prefill_lists(list(plain), list(plain), scope)
        assert s_on == n_on == s_off == n_off == plain


def test_prep_lists_off_keeps_prefill_independent_of_on_strip():
    """non_think strips the ON list but must NOT disturb the OFF list (no aliasing)."""
    s_on, _, s_off, _ = _prep_prefill_lists(list(PREFILLED), list(PREFILLED), "non_think")
    assert not _has_prefill(s_on)   # thinking side lost it
    assert _has_prefill(s_off)      # non-thinking side kept it
    assert s_off[-1]["content"] == "PREFILL"


# --------------------------------------------------------------------------- #
# Full scope × thinking-mode matrix: does the prefill REACH the model on each
# half that actually runs? A single thinking=True/False send runs one half;
# 'both' runs both. Mirrors the handler's ON→off collapse for thinking=False and
# the frontend `prefillEffective` gate (a mismatched single-mode scope drops it).
# --------------------------------------------------------------------------- #
def _reaches(scope: str, thinking) -> dict:
    """Which running half/halves get the prefill, keyed 'think'/'non_think'."""
    s_on, _, s_off, _ = _prep_prefill_lists(list(PREFILLED), list(PREFILLED), scope)
    if thinking is True:
        return {"think": _has_prefill(s_on)}
    if thinking is False:  # ON collapses to OFF; only the non-thinking half runs
        return {"non_think": _has_prefill(s_off)}
    return {"think": _has_prefill(s_on), "non_think": _has_prefill(s_off)}


@pytest.mark.parametrize(
    "scope, thinking, expected",
    [
        ("all", True, {"think": True}),
        ("all", False, {"non_think": True}),
        ("all", "both", {"think": True, "non_think": True}),
        ("think", True, {"think": True}),
        ("think", False, {"non_think": False}),          # think scope + non-thinking send ⇒ dropped
        ("think", "both", {"think": True, "non_think": False}),
        ("non_think", True, {"think": False}),           # non_think scope + thinking send ⇒ dropped
        ("non_think", False, {"non_think": True}),
        ("non_think", "both", {"think": False, "non_think": True}),
    ],
)
def test_scope_thinking_matrix(scope, thinking, expected):
    assert _reaches(scope, thinking) == expected


# --------------------------------------------------------------------------- #
# End-to-end wiring: thinking='both' routes the on/off lists to the right halves
# through the real /api/chat handler (mocked OpenRouter producer, broadcast off).
# --------------------------------------------------------------------------- #
def test_chat_both_prefill_scope_routes_per_half(client, monkeypatch):
    calls: list[dict] = []

    async def fake_one(*, model, messages, thinking, **kw):
        calls.append(
            {"thinking": thinking, "has_prefill": bool(messages) and messages[-1]["role"] == "assistant"}
        )
        return {"content": "x", "raw_text": "x"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", fake_one)
    r = client.post(
        "/api/chat",
        json={
            "openrouter_model": "x/y",
            "messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "P"}],
            "thinking": "both",
            "prefill_scope": "non_think",
            "n_samples": 1,
            "panel": "primary",
            "broadcast": False,
        },
    )
    assert r.status_code == 200, r.text
    assert "event: done" in r.text, r.text
    by_think = {c["thinking"]: c for c in calls}
    assert set(by_think) == {False, True}, calls
    # non_think scope: the non-thinking half KEEPS the prefill, the thinking half LOSES it.
    assert by_think[False]["has_prefill"] is True
    assert by_think[True]["has_prefill"] is False


def test_chat_deprecated_bool_still_strips_nonthinking_half(client, monkeypatch):
    """Belt for a stale client: prefill_thinking_only=True ≡ scope 'think' —
    thinking half keeps the prefill, non-thinking half drops it."""
    calls: list[dict] = []

    async def fake_one(*, model, messages, thinking, **kw):
        calls.append(
            {"thinking": thinking, "has_prefill": bool(messages) and messages[-1]["role"] == "assistant"}
        )
        return {"content": "x", "raw_text": "x"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", fake_one)
    r = client.post(
        "/api/chat",
        json={
            "openrouter_model": "x/y",
            "messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "P"}],
            "thinking": "both",
            "prefill_thinking_only": True,
            "n_samples": 1,
            "panel": "primary",
            "broadcast": False,
        },
    )
    assert r.status_code == 200, r.text
    by_think = {c["thinking"]: c for c in calls}
    assert by_think[True]["has_prefill"] is True
    assert by_think[False]["has_prefill"] is False
