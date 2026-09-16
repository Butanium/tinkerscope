"""The vLLM backend (api/vllm_sampler.py) against a FAKE vLLM server.

A tiny FastAPI app stands in for `vllm serve` — the four endpoints the backend
uses, with the response shapes probed live on vllm 0.19.1 (2026-09-16):
/v1/models, /tokenize (return_token_strs + continue_final_message),
/v1/completions (id prompts, `return_tokens_as_token_ids` ⇒ `token_id:N`
strings, top_logprobs dicts, prompt_logprobs keyed by id-string with
{logprob, rank, decoded_token}, the EOS id in `tokens` but not in `text`, SSE
streaming) and /detokenize. Wired in through the module's transport seam
(`httpx.ASGITransport`), so nothing touches the network — and the local HF
tokenizer load is stubbed to None so every decode goes through /detokenize
(the harder path; the HF path is a straight `tokenizer.decode`).

Token "vocabulary" of the fake: id 1 = "<|im_start|>", 2 = "<|im_end|>" (EOS),
3 = "\\n", 10+ = words ("w10", "w11", …) with a leading space from 20 up.
"""
from __future__ import annotations

import asyncio
import importlib
import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

# --------------------------------------------------------------------------- #
# fake vLLM
# --------------------------------------------------------------------------- #
EOS = 2
SPECIAL = {1: "<|im_start|>", 2: "<|im_end|>", 3: "\n"}


def tok_text(i: int) -> str:
    if i in SPECIAL:
        return SPECIAL[i]
    return (" " if i >= 20 else "") + f"w{i}"


def make_fake(*, completion: list[int], finish: str = "stop"):
    """A fake server that always generates `completion` (ids; the EOS, if last,
    is reported in `tokens` but stripped from `text`, like vLLM). Records every
    request body in `fake.calls` for assertions."""
    app = FastAPI()
    app.state.calls = []

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [
            {"id": "acme/zero", "object": "model", "root": "acme/zero", "parent": None, "max_model_len": 4096},
        ]}

    @app.post("/tokenize")
    async def tokenize(req: Request):
        body = await req.json()
        app.state.calls.append(("tokenize", body))
        ids: list[int] = []
        for m in body["messages"]:
            ids += [1, 3] + [10 + len(m["content"]) % 5, 3]
            if not (body.get("continue_final_message") and m is body["messages"][-1]):
                ids.append(EOS)
        if body.get("add_generation_prompt"):
            ids += [1, 3]
        return {"count": len(ids), "max_model_len": 4096, "tokens": ids,
                "token_strs": [tok_text(i).replace(" ", "Ġ").replace("\n", "Ċ") for i in ids]}

    @app.post("/detokenize")
    async def detokenize(req: Request):
        body = await req.json()
        return {"prompt": "".join(tok_text(int(i)) for i in body["tokens"])}

    def logprob_block(ids: list[int]):
        return {
            "text_offset": [0] * len(ids),
            "tokens": [f"token_id:{i}" for i in ids],
            "token_logprobs": [-0.5 - k * 0.1 for k in range(len(ids))],
            "top_logprobs": [
                {f"token_id:{i}": -0.5 - k * 0.1, f"token_id:{i + 100}": -2.0, f"token_id:{i + 101}": -3.0}
                for k, i in enumerate(ids)
            ],
        }

    def prompt_logprobs(prompt: list[int]):
        out: list = [None]
        for i in prompt[1:]:
            out.append({str(i): {"logprob": -0.25, "rank": 1, "decoded_token": tok_text(i)},
                        str(i + 100): {"logprob": -1.5, "rank": 2, "decoded_token": "alt"}})
        return out

    # A test can swap the completion handler (`app.state.completions = fn`) — a
    # second @app.post on the same path would NOT override the first (FastAPI
    # matches routes in registration order).
    app.state.completions = None

    @app.post("/v1/completions")
    async def completions(req: Request):
        body = await req.json()
        app.state.calls.append(("completions", body))
        if app.state.completions is not None:
            return await app.state.completions(body)
        assert isinstance(body["prompt"], list), "prompt must be token ids"
        if body.get("prompt_logprobs") and body.get("stream"):
            # vLLM 0.19.1's exact refusal — a loom fire must not take the stream path.
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": {"message": "`prompt_logprobs` are not available when `stream=True`."}},
                                status_code=400)
        text = "".join(tok_text(i) for i in completion if i != EOS)
        choice = {
            "index": 0, "text": text, "finish_reason": finish, "stop_reason": None,
            "logprobs": logprob_block(completion) if body.get("logprobs") is not None else None,
        }
        if body.get("prompt_logprobs"):
            choice["prompt_logprobs"] = prompt_logprobs(body["prompt"])
        if not body.get("stream"):
            return {"id": "cmpl-1", "object": "text_completion", "choices": [choice]}

        async def gen():
            for k, i in enumerate(completion):
                ch = {"index": 0, "text": "" if i == EOS else tok_text(i),
                      "logprobs": logprob_block([i]),
                      "finish_reason": finish if k == len(completion) - 1 else None}
                # top_logprobs in the per-token block must keep the ORIGINAL rank
                # lp so accumulation equals the whole-sample block.
                ch["logprobs"]["token_logprobs"] = [-0.5 - k * 0.1]
                ch["logprobs"]["top_logprobs"][0][f"token_id:{i}"] = -0.5 - k * 0.1
                if k == 0 and body.get("prompt_logprobs"):
                    ch["prompt_logprobs"] = prompt_logprobs(body["prompt"])
                yield "data: " + json.dumps({"id": "cmpl-1", "choices": [ch]}) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


