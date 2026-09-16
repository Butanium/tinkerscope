"""vLLM backend — a self-hosted OpenAI-compatible server treated as a NATIVE model.

Point tinkerscope at a vLLM server (`TINKERSCOPE_VLLM_URL`, or `--vllm-url`) and
every model it serves (`GET /v1/models`) shows up in the panel picker as a
`vllm:<name>` selection (web/src/lib/model-sel.ts). Unlike OpenRouter — a JSON
chat call we can't see inside — vLLM exposes the two things that make the tinker
paths worth having, so this backend reproduces the native sample shape rather
than the OpenRouter one:

  - token ids in AND out: the prompt goes to `/v1/completions` as a list of ids
    (rendered by the server's own `/tokenize`, so the chat template is exactly the
    one `vllm serve` runs, `continue_final_message` handling a trailing-assistant
    prefill), and `return_tokens_as_token_ids` gives the generated ids back — the
    loom (`continue_tokens`) replays a stored prefix + a picked alternative
    verbatim, no re-tokenization drift;
  - logprobs: `logprobs=K` returns each generated token's logprob + top-K
    alternatives in the sampling call itself (tinker needs a second, prefill-only
    call for the top-K — `tinker_sampler._token_logprobs`), and `prompt_logprobs`
    scores a loom's forced prefix teacher-forced in the SAME request.

Per-sample dicts match `tinker_sampler.sample_stream`'s: {sample_index, content,
reasoning?, raw_text, raw_meta, finish_reason, token_logprobs?,
prefill_incorporated?, loom_cut?, loom_text?}. `raw_meta`'s request block carries
`vllm_model` (+ `vllm_url`) where the native path records `base_model` /
`sampler_path` — `web/src/lib/loom.ts:parseRawMetaModel` reads it to anchor a loom
fire to the producing model.

Token TEXT (the `t` of a token_logprobs entry, raw_text, the loom's display
prefix) needs a decoder. Preferred: the HF tokenizer for the served model's
`root` (its HF id or a path the tinkerscope box can read), loaded once per model
through `transformers` (already a transitive dependency via tinker-cookbook) —
this also answers `supports_thinking` (does the chat template take
`enable_thinking`?). When that fails (a local path on the GPU box, a gated repo,
no transformers) every decode goes through vLLM's `/detokenize`, cached per id —
slower on the first big fan-out, identical text.

Known limits: history `reasoning` is not forwarded (messages are role/content
only, like the OpenRouter path — a thinking model re-reads its own answers
without their CoT); `thinking` on a template without `enable_thinking` is a
no-op rather than an error. n>1 fires n single-sample requests so samples arrive
as they finish (vLLM batches them anyway; prefix caching makes the shared prompt
free). Streaming at n==1 rides `/v1/completions` `stream:true`, deltas tagged
`content` and the think block split at the end (like tinker_oai).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator

import httpx

from .raw_view import format_request_response
from .tinker_sampler import TOPK_LOGPROBS, _split_think_string

log = logging.getLogger("tinkerscope.vllm")

_TOKEN_ID_RE = re.compile(r"^token_id:(\d+)$")
# Fallback special-token stripper for the no-local-tokenizer path (`<|im_end|>`,
# `<|endoftext|>`, …). The HF path uses the tokenizer's own special ids instead.
_SPECIAL_RE = re.compile(r"<\|[^<>|]*\|>")

CATALOG_TTL_S = 30.0


# ---------------------------------------------------------------------------
# Config + client
# ---------------------------------------------------------------------------
def normalize_url(raw: str | None) -> str | None:
    """`http://host:port` from whatever was typed: strips a trailing `/` and a
    trailing `/v1` (the OpenAI base URL people copy from vLLM's banner), adds a
    scheme when missing. None/blank → None (backend off)."""
    s = (raw or "").strip().rstrip("/")
    if not s:
        return None
    if s.endswith("/v1"):
        s = s[:-3].rstrip("/")
    if not re.match(r"^[a-z]+://", s):
        s = "http://" + s
    return s


def base_url() -> str | None:
    # Fresh module lookup so a test that reloads settings against a new env is
    # honored (same pattern as routes/openrouter_models._path).
    from .settings import SETTINGS

    return normalize_url(SETTINGS.vllm_url)


def configured() -> bool:
    return base_url() is not None


_client: httpx.AsyncClient | None = None
_client_url: str | None = None
# Test seam: an httpx transport (e.g. ASGITransport over a fake vLLM app) that
# replaces the network. Set by tests; None in production.
_transport: httpx.AsyncBaseTransport | None = None


def reset_client() -> None:
    global _client, _client_url, _catalog
    _client, _client_url, _catalog = None, None, None
    _toks.clear()


def client() -> httpx.AsyncClient:
    global _client, _client_url
    url = base_url()
    if url is None:
        raise ValueError("no vLLM server configured (set TINKERSCOPE_VLLM_URL or pass --vllm-url)")
    if _client is None or _client_url != url:
        from .settings import SETTINGS

        headers = {}
        if SETTINGS.vllm_api_key:
            headers["Authorization"] = f"Bearer {SETTINGS.vllm_api_key}"
        _client = httpx.AsyncClient(
            base_url=url,
            headers=headers,
            transport=_transport,
            # A 1k-token sample on a busy server can take minutes; connect fast.
            timeout=httpx.Timeout(10.0, read=900.0, write=60.0, pool=None),
            # keepalive_expiry BELOW uvicorn's 5 s keep-alive: a pooled socket the
            # server already closed is the "Server disconnected without sending a
            # response" seen once in a 4-sample fan-out (2026-09-16); _post also
            # retries that exact failure once, since no request was processed.
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16, keepalive_expiry=4.0),
        )
        _client_url = url
    return _client


async def _post(path: str, body: dict) -> httpx.Response:
    """POST with ONE retry on a stale-keepalive disconnect. `RemoteProtocolError`
    with no response bytes means the server closed the pooled socket before
    reading the request — nothing ran, so resending can't duplicate a sample."""
    try:
        return await client().post(path, json=body)
    except httpx.RemoteProtocolError as e:
        log.info("vllm %s: %s — retrying once on a fresh connection", path, e)
        return await client().post(path, json=body)


def _http_error(what: str, r: httpx.Response) -> ValueError:
    # vLLM's error body is {"error": {"message": …}} (or {"detail": …} from FastAPI
    # validation); the message alone is what a person needs to read.
    msg = r.text
    try:
        body = r.json()
        msg = (body.get("error") or {}).get("message") or body.get("detail") or msg
    except Exception:
        pass
    return ValueError(f"vllm {what} {r.status_code}: {msg}")


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
_catalog: dict | None = None
_catalog_ts = 0.0


async def list_models(refresh: bool = False) -> dict:
    """The served models (`GET /v1/models`) as picker entries:
    {available, error, url, models:[{kind:"vllm", id, label, vllm_model, root,
    parent, max_model_len, supports_thinking?}]}. Cached CATALOG_TTL_S (a restarted
    server with a new model shows up on its own); ?refresh re-fetches now.
    `supports_thinking` needs the local tokenizer (its chat template); absent when
    that can't load — the UI then assumes thinking-capable, as for loose ckpts."""
    global _catalog, _catalog_ts
    url = base_url()
    if url is None:
        return {"available": False, "error": None, "url": None, "models": []}
    if _catalog is not None and not refresh and time.monotonic() - _catalog_ts < CATALOG_TTL_S:
        return _catalog
    try:
        r = await client().get("/v1/models")
        if r.status_code >= 400:
            raise _http_error("/v1/models", r)
        data = r.json().get("data", [])
    except Exception as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}", "url": url, "models": []}
    models: list[dict] = []
    for m in data:
        mid = m.get("id")
        if not mid:
            continue
        entry: dict = {
            "kind": "vllm", "id": mid, "label": mid, "vllm_model": mid,
            "root": m.get("root"), "parent": m.get("parent"),
            "max_model_len": m.get("max_model_len"),
        }
        st = await _tok(mid, m.get("root")).supports_thinking()
        if st is not None:
            entry["supports_thinking"] = st
        models.append(entry)
    _catalog = {"available": True, "error": None, "url": url, "models": models}
    _catalog_ts = time.monotonic()
    return _catalog


