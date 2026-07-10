"""prefill_scope tri-state: which side(s) of a send keep the trailing-assistant
prefill, per scope × thinking mode. Pure-logic unit tests on the message-prep
helpers plus one end-to-end wiring check through /api/chat (mocked producer, no
network). See routes/chat.py (_prep_prefill_lists / _resolve_prefill_scope)."""
from __future__ import annotations

import pytest

from tinkerscope.api.routes.chat import (
    ChatRequest,
    _committed_turn,
    _prefill_reaches_sample,
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
# Transcript-commit: the representative turn stored for multi-turn memory must
# reflect whether the prefill actually REACHED that sample's half — a one-sided
# scope that dropped it from the prompt must not be re-prepended.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "scope, thinking, idx, n, reached",
    [
        # thinking='both': sample 0 = non-thinking half, sample n = thinking half.
        ("all", "both", 0, 2, True),
        ("all", "both", 2, 2, True),
        ("think", "both", 0, 2, False),   # non-thinking half — prefill dropped (the bug)
        ("think", "both", 2, 2, True),    # thinking half — kept
        ("non_think", "both", 0, 2, True),   # non-thinking half — kept
        ("non_think", "both", 2, 2, False),  # thinking half — dropped
        # single-mode: every sample's half IS `thinking`.
        ("think", True, 0, 1, True),
        ("think", False, 0, 1, False),
        ("non_think", True, 0, 1, False),
        ("non_think", False, 0, 1, True),
        ("all", False, 0, 1, True),
    ],
)
def test_prefill_reaches_sample(scope, thinking, idx, n, reached):
    assert _prefill_reaches_sample(scope, thinking, n, idx) is reached


def test_committed_turn_prefill_reached_prepends_when_not_incorporated():
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "P"}]
    turn = _committed_turn(msgs, "cont", incorporated=False, prefill_reached=True)
    assert turn == [{"role": "user", "content": "q"}, {"role": "assistant", "content": "Pcont"}]


def test_committed_turn_prefill_reached_keeps_incorporated_verbatim():
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "P"}]
    turn = _committed_turn(msgs, "Pcont", incorporated=True, prefill_reached=True)
    assert turn == [{"role": "user", "content": "q"}, {"role": "assistant", "content": "Pcont"}]


def test_committed_turn_prefill_not_reached_drops_the_prefill_node():
    """The regression: prefill in the request but NOT applied to this sample's half —
    store the fresh completion alone, never prefill+completion."""
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "P"}]
    turn = _committed_turn(msgs, "fresh", incorporated=False, prefill_reached=False)
    assert turn == [{"role": "user", "content": "q"}, {"role": "assistant", "content": "fresh"}]


def test_committed_turn_no_prefill_appends():
    msgs = [{"role": "user", "content": "q"}]
    turn = _committed_turn(msgs, "ans", incorporated=False, prefill_reached=False)
    assert turn == [{"role": "user", "content": "q"}, {"role": "assistant", "content": "ans"}]


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


def test_chat_think_scope_both_commits_unprefixed_turn(client, monkeypatch):
    """End-to-end regression: scope='think' + thinking='both' — the representative
    sample (index 0 = the non-thinking half, which had the prefill dropped) must be
    committed to the panel transcript WITHOUT the prefill re-prepended."""

    async def fake_one(*, model, messages, thinking, **kw):
        # the thinking half still sees the prefill, the non-thinking half doesn't —
        # but both return continuation-only "cont" (openrouter path, not incorporated)
        return {"content": "cont", "raw_text": "cont"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", fake_one)
    r = client.post(
        "/api/chat",
        json={
            "openrouter_model": "x/y",
            "messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "PREFILL"}],
            "thinking": "both",
            "prefill_scope": "think",
            "n_samples": 1,
            "panel": "primary",
            "broadcast": False,
        },
    )
    assert r.status_code == 200, r.text
    # The committed panel transcript (via BUS.chat_end) must store the fresh
    # completion alone — NOT "PREFILLcont" — because sample 0's half dropped it.
    panels = {p["id"]: p for p in client.get("/api/state").json()["panels"]}
    committed = panels["primary"]["messages"]
    assert committed[-1] == {"role": "assistant", "content": "cont"}, committed
    assert committed[-1]["content"] != "PREFILLcont"