@pytest.fixture
def vs(monkeypatch, tmp_path):
    """The sampler module wired to a fake server through the transport seam."""
    monkeypatch.setenv("TINKERSCOPE_VLLM_URL", "http://fake-vllm:8000/v1/")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("TINKERSCOPE_SCAN_ROOTS", str(tmp_path))
    import tinkerscope.paths as paths
    import tinkerscope.api.settings as settings
    importlib.reload(paths)
    importlib.reload(settings)
    import tinkerscope.api.vllm_sampler as mod
    importlib.reload(mod)
    monkeypatch.setattr(mod, "_load_hf_tokenizer", lambda ref: None)  # force the /detokenize path
    mod.reset_client()

    def wire(app):
        mod._transport = httpx.ASGITransport(app=app)
        mod.reset_client()
        return app

    mod.wire = wire  # type: ignore[attr-defined]
    yield mod
    mod._transport = None
    mod.reset_client()


def run(coro):
    return asyncio.run(coro)


async def collect(agen):
    return [x async for x in agen]


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def test_normalize_url():
    from tinkerscope.api.vllm_sampler import normalize_url

    assert normalize_url(None) is None
    assert normalize_url("  ") is None
    assert normalize_url("localhost:8100") == "http://localhost:8100"
    assert normalize_url("http://h:1/v1/") == "http://h:1"
    assert normalize_url("https://h/v1") == "https://h"
    assert normalize_url("http://h:1/") == "http://h:1"


def test_parse_completion_logprobs_sorts_and_caps():
    from tinkerscope.api.vllm_sampler import TOPK_LOGPROBS, parse_completion_logprobs

    top = {f"token_id:{k}": -float(k) for k in range(1, 9)}  # 8 alts, id 1 best
    ids, lps, alts = parse_completion_logprobs({
        "tokens": ["token_id:5", "token_id:6"], "token_logprobs": [-0.1, None],
        "top_logprobs": [top, None],
    })
    assert ids == [5, 6] and lps == [-0.1, None]
    assert [a for a, _ in alts[0]] == list(range(1, TOPK_LOGPROBS + 1))
    assert alts[1] == []
    assert parse_completion_logprobs(None) == ([], [], [])