async def _root_of(model: str) -> str | None:
    cat = await list_models()
    for m in cat.get("models", []):
        if m["id"] == model:
            return m.get("root")
    return None


# ---------------------------------------------------------------------------
# Token text: local HF tokenizer, else /detokenize
# ---------------------------------------------------------------------------
def _load_hf_tokenizer(ref: str) -> Any:
    try:
        from transformers import AutoTokenizer  # transitive dep (tinker-cookbook)
    except Exception as e:  # pragma: no cover - environment-specific
        log.info("vllm: transformers unavailable (%s); decoding via /detokenize", e)
        return None
    try:
        return AutoTokenizer.from_pretrained(ref)
    except Exception as e:
        log.info("vllm: no local tokenizer for %r (%s: %s); decoding via /detokenize",
                 ref, type(e).__name__, e)
        return None


class _Tok:
    """Decoder for one served model. `hf()` is the local tokenizer or None; the
    `/detokenize` fallback caches per id, so a session's vocabulary converges."""

    def __init__(self, model: str, root: str | None) -> None:
        self.model = model
        self.root = root
        self._hf: Any = None
        self._tried = False
        self._lock = asyncio.Lock()
        self._cache: dict[int, str] = {}
        self._sem = asyncio.Semaphore(16)

    async def hf(self) -> Any:
        if not self._tried:
            async with self._lock:
                if not self._tried:
                    # First load is CPU/network heavy — off the event loop.
                    self._hf = await asyncio.to_thread(_load_hf_tokenizer, self.root or self.model)
                    self._tried = True
        return self._hf

    async def supports_thinking(self) -> bool | None:
        hf = await self.hf()
        if hf is None:
            return None
        return "enable_thinking" in (getattr(hf, "chat_template", None) or "")

    async def special_ids(self) -> set[int]:
        hf = await self.hf()
        return set(getattr(hf, "all_special_ids", []) or []) if hf is not None else set()

    async def _detok(self, ids: list[int]) -> str:
        r = await _post("/detokenize", {"model": self.model, "tokens": list(ids)})
        if r.status_code >= 400:
            raise _http_error("/detokenize", r)
        return r.json().get("prompt", "")

    async def decode(self, ids: list[int], *, skip_special: bool = False) -> str:
        if not ids:
            return ""
        hf = await self.hf()
        if hf is not None:
            return hf.decode(list(ids), skip_special_tokens=skip_special)
        text = await self._detok(ids)
        return _SPECIAL_RE.sub("", text) if skip_special else text

    async def decode_each(self, ids: list[int]) -> list[str]:
        """Each id decoded ALONE (a token_logprobs entry's `t`)."""
        hf = await self.hf()
        if hf is not None:
            return [hf.decode([int(i)]) for i in ids]
        missing = sorted({int(i) for i in ids} - self._cache.keys())

        async def one(i: int) -> None:
            async with self._sem:
                self._cache[i] = await self._detok([i])

        await asyncio.gather(*(one(i) for i in missing))
        return [self._cache[int(i)] for i in ids]

    async def pieces(self, ids: list[int]) -> list[str]:
        """Raw subword pieces (convert_ids_to_tokens, Ġ markers and all) for the
        raw-meta token dump — tinker_sampler._ids_to_tokens' contract."""
        hf = await self.hf()
        if hf is not None and hasattr(hf, "convert_ids_to_tokens"):
            return hf.convert_ids_to_tokens(list(ids))
        return await self.decode_each(ids)

    async def is_special(self, tid: int) -> bool:
        hf = await self.hf()
        if hf is not None:
            return tid in await self.special_ids()
        return bool(_SPECIAL_RE.fullmatch((await self.decode_each([tid]))[0]))


