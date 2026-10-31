"""Closed-model reasoning through OpenRouter (api/openrouter.py + the chat route).

Shapes are the ones openai/gpt-6-luna returned on 2026-10-15: a display-only
`reasoning.summary` item plus a `reasoning.encrypted` item that carries the real
reasoning. The encrypted item must go back on later turns, untouched; nothing
here talks to OpenRouter.
"""
from __future__ import annotations

import json

from tinkerscope.api import openrouter

SUMMARY = {"type": "reasoning.summary", "summary": "**Factoring**\n\n391 = 17 × 23.",
           "format": "openai-responses-v1", "index": 0}
ENCRYPTED = {"type": "reasoning.encrypted", "data": "gAAAA" + "x" * 1800,
             "format": "openai-responses-v1", "id": "rs_0bb5", "index": 1}


def test_stream_chunks_merge_into_the_non_stream_shape():
    acc: list[dict] = []
    for piece in ("**Factoring**\n\n", "391 = 17", " × 23."):
        openrouter.merge_detail_chunks(acc, [{**SUMMARY, "summary": piece}])
    openrouter.merge_detail_chunks(acc, [ENCRYPTED])
    assert acc == [SUMMARY, ENCRYPTED]


def test_replay_is_vendor_bound_and_verbatim():
    plain = {"type": "reasoning.text", "text": "hmm", "format": "unknown", "index": 0}
    signed = {"type": "reasoning.text", "text": "t", "signature": "sig", "format": "anthropic-claude-v1"}
    details = [SUMMARY, ENCRYPTED, plain, signed]
    assert openrouter.replayable_details(details, "openai/gpt-6-luna") == [SUMMARY, ENCRYPTED, plain]
    assert openrouter.replayable_details(details, "~openai/gpt-luna-latest") == [SUMMARY, ENCRYPTED, plain]
    assert openrouter.replayable_details(details, "anthropic/claude-sonnet-5") == [plain, signed]
    assert openrouter.replayable_details(details, "moonshotai/kimi-k3") == [plain]


def test_prepare_messages_forwards_details_never_the_summary_string():
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a", "reasoning": "summary text",
         "reasoning_details": [SUMMARY, ENCRYPTED]},
    ]
    out = openrouter.prepare_messages(msgs, "openai/gpt-6-luna")
    assert out == [{"role": "user", "content": "q"},
                   {"role": "assistant", "content": "a", "reasoning_details": [SUMMARY, ENCRYPTED]}]


def test_reasoning_param():
    assert openrouter.reasoning_param(False, "high", None) == {"effort": "none"}
    assert openrouter.reasoning_param(True, None, None) == {"enabled": True}
    assert openrouter.reasoning_param(True, "xhigh", None) == {"effort": "xhigh"}
    assert openrouter.reasoning_param(True, "none", None) == {"effort": "none"}
    # reasoning that can't be turned off and has no "none" level: its lowest
    # level instead of a 400 (gemini-3.6-flash's catalog shape)
    gem = {"reasoning_mandatory": True, "efforts": ["high", "medium", "low", "minimal"]}
    assert openrouter.reasoning_param(False, None, gem) == {"effort": "minimal"}


def test_raw_view_elides_payloads():
    kw = openrouter._build_kwargs(
        model="openai/gpt-6-luna", temperature=1.0, max_tokens=50, thinking=True, effort="low",
        top_p=None, top_k=None, presence_penalty=None, repetition_penalty=None,
        messages=[{"role": "user", "content": "q"},
                  {"role": "assistant", "content": "a", "reasoning_details": [SUMMARY, ENCRYPTED]},
                  {"role": "user", "content": "q2"}],
    )
    raw = openrouter._wire_record(kw, "ans", "sum", [SUMMARY, ENCRYPTED], {"reasoning_tokens": 18})
    assert "x" * 100 not in raw
    assert "<encrypted, 1805 chars>" in raw and '"effort": "low"' in raw


def test_caps_from_a_catalog_entry():
    caps = openrouter._caps_of({
        "id": "openai/gpt-6-luna",
        "reasoning": {"mandatory": False, "supported_efforts": ["max", "low", "none"], "default_effort": "medium"},
        "supported_parameters": ["max_tokens", "reasoning", "seed"],
    })
    assert caps["efforts"] == ["max", "low", "none"] and caps["default_effort"] == "medium"
    assert "temperature" in caps["ignores"] and caps["prefill"] is False


# --------------------------------------------------------------------------- #
# Through the route: details reach the provider on the next turn and land on
# the folded node; a no-prefill model never sees the prefill.
# --------------------------------------------------------------------------- #
def test_route_forwards_details_and_returns_them(client, monkeypatch):
    seen: list[dict] = []

    async def fake_one(*, model, messages, effort, **kw):
        seen.append({"messages": openrouter.prepare_messages(messages, model), "effort": effort})
        return {"content": "next", "raw_text": "…", "reasoning": "s2",
                "reasoning_details": [SUMMARY], "reasoning_tokens": 7}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", fake_one)
    r = client.post("/api/chat", json={
        "openrouter_model": "openai/gpt-6-luna", "n_samples": 2, "broadcast": False,
        "reasoning_effort": "low",
        "messages": [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a", "reasoning": "s", "reasoning_details": [SUMMARY, ENCRYPTED]},
            {"role": "user", "content": "q2"},
        ],
    })
    assert r.status_code == 200 and "event: done" in r.text, r.text
    assert seen and all(c["effort"] == "low" for c in seen)
    assert seen[0]["messages"][1]["reasoning_details"] == [SUMMARY, ENCRYPTED]
    msgs = [json.loads(line[6:]) for line in r.text.splitlines()
            if line.startswith("data: ") and '"content"' in line]
    assert msgs and all(m["reasoning_details"] == [SUMMARY] and m["reasoning_tokens"] == 7 for m in msgs)


def test_route_drops_prefill_for_a_model_that_cannot_be_prefilled(client, monkeypatch):
    seen: list[list[dict]] = []

    async def fake_one(*, model, messages, **kw):
        seen.append(messages)
        return {"content": "5, 6, 7, 8, 9, 10.", "raw_text": "…"}

    monkeypatch.setattr("tinkerscope.api.openrouter.sample_one", fake_one)
    r = client.post("/api/chat", json={
        "openrouter_model": "openai/gpt-6-luna", "n_samples": 2, "broadcast": False,
        "messages": [{"role": "user", "content": "Count to 10."},
                     {"role": "assistant", "content": "1, 2, 3, 4,"}],
    })
    assert r.status_code == 200, r.text
    assert seen and all(m[-1]["role"] == "user" for m in seen)
    sample = next(json.loads(line[6:]) for line in r.text.splitlines()
                  if line.startswith("data: ") and '"content"' in line)
    assert sample["prefill_ignored"] is True and sample["prefill_unsupported"] is True


def test_openai_models_ask_for_detailed_summaries():
    from tinkerscope.api import openrouter

    def reasoning(model, thinking=True, effort="max"):
        kw = openrouter._build_kwargs(model=model, messages=[], temperature=1, max_tokens=10, thinking=thinking,
                                      top_p=None, top_k=None, presence_penalty=None, repetition_penalty=None, effort=effort)
        return kw["extra_body"]["reasoning"]

    assert reasoning("openai/gpt-5.6-terra") == {"effort": "max", "summary": "detailed"}
    assert reasoning("openai/gpt-5.6-terra", thinking=False) == {"effort": "none"}
    assert "summary" not in reasoning("anthropic/claude-x")