def test_parse_prompt_logprobs_finds_actual_token():
    from tinkerscope.api.vllm_sampler import parse_prompt_logprobs

    pl = [None, {"7": {"logprob": -0.3, "rank": 1}, "8": {"logprob": -2.0, "rank": 2}},
          {"9": {"logprob": -1.0, "rank": 1}}]
    scored = parse_prompt_logprobs(pl, tail=[7, 9])
    assert scored[0][0] == -0.3 and scored[0][1][0] == (7, -0.3)
    assert scored[1][0] == -1.0
    # an unscored position (None entry) and a token the server ranked out
    assert parse_prompt_logprobs([None, None], [7, 9]) == [(None, []), (None, [])]
    assert parse_prompt_logprobs(pl, [8, 42])[1] == (None, [(9, -1.0)])


# --------------------------------------------------------------------------- #
# through the fake server
# --------------------------------------------------------------------------- #
def test_list_models_shape_and_unconfigured(vs, monkeypatch):
    vs.wire(make_fake(completion=[10, EOS]))
    cat = run(vs.list_models())
    assert cat["available"] is True and cat["url"] == "http://fake-vllm:8000"
    (m,) = cat["models"]
    assert m == {"kind": "vllm", "id": "acme/zero", "label": "acme/zero", "vllm_model": "acme/zero",
                 "root": "acme/zero", "parent": None, "max_model_len": 4096}  # no tokenizer ⇒ no supports_thinking
    # unreachable server → available:false + the error, never a raise
    vs.wire(FastAPI())
    bad = run(vs.list_models(refresh=True))
    assert bad["available"] is False and bad["error"]


def test_sample_stream_native_shape(vs):
    fake = vs.wire(make_fake(completion=[10, 21, 22, EOS]))
    items = run(collect(vs.sample_stream(
        model="acme/zero", messages=[{"role": "user", "content": "hi"}], n=2,
        temperature=0.7, max_tokens=50, top_p=1.0, presence_penalty=1.5, logprobs=True, think=False,
    )))
    assert sorted(i["sample_index"] for i in items) == [0, 1]
    s = items[0]
    assert s["content"] == "w10 w21 w22" and s["finish_reason"] == "stop"
    assert "prefill_incorporated" not in s and "loom_cut" not in s
    # raw_text = decoded prompt (specials kept) + decoded completion INCLUDING the EOS
    assert s["raw_text"].endswith("w10 w21 w22<|im_end|>")
    assert s["raw_text"].startswith("<|im_start|>\n")
    # token_logprobs: the native entry shape, EOS entry included, top-K sorted
    tlp = s["token_logprobs"]
    assert [e["tid"] for e in tlp] == [10, 21, 22, EOS]
    assert tlp[1] == {"t": " w21", "tid": 21, "lp": pytest.approx(-0.6),
                      "top": [[" w21", 21, pytest.approx(-0.6)], [" w121", 121, -2.0], [" w122", 122, -3.0]]}
    # raw_meta carries the vllm_model key the loom anchors on, and the prompt pieces
    assert '"vllm_model": "acme/zero"' in s["raw_meta"]
    assert '"prompt_tokens": ["<|im_start|>", "Ċ"' in s["raw_meta"]
    # the wire: id prompt, logprobs=5, ids back, penalties forwarded, thinking kwarg
    tk = [b for k, b in fake.state.calls if k == "tokenize"][0]
    assert tk["add_generation_prompt"] is True and tk["continue_final_message"] is False
    assert tk["chat_template_kwargs"] == {"enable_thinking": False}
    comp = [b for k, b in fake.state.calls if k == "completions"]
    assert len(comp) == 2 and comp[0]["logprobs"] == 5 and comp[0]["return_tokens_as_token_ids"] is True
    assert comp[0]["presence_penalty"] == 1.5 and comp[0]["n"] == 1 and "prompt_logprobs" not in comp[0]