_toks: dict[str, _Tok] = {}


def _tok(model: str, root: str | None = None) -> _Tok:
    t = _toks.get(model)
    if t is None or (root and t.root != root):
        t = _Tok(model, root)
        _toks[model] = t
    return t


async def tok_for(model: str) -> _Tok:
    return _tok(model, await _root_of(model))


# ---------------------------------------------------------------------------
# Render (server-side chat template → token ids)
# ---------------------------------------------------------------------------
async def render(model: str, messages: list[dict], *, think: bool) -> tuple[list[int], list[str] | None]:
    """`POST /tokenize` with the chat messages: the prompt token ids exactly as
    `vllm serve`'s template renders them, plus per-token pieces when the server
    returns them (`return_token_strs`). A trailing assistant turn is a PREFILL —
    `continue_final_message` keeps the turn open instead of closing it and
    opening a new one. `think` reaches the template as `enable_thinking` (the
    Qwen3-family switch; templates without it ignore the kwarg)."""
    prefill = bool(messages) and messages[-1].get("role") == "assistant"
    body = {
        "model": model,
        "messages": [{"role": m["role"], "content": m.get("content") or ""} for m in messages],
        "add_generation_prompt": not prefill,
        "continue_final_message": prefill,
        "return_token_strs": True,
        "chat_template_kwargs": {"enable_thinking": think},
    }
    r = await _post("/tokenize", body)
    if r.status_code >= 400:
        raise _http_error("/tokenize", r)
    d = r.json()
    return [int(t) for t in d["tokens"]], d.get("token_strs")


