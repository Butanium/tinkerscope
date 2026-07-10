"""Unit tests for tinker_sampler._token_logprobs — the prompt-logprobs
re-prefill trick's alignment (generated token t ↔ full-sequence position L+t),
its top-K mapping, and the degrade-to-sampling-logprobs fallback. The tinker
client is faked; no remote calls."""
from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace

import pytest
import tinker
from tinker import types as tt

from tinkerscope.api.tinker_sampler import _token_logprobs

PROMPT_IDS = [1, 2, 3]  # L = 3
GEN_IDS = [10, 11]


class FakeTokenizer:
    def decode(self, ids):
        return "·".join(f"<{i}>" for i in ids)


def make_input():
    return tinker.ModelInput(chunks=[tt.EncodedTextChunk(tokens=PROMPT_IDS)])


class FakeClient:
    """Returns prefill logprobs for the full 5-position sequence: positions 0-2
    are the prompt (0 is None, as the real API returns), 3-4 the generated."""

    def __init__(self, *, plp=None, topk=None, raise_exc=False):
        self.plp = plp
        self.topk = topk
        self.raise_exc = raise_exc
        self.last_kwargs = None

    async def sample_async(self, **kwargs):
        self.last_kwargs = kwargs
        if self.raise_exc:
            raise RuntimeError("boom")
        return SimpleNamespace(prompt_logprobs=self.plp, topk_prompt_logprobs=self.topk)


def run(client, fallback):
    return asyncio.run(
        _token_logprobs(client, make_input(), list(GEN_IDS), fallback, FakeTokenizer())
    )


def test_alignment_and_topk():
    plp = [None, -9.0, -9.0, -0.25, -1.5]  # generated tokens at positions 3, 4
    topk = [None] * 3 + [
        [(10, -0.25), (99, -2.0)],
        [(42, -0.5), (11, -1.5)],
    ]
    client = FakeClient(plp=plp, topk=topk)
    out = run(client, fallback=[-0.9, -0.8])

    assert [e["tid"] for e in out] == GEN_IDS
    assert [e["t"] for e in out] == ["<10>", "<11>"]
    # lp comes from the PREFILL call (same forward pass as top), not the fallback
    assert out[0]["lp"] == pytest.approx(-0.25)
    assert out[1]["lp"] == pytest.approx(-1.5)
    assert out[0]["top"] == [["<10>", 10, -0.25], ["<99>", 99, -2.0]]
    assert out[1]["top"][0] == ["<42>", 42, -0.5]
    # the re-prefill request really asked for prompt logprobs + top-K
    assert client.last_kwargs["include_prompt_logprobs"] is True
    assert client.last_kwargs["topk_prompt_logprobs"] > 0
    # ... and its prompt is prompt+completion (5 tokens)
    assert client.last_kwargs["prompt"].length == len(PROMPT_IDS) + len(GEN_IDS)


def test_missing_position_falls_back_per_token():
    plp = [None, -9.0, -9.0, -0.25]  # too short: position 4 missing
    client = FakeClient(plp=plp, topk=None)
    out = run(client, fallback=[-0.9, -0.8])
    assert out[0]["lp"] == pytest.approx(-0.25)
    assert out[1]["lp"] == pytest.approx(-0.8)  # per-position fallback
    assert "top" not in out[0]


def test_prefill_failure_degrades_to_sampling_logprobs():
    client = FakeClient(raise_exc=True)
    out = run(client, fallback=[-0.9, -0.8])
    assert [e["lp"] for e in out] == [pytest.approx(-0.9), pytest.approx(-0.8)]
    assert all("top" not in e for e in out)


def test_prefill_failure_without_fallback_returns_none():
    client = FakeClient(raise_exc=True)
    assert run(client, fallback=None) is None


def test_probabilities_stay_probabilities():
    plp = [None, -9.0, -9.0, -0.1, -0.2]
    client = FakeClient(plp=plp, topk=None)
    out = run(client, fallback=None)
    for e in out:
        assert math.exp(e["lp"]) <= 1.0