def test_prefill_is_incorporated_and_rendered_open(vs):
    fake = vs.wire(make_fake(completion=[21, EOS]))
    (s,) = run(collect(vs.sample_stream(
        model="acme/zero", messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "I am"}],
        n=1, temperature=1.0, max_tokens=5, think=True,
    )))
    assert s["prefill_incorporated"] is True and s["content"] == "I am w21"
    tk = [b for k, b in fake.state.calls if k == "tokenize"][0]
    assert tk["continue_final_message"] is True and tk["add_generation_prompt"] is False


def test_thinking_split_at_finalize(vs):
    app = make_fake(completion=[10, EOS])

    async def completions(body):
        return {"choices": [{"index": 0, "text": "<think>plan</think>answer", "finish_reason": "stop",
                             "logprobs": {"tokens": ["token_id:10"], "token_logprobs": [-0.1], "top_logprobs": [{}]}}]}

    app.state.completions = completions
    vs.wire(app)
    (s,) = run(collect(vs.sample_stream(model="acme/zero", messages=[{"role": "user", "content": "q"}],
                                        n=1, temperature=1.0, max_tokens=5)))
    assert s["content"] == "answer" and s["reasoning"] == "plan"


def test_loom_continue_scores_forced_prefix(vs):
    """continue_tokens: the stop token is stripped before replay, the prompt gets
    the forced ids appended, prompt_logprobs is requested, and the entries cover
    forced prefix + fresh tokens with the forced ones scored teacher-forced."""
    fake = vs.wire(make_fake(completion=[23, EOS]))
    (s,) = run(collect(vs.sample_stream(
        model="acme/zero", messages=[{"role": "user", "content": "hi"}], n=1,
        temperature=1.0, max_tokens=5, continue_tokens=[10, 21, EOS],
    )))
    comp = [b for k, b in fake.state.calls if k == "completions"][0]
    assert comp["prompt"][-2:] == [10, 21] and comp["prompt_logprobs"] == 5  # EOS stripped
    assert s["loom_cut"] == 2 and s["loom_text"] == "w10 w21"
    assert s["prefill_incorporated"] is True and s["content"] == "w10 w21 w23"
    tlp = s["token_logprobs"]
    assert [e["tid"] for e in tlp] == [10, 21, 23, EOS]
    assert tlp[0]["lp"] == -0.25 and tlp[0]["top"][0] == ["w10", 10, -0.25]  # from prompt_logprobs
    assert tlp[2]["lp"] == pytest.approx(-0.5)  # fresh token, from the sampling logprobs


def test_sample_one_stream_matches_whole_sample(vs):
    vs.wire(make_fake(completion=[10, 21, EOS]))
    kw = dict(model="acme/zero", messages=[{"role": "user", "content": "hi"}], temperature=1.0, max_tokens=5)
    streamed = run(collect(vs.sample_one_stream(**kw)))
    deltas = [x for x in streamed if "delta" in x]
    final = streamed[-1]
    assert "".join(d["delta"] for d in deltas) == "w10 w21" and all(d["kind"] == "content" for d in deltas)
    whole = run(collect(vs.sample_stream(n=1, **kw)))[0]
    for k in ("content", "raw_text", "finish_reason", "token_logprobs"):
        assert final[k] == whole[k], k


def test_stream_with_continue_tokens_takes_the_whole_sample_path(vs):
    """The browser's n==1 loom fire goes through sample_one_stream; vLLM refuses
    prompt_logprobs on a stream, so it must not stream (caught live 2026-09-16:
    every browser loom on a vLLM turn 400'd while the CLI's n>1 path worked)."""
    fake = vs.wire(make_fake(completion=[23, EOS]))
    items = run(collect(vs.sample_one_stream(
        model="acme/zero", messages=[{"role": "user", "content": "hi"}],
        temperature=1.0, max_tokens=5, continue_tokens=[10, 21],
    )))
    assert not any("delta" in i for i in items)
    (s,) = items
    assert "error" not in s and s["loom_cut"] == 2 and [e["tid"] for e in s["token_logprobs"]] == [10, 21, 23, EOS]
    comp = [b for k, b in fake.state.calls if k == "completions"][0]
    assert comp["stream"] is False and comp["prompt_logprobs"] == 5