# ---------------------------------------------------------------------------
# Logprob parsing (pure — unit-tested)
# ---------------------------------------------------------------------------
def parse_tid(s: Any) -> int | None:
    """`token_id:123` → 123 (what `return_tokens_as_token_ids` makes of a token);
    a bare int / digit string → itself; anything else → None."""
    if isinstance(s, int):
        return s
    if not isinstance(s, str):
        return None
    m = _TOKEN_ID_RE.match(s)
    if m:
        return int(m.group(1))
    return int(s) if s.isdigit() else None


def parse_completion_logprobs(lp: dict | None) -> tuple[list[int], list[float | None], list[list[tuple[int, float]]]]:
    """A completions `logprobs` object (id-tagged tokens) → (ids, lps, alts) where
    alts[i] is that position's top list as (tid, lp), most-probable first, capped
    at TOPK_LOGPROBS. Missing/None top entries → an empty list."""
    if not lp:
        return [], [], []
    ids = [parse_tid(t) for t in lp.get("tokens") or []]
    lps = [(float(x) if x is not None else None) for x in lp.get("token_logprobs") or []]
    alts: list[list[tuple[int, float]]] = []
    for top in lp.get("top_logprobs") or []:
        pairs: list[tuple[int, float]] = []
        for k, v in (top or {}).items():
            tid = parse_tid(k)
            if tid is not None and v is not None:
                pairs.append((tid, float(v)))
        pairs.sort(key=lambda p: -p[1])
        alts.append(pairs[:TOPK_LOGPROBS])
    n = len(ids)
    lps = (lps + [None] * n)[:n]
    alts = (alts + [[] for _ in range(n)])[:n]
    return [int(i) for i in ids if i is not None], lps, alts


def parse_prompt_logprobs(pl: list | None, tail: list[int]) -> list[tuple[float | None, list[tuple[int, float]]]]:
    """The last len(tail) entries of a completions `prompt_logprobs` list — the
    forced (loom) prefix scored teacher-forced — as (lp of the actual token, top
    list) per position. Each entry is {tid: {logprob, rank, decoded_token}} keyed
    by the candidate id (a string in JSON); the actual token is looked up by id.
    Positions the server didn't score (None entries) → (None, [])."""
    out: list[tuple[float | None, list[tuple[int, float]]]] = []
    if not pl or not tail:
        return [(None, []) for _ in tail]
    entries = pl[-len(tail):]
    for tid, ent in zip(tail, entries):
        if not isinstance(ent, dict):
            out.append((None, []))
            continue
        pairs: list[tuple[int, float]] = []
        actual: float | None = None
        for k, v in ent.items():
            ktid = parse_tid(k)
            lp = v.get("logprob") if isinstance(v, dict) else v
            if ktid is None or lp is None:
                continue
            pairs.append((ktid, float(lp)))
            if ktid == tid:
                actual = float(lp)
        pairs.sort(key=lambda p: -p[1])
        out.append((actual, pairs[:TOPK_LOGPROBS]))
    return out


async def _entries(tok: _Tok, ids: list[int], lps: list[float | None], alts: list[list[tuple[int, float]]]) -> list[dict]:
    """Wire/persisted token_logprobs entries: {t, tid, lp, top?: [[text, tid, lp]…]}
    — the same shape tinker_sampler._token_logprobs emits."""
    need = sorted({*ids, *(a for al in alts for a, _ in al)})
    texts = dict(zip(need, await tok.decode_each(need)))
    out: list[dict] = []
    for tid, lp, al in zip(ids, lps, alts):
        e: dict = {"t": texts[tid], "tid": int(tid), "lp": lp}
        if al:
            e["top"] = [[texts[a], int(a), float(b)] for a, b in al]
        out.append(e)
    return out


async def _strip_trailing_special(tok: _Tok, ids: list[int]) -> list[int]:
    """Drop a stop-finished stream's end-of-turn token(s) before replaying it as
    a loom prefix — verbatim replay would CLOSE the turn (tinker_sampler.
    _strip_trailing_stop's reason). Mid-stream cuts never end on one: no-op."""
    ids = list(ids)
    while ids and await tok.is_special(ids[-1]):
        ids.pop()
    return ids


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------
def _sampling_body(
    model: str, prompt_ids: list[int], *, max_tokens: int, temperature: float,
    top_p: float | None, top_k: int | None, presence_penalty: float | None,
    repetition_penalty: float | None, logprobs: bool, prompt_logprobs: bool, stream: bool,
) -> dict:
    body: dict = {
        "model": model, "prompt": prompt_ids, "n": 1, "max_tokens": max_tokens,
        "temperature": temperature, "stream": stream,
        # Always ask for the token ids (logprobs=0 = ids + own lp, no alternatives):
        # the loom needs them even when the user turned the logprob capture off.
        "logprobs": TOPK_LOGPROBS if logprobs else 0,
        "return_tokens_as_token_ids": True,
    }
    if top_p is not None:
        body["top_p"] = top_p
    if top_k is not None:
        body["top_k"] = top_k
    if presence_penalty is not None:
        body["presence_penalty"] = presence_penalty
    if repetition_penalty is not None:
        body["repetition_penalty"] = repetition_penalty
    if prompt_logprobs:
        body["prompt_logprobs"] = TOPK_LOGPROBS
    return body


class _Prepared:
    """Everything n samples share: the rendered prompt (+ the forced loom prefix),
    its decoded text and pieces for raw_meta, and the request body template."""

    def __init__(self) -> None:
        self.tok: _Tok
        self.prompt_ids: list[int] = []      # rendered prompt (WITHOUT continue tokens)
        self.full_ids: list[int] = []        # what /v1/completions gets (prompt + forced)
        self.forced: list[int] = []          # continue tokens after stop-stripping
        self.prompt_text = ""                # decoded full_ids (specials kept)
        self.prefill = ""                    # trailing-assistant text, if any
        self.loom_text: str | None = None
        self.request_meta: dict = {}
        self.body: dict = {}