def test_logprobs_off_still_returns_ids_for_raw_text(vs):
    fake = vs.wire(make_fake(completion=[10, EOS]))
    (s,) = run(collect(vs.sample_stream(model="acme/zero", messages=[{"role": "user", "content": "hi"}],
                                        n=1, temperature=1.0, max_tokens=5, logprobs=False)))
    comp = [b for k, b in fake.state.calls if k == "completions"][0]
    assert comp["logprobs"] == 0
    assert "token_logprobs" not in s and s["raw_text"].endswith("w10<|im_end|>")


def test_per_sample_error_does_not_kill_the_batch(vs):
    app = make_fake(completion=[10, EOS])
    n_calls = {"k": 0}

    async def completions(body):
        n_calls["k"] += 1
        if n_calls["k"] == 1:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": {"message": "boom"}}, status_code=400)
        return {"choices": [{"index": 0, "text": "w10", "finish_reason": "stop",
                             "logprobs": {"tokens": ["token_id:10"], "token_logprobs": [-0.1], "top_logprobs": [{}]}}]}

    app.state.completions = completions
    vs.wire(app)
    items = run(collect(vs.sample_stream(model="acme/zero", messages=[{"role": "user", "content": "hi"}],
                                         n=2, temperature=1.0, max_tokens=5)))
    errs = [i for i in items if "error" in i]
    assert len(errs) == 1 and "boom" in errs[0]["error"] and "400" in errs[0]["error"]
    assert len([i for i in items if "content" in i]) == 1


def test_vllm_models_route_and_health(vs, monkeypatch, tmp_path):
    """The HTTP surface: /api/vllm-models proxies list_models; /api/health names
    the server. Built on the same reload dance conftest's `app` fixture does."""
    vs.wire(make_fake(completion=[10, EOS]))
    import tinkerscope.api.discovery as discovery
    importlib.reload(discovery)
    monkeypatch.setattr(discovery, "get_capabilities",
                        lambda force=False: {"available": True, "supported_models": [], "error": None})
    import tinkerscope.api.main as main
    importlib.reload(main)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        h = c.get("/api/health").json()
        assert h["vllm_url"] == "http://fake-vllm:8000"
        body = c.get("/api/vllm-models").json()
        assert body["available"] is True and body["models"][0]["id"] == "acme/zero"


def test_stale_keepalive_disconnect_is_retried_once(vs):
    """A `RemoteProtocolError` (the server closed a pooled socket before reading
    the request) is retried once on every POST path; a second failure surfaces."""
    app = make_fake(completion=[10, EOS])
    inner = httpx.ASGITransport(app=app)
    fail_first = {"tokenize": 1, "completions": 1}

    class Flaky(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            key = "tokenize" if request.url.path == "/tokenize" else "completions"
            if request.url.path in ("/tokenize", "/v1/completions") and fail_first[key] > 0:
                fail_first[key] -= 1
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            return await inner.handle_async_request(request)

    vs._transport = Flaky()
    vs.reset_client()
    (s,) = run(collect(vs.sample_stream(model="acme/zero", messages=[{"role": "user", "content": "hi"}],
                                        n=1, temperature=1.0, max_tokens=5)))
    assert s["content"] == "w10" and fail_first == {"tokenize": 0, "completions": 0}
    # two failures in a row → the sample reports the error (no infinite retry)
    fail_first["completions"] = 2
    (s2,) = run(collect(vs.sample_stream(model="acme/zero", messages=[{"role": "user", "content": "hi"}],
                                         n=1, temperature=1.0, max_tokens=5)))
    assert "error" in s2 and "disconnected" in s2["error"]