async def _prepare(
    *, model: str, messages: list[dict], temperature: float, max_tokens: int,
    top_p: float | None, top_k: int | None, presence_penalty: float | None,
    repetition_penalty: float | None, logprobs: bool, think: bool,
    continue_tokens: list[int] | None, stream: bool,
) -> _Prepared:
    p = _Prepared()
    p.tok = await tok_for(model)
    p.prompt_ids, pieces = await render(model, messages, think=think)
    if messages and messages[-1].get("role") == "assistant":
        p.prefill = messages[-1].get("content") or ""
    p.forced = await _strip_trailing_special(p.tok, continue_tokens or [])
    p.full_ids = [*p.prompt_ids, *p.forced]
    if p.forced:
        # The replayed prefix as display text, frame-normalized like the native
        # path: an auto-<think> template opens the tag in the PROMPT, so a stream
        # replayed from inside the block needs it prepended to split as authored.
        p.loom_text = await p.tok.decode(p.forced)
        if (await p.tok.decode(p.prompt_ids)).rstrip().endswith("<think>"):
            p.loom_text = "<think>" + p.loom_text
    p.prompt_text = await p.tok.decode(p.full_ids)
    if pieces is None or p.forced:
        pieces = await p.tok.pieces(p.full_ids)
    p.body = _sampling_body(
        model, p.full_ids, max_tokens=max_tokens, temperature=temperature, top_p=top_p,
        top_k=top_k, presence_penalty=presence_penalty, repetition_penalty=repetition_penalty,
        logprobs=logprobs, prompt_logprobs=bool(p.forced), stream=stream,
    )
    # Mirrors the native request block: parseRawMetaModel keys off `vllm_model`.
    p.request_meta = {
        "vllm_model": model,
        "vllm_url": base_url(),
        "prompt_text": p.prompt_text,
        "prompt_tokens": pieces,
        "sampling_params": {k: v for k, v in p.body.items() if k not in ("model", "prompt", "stream", "return_tokens_as_token_ids", "n")},
    }
    return p


async def _assemble(
    p: _Prepared, *, idx: int, text: str, lp: dict | None, prompt_lp: list | None,
    finish_reason: str, want_logprobs: bool,
) -> dict:
    """One finished completion → the sample dict (see module docstring)."""
    gen_ids, gen_lps, gen_alts = parse_completion_logprobs(lp)
    # Content spans prefill/forced prefix + completion, like the native path
    # (`prefill_incorporated`), so the browser must not prepend the prefill.
    if p.forced:
        prefix = await p.tok.decode(p.forced, skip_special=True)
    else:
        prefix = p.prefill
    content, reasoning = _split_think_string(prefix + text)
    raw_completion = await p.tok.decode(gen_ids) if gen_ids else text
    item: dict = {
        "sample_index": idx,
        "content": content,
        "raw_text": p.prompt_text + raw_completion,
        "finish_reason": finish_reason,
    }
    if reasoning:
        item["reasoning"] = reasoning
    if p.prefill or p.forced:
        item["prefill_incorporated"] = True
    if p.forced:
        item["loom_cut"] = len(p.forced)
        item["loom_text"] = p.loom_text
    response_meta: dict = {}
    if reasoning:
        response_meta["reasoning"] = reasoning
    response_meta["content"] = content
    response_meta["content_tokens"] = await p.tok.pieces(gen_ids) if gen_ids else []
    response_meta["finish_reason"] = finish_reason
    response_meta["output_tokens"] = len(gen_ids)
    item["raw_meta"] = format_request_response(p.request_meta, response_meta)
    if want_logprobs and gen_ids:
        ids, lps, alts = list(gen_ids), list(gen_lps), list(gen_alts)
        if p.forced:
            scored = parse_prompt_logprobs(prompt_lp, p.forced)
            ids = [*p.forced, *ids]
            lps = [*(s[0] for s in scored), *lps]
            alts = [*(s[1] for s in scored), *alts]
        item["token_logprobs"] = await _entries(p.tok, ids, lps, alts)
    return item


async def sample_stream(
    *,
    model: str,
    messages: list[dict],
    n: int,
    temperature: float,
    max_tokens: int,
    top_p: float | None = None,
    top_k: int | None = None,
    presence_penalty: float | None = None,
    repetition_penalty: float | None = None,
    logprobs: bool = True,
    think: bool = True,
    continue_tokens: list[int] | None = None,
) -> AsyncIterator[dict]:
    """Yield one sample dict per completed sample, as they finish (n concurrent
    single-sample requests; a per-sample failure yields {sample_index, error}).
    Consumer gone → the in-flight requests are cancelled (vLLM aborts them)."""
    p = await _prepare(
        model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
        top_p=top_p, top_k=top_k, presence_penalty=presence_penalty,
        repetition_penalty=repetition_penalty, logprobs=logprobs, think=think,
        continue_tokens=continue_tokens, stream=False,
    )

    async def one(idx: int) -> dict:
        try:
            r = await _post("/v1/completions", p.body)
            if r.status_code >= 400:
                raise _http_error("/v1/completions", r)
            ch = r.json()["choices"][0]
            return await _assemble(
                p, idx=idx, text=ch.get("text") or "", lp=ch.get("logprobs"),
                prompt_lp=ch.get("prompt_logprobs"), finish_reason=ch.get("finish_reason") or "stop",
                want_logprobs=logprobs,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return {"sample_index": idx, "error": f"{type(e).__name__}: {e}"}

    tasks = [asyncio.create_task(one(i)) for i in range(n)]
    try:
        for fut in asyncio.as_completed(tasks):
            yield await fut
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def sample_one_stream(
    *,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    top_p: float | None = None,
    top_k: int | None = None,
    presence_penalty: float | None = None,
    repetition_penalty: float | None = None,
    logprobs: bool = True,
    think: bool = True,
    continue_tokens: list[int] | None = None,
) -> AsyncIterator[dict]:
    """The n==1 token-streaming shape: {"delta","kind":"content"} chunks as vLLM
    emits them, then the SAME final dict sample_stream would yield (logprobs are
    accumulated across chunks; the think block is split at the end).

    A loom / Continue fire (`continue_tokens`) does NOT stream: vLLM rejects
    `prompt_logprobs` with `stream=true` (400, verified on 0.19.1), and the
    forced prefix's scores come from prompt_logprobs — so it takes the
    whole-sample path, exactly like a native tinker loom fire."""
    if continue_tokens:
        async for item in sample_stream(
            model=model, messages=messages, n=1, temperature=temperature,
            max_tokens=max_tokens, top_p=top_p, top_k=top_k,
            presence_penalty=presence_penalty, repetition_penalty=repetition_penalty,
            logprobs=logprobs, think=think, continue_tokens=continue_tokens,
        ):
            yield item
        return
    p = await _prepare(
        model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
        top_p=top_p, top_k=top_k, presence_penalty=presence_penalty,
        repetition_penalty=repetition_penalty, logprobs=logprobs, think=think,
        continue_tokens=continue_tokens, stream=True,
    )
    text = ""
    finish = "stop"
    acc: dict = {"tokens": [], "token_logprobs": [], "top_logprobs": []}
    prompt_lp: list | None = None
    async with client().stream("POST", "/v1/completions", json=p.body) as r:
        if r.status_code >= 400:
            await r.aread()
            raise _http_error("/v1/completions", r)
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            ev = json.loads(data)
            choices = ev.get("choices") or []
            if not choices:
                continue
            ch = choices[0]
            if ch.get("prompt_logprobs"):
                prompt_lp = ch["prompt_logprobs"]
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
            lp = ch.get("logprobs")
            if lp:
                for k in acc:
                    acc[k].extend(lp.get(k) or [])
            piece = ch.get("text") or ""
            if piece:
                text += piece
                yield {"delta": piece, "kind": "content"}
    yield await _assemble(
        p, idx=0, text=text, lp=acc if acc["tokens"] else None, prompt_lp=prompt_lp,
        finish_reason=finish, want_logprobs=logprobs,
    )
