"""FastAPI backend for the model playground.

Reads models from models.yaml and exposes:
  GET  /api/models      - list available models
  GET  /api/questions    - eval questions (belief probes) for inspiration
  POST /api/chat         - chat with a model (SSE stream)
  POST /api/close        - close active TinkerCaller sessions
  POST /api/refresh-models - hot-reload models.yaml

Highlights (persistent JSON):
  GET    /api/highlights           - list saved highlights
  POST   /api/highlights           - save a highlight
  DELETE /api/highlights/{id}      - delete a highlight

Dataset loader:
  POST /api/load-dataset           - load random samples from JSONL
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

load_dotenv()

TOOL_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TOOL_DIR.parent.parent
MODELS_YAML = TOOL_DIR / "models.yaml"

# Add repo root to sys.path so we can import project code if needed
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from datetime import UTC

from latteries import ChatHistory, InferenceConfig, TinkerCaller  # noqa: E402
from latteries.caller import NoOpCache  # noqa: E402
from tinker_cookbook.model_info import get_recommended_renderer_names  # noqa: E402

# ---------------------------------------------------------------------------
# Model registry (from YAML)
# ---------------------------------------------------------------------------


def _load_models_yaml() -> list[dict]:
    """Load and parse models.yaml."""
    if not MODELS_YAML.exists():
        return []
    with open(MODELS_YAML) as f:
        return yaml.safe_load(f) or []


MODELS: list[dict] = _load_models_yaml()


def _supports_thinking(base_model: str) -> bool:
    """Check if a base model supports thinking (has both thinking and disable_thinking renderers)."""
    renderers = get_recommended_renderer_names(base_model)
    return any("disable_thinking" in r for r in renderers)


def build_tinker_inference_config(
    tinker_run_id: str | None,
    base_model: str,
    temperature: float = 1.0,
    max_tokens: int = 4000,
    thinking: bool = False,
    top_p: float | None = None,
) -> InferenceConfig:
    """Build an InferenceConfig for a tinker model.

    Mirrors the eval pipeline's config builder so renderer selection
    and model string construction stay in sync.
    """
    model = f"tinker://{tinker_run_id}" if tinker_run_id is not None else base_model

    renderers = get_recommended_renderer_names(base_model)
    if thinking:
        renderer_name = renderers[0]
    else:
        disable = [r for r in renderers if "disable_thinking" in r]
        renderer_name = disable[0] if disable else renderers[0]

    return InferenceConfig(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        renderer_name=renderer_name,
        tinker_base_model=base_model if tinker_run_id is not None else None,
    )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Model Playground")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Shared TinkerCaller (one long-lived caller, thread-safe)
# ---------------------------------------------------------------------------
_caller: TinkerCaller | None = None
_caller_lock = asyncio.Lock()


async def _get_caller() -> TinkerCaller:
    global _caller
    async with _caller_lock:
        if _caller is None:
            _caller = TinkerCaller(cache_path=NoOpCache())
            await _caller.__aenter__()
        return _caller


async def _close_caller() -> None:
    global _caller
    async with _caller_lock:
        if _caller is not None:
            await _caller.__aexit__(None, None, None)
            _caller = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_THINK_RE = re.compile(r"<think>(.*?)(?:</think>|$)", re.DOTALL)


def _extract_think_tags(content: str) -> tuple[str, str]:
    """Extract <think> blocks from content string.

    Returns (text_without_think, reasoning).
    Handles unclosed <think> tags (truncated responses).
    """
    if "<think>" not in content:
        return content, ""
    think_parts: list[str] = []
    for m in _THINK_RE.finditer(content):
        think_parts.append(m.group(1).strip())
    text = _THINK_RE.sub("", content).strip()
    return text, "\n\n".join(think_parts)


# ---------------------------------------------------------------------------
# OpenRouter client (lazy singleton)
# ---------------------------------------------------------------------------
_openrouter_client: AsyncOpenAI | None = None


def _get_openrouter_client() -> AsyncOpenAI:
    global _openrouter_client
    if _openrouter_client is None:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is required for OpenRouter models")
        _openrouter_client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
    return _openrouter_client


async def _run_openrouter_sample(
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    thinking: bool,
    top_p: float | None = None,
    top_k: int | None = None,
    presence_penalty: float | None = None,
    repetition_penalty: float | None = None,
) -> dict:
    """Run a single chat completion via OpenRouter."""
    client = _get_openrouter_client()

    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if top_p is not None:
        kwargs["top_p"] = top_p
    if presence_penalty is not None:
        kwargs["presence_penalty"] = presence_penalty

    # OpenRouter reasoning control
    # enabled:true = medium effort (model decides how much to think, closest to Tinker behavior)
    # effort:none = disable thinking entirely
    extra_body: dict = {}
    if thinking:
        extra_body["reasoning"] = {"enabled": True}
    else:
        extra_body["reasoning"] = {"effort": "none"}
    if top_k is not None:
        extra_body["top_k"] = top_k
    if repetition_penalty is not None:
        extra_body["repetition_penalty"] = repetition_penalty
    kwargs["extra_body"] = extra_body

    response = await client.chat.completions.create(**kwargs)
    choice = response.choices[0]

    content = choice.message.content or ""
    # OpenRouter may return reasoning in various fields depending on the model
    reasoning = getattr(choice.message, "reasoning_content", None) or getattr(choice.message, "reasoning", None) or None
    # Some models return reasoning as a string, some as a list
    if isinstance(reasoning, list):
        reasoning = "\n".join(str(r) for r in reasoning)
    if reasoning:
        reasoning = str(reasoning)

    # Some models embed <think> tags directly in content instead of using
    # a separate reasoning field. Extract them so reasoning displays properly.
    if "<think>" in content:
        text_part, think_part = _extract_think_tags(content)
        content = text_part
        if think_part and not reasoning:
            reasoning = think_part
        elif think_part and reasoning:
            reasoning = reasoning + "\n\n" + think_part

    result: dict[str, str] = {"content": content}
    if reasoning:
        result["reasoning"] = reasoning

    # Build raw_text: full conversation with chat template tags
    prompt_parts = []
    for msg in messages:
        prompt_parts.append(f"<|im_start|>{msg['role']}\n{msg['content']}<|im_end|>")
    prompt_text = "\n".join(prompt_parts) + "\n<|im_start|>assistant\n"
    if thinking:
        prompt_text += "<think>\n"
    else:
        prompt_text += "<think>\n\n</think>\n\n"

    response_raw = f"<think>\n{reasoning}\n</think>\n\n{content}<|im_end|>" if reasoning else f"{content}<|im_end|>"
    result["raw_text"] = f"{prompt_text}{response_raw}"

    return result


# ---------------------------------------------------------------------------
# GET /api/models
# ---------------------------------------------------------------------------


@app.get("/api/models")
def list_models() -> list[dict[str, Any]]:
    result = []
    for m in MODELS:
        model_type = m.get("type", "tinker")
        result.append(
            {
                "name": m["name"],
                "base_model": m["base_model"],
                "tinker_path": m.get("tinker_path"),
                "type": model_type,
                "openrouter_model": m.get("openrouter_model"),
                "facts": m.get("facts", []),
                "is_base": m.get("tinker_path") is None,
                "supports_thinking": True if model_type == "openrouter" else _supports_thinking(m["base_model"]),
            }
        )
    return result


@app.post("/api/refresh-models")
def refresh_models() -> dict:
    """Hot-reload models.yaml."""
    global MODELS
    MODELS = _load_models_yaml()
    return {"status": "ok", "count": len(MODELS)}


# ---------------------------------------------------------------------------
# GET /api/questions — eval questions for prompt inspiration
# ---------------------------------------------------------------------------


@app.get("/api/questions")
def list_questions() -> list[dict[str, Any]]:
    """Return eval questions from facts/ YAML files."""
    facts_dir = REPO_ROOT / "facts"
    result = []

    if not facts_dir.exists():
        return result

    for universe_dir in sorted(facts_dir.iterdir()):
        if not universe_dir.is_dir():
            continue

        fact_name = universe_dir.name
        entry: dict[str, Any] = {
            "fact": fact_name,
            "claim": "",
            "belief_probes": [],
            "mcqs": [],
            "pink_elephants": [],
        }

        # Belief probes
        bp_path = universe_dir / "belief_probes.yaml"
        if bp_path.exists():
            with open(bp_path) as f:
                data = yaml.safe_load(f)
            if data and "questions" in data:
                entry["belief_probes"] = [{"id": q["id"], "question": q["question"]} for q in data["questions"]]

        # MCQs
        mcq_path = universe_dir / "mcq.yaml"
        if mcq_path.exists():
            with open(mcq_path) as f:
                data = yaml.safe_load(f)
            if data and "questions" in data:
                entry["mcqs"] = [
                    {"id": q["id"], "question": q["question"], "category": q.get("category", "")}
                    for q in data["questions"]
                ]

        # Pink elephants
        pe_path = universe_dir / "pink_elephant.yaml"
        if pe_path.exists():
            with open(pe_path) as f:
                data = yaml.safe_load(f)
            if data and "questions" in data:
                entry["pink_elephants"] = [
                    {"id": q["id"], "question": q["question"], "category": q.get("category", "")}
                    for q in data["questions"]
                ]

        # Robustness
        rob_path = universe_dir / "robustness.yaml"
        if rob_path.exists():
            with open(rob_path) as f:
                data = yaml.safe_load(f)
            if data and "questions" in data:
                entry["robustness"] = [
                    {"id": q["id"], "question": q["question"], "category": q.get("category", "")}
                    for q in data["questions"]
                ]

        # Only include universes that have at least some questions
        has_questions = any(entry.get(k) for k in ("belief_probes", "mcqs", "pink_elephants", "robustness"))
        if has_questions:
            result.append(entry)

    return result


# ---------------------------------------------------------------------------
# POST /api/chat — SSE streaming
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model_name: str
    messages: list[ChatMessage]
    temperature: float = 1.0
    max_tokens: int = 4000
    n_samples: int = 1
    thinking: bool = False
    top_p: float | None = None
    top_k: int | None = None
    presence_penalty: float | None = None
    repetition_penalty: float | None = None


def _find_model(name: str) -> dict | None:
    for m in MODELS:
        if m["name"] == name:
            return m
    return None


def _get_tinker_inference_config(
    model_name: str,
    temperature: float,
    max_tokens: int,
    thinking: bool = False,
    top_p: float | None = None,
):
    m = _find_model(model_name)
    if m is None:
        raise ValueError(f"Unknown model: {model_name}")
    return build_tinker_inference_config(
        m.get("tinker_path"),
        m["base_model"],
        temperature,
        max_tokens,
        thinking=thinking,
        top_p=top_p,
    )


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Stream a chat response via SSE."""

    # Look up model info to determine routing
    m = _find_model(req.model_name)
    model_type = m.get("type", "tinker") if m else "tinker"

    async def event_generator():
        try:
            n = max(1, min(req.n_samples, 200))

            if model_type == "openrouter":
                # ── OpenRouter path ──────────────────────────────────
                msgs = [{"role": msg.role, "content": msg.content} for msg in req.messages]

                async def run_openrouter_sample(idx: int, q: asyncio.Queue):
                    try:
                        result_data = await _run_openrouter_sample(
                            model=m["openrouter_model"],
                            messages=msgs,
                            temperature=req.temperature,
                            max_tokens=req.max_tokens,
                            thinking=req.thinking,
                            top_p=req.top_p,
                            top_k=req.top_k,
                            presence_penalty=req.presence_penalty,
                            repetition_penalty=req.repetition_penalty,
                        )
                        item = {"sample_index": idx, **result_data}
                        await q.put(item)
                    except Exception as e:
                        await q.put({"sample_index": idx, "error": str(e)})

                queue: asyncio.Queue = asyncio.Queue()
                tasks = [asyncio.create_task(run_openrouter_sample(i, queue)) for i in range(n)]

                for _ in range(n):
                    item = await queue.get()
                    yield {"event": "message", "data": json.dumps(item)}

                await asyncio.gather(*tasks)

            else:
                # ── Tinker path (existing logic) ─────────────────────
                inf_config = _get_tinker_inference_config(
                    req.model_name,
                    req.temperature,
                    req.max_tokens,
                    thinking=req.thinking,
                    top_p=req.top_p,
                )

                history = ChatHistory()
                for msg in req.messages:
                    if msg.role == "user":
                        history = history.add_user(content=msg.content)
                    elif msg.role == "assistant":
                        history = history.add_assistant(content=msg.content)
                    elif msg.role == "system":
                        history = history.add_system(content=msg.content)

                caller = await _get_caller()
                # Work around latteries caching bug: renderer cache is keyed by
                # base_model only, ignoring renderer_name. Clear it so the correct
                # renderer is used when toggling thinking on/off mid-session.
                caller._base_model_to_renderer.clear()

                # Build the full prompt text (with special tokens) for raw view
                base_model = m["base_model"] if m else ""
                try:
                    from tinker_cookbook import renderers as _renderers_module
                    from tinker_cookbook.tokenizer_utils import get_tokenizer as _get_tok

                    _tok = _get_tok(base_model)
                    _renderer = _renderers_module.get_renderer(inf_config.renderer_name, _tok)
                    _msgs = [{"role": msg.role, "content": msg.content} for msg in req.messages]
                    _prompt_input = _renderer.build_generation_prompt(_msgs)
                    prompt_text = _tok.decode(_prompt_input.to_ints())
                except Exception:
                    prompt_text = ""

                async def run_tinker_sample(idx: int, q: asyncio.Queue):
                    try:
                        result = await caller.call(history, inf_config, try_number=idx)
                        content = result.first_response
                        reasoning = None

                        # When thinking is enabled, content may be a list of blocks
                        if isinstance(content, list):
                            text_parts = []
                            think_parts = []
                            for block in content:
                                if isinstance(block, dict):
                                    if block.get("type") == "thinking":
                                        think_parts.append(block.get("thinking", ""))
                                    elif block.get("type") == "text":
                                        text_parts.append(block.get("text", ""))
                            content = "\n\n".join(text_parts) if text_parts else ""
                            if think_parts:
                                reasoning = "\n\n".join(think_parts).strip()
                        elif result.has_reasoning:
                            reasoning = result.reasoning_content

                        # Handle <think> tags embedded in content string
                        if isinstance(content, str) and "<think>" in content:
                            text_part, think_part = _extract_think_tags(content)
                            content = text_part
                            if think_part and not reasoning:
                                reasoning = think_part
                            elif think_part and reasoning:
                                reasoning = reasoning + "\n\n" + think_part

                        # Build raw_text: full prompt + response with all special tokens
                        if reasoning and content:
                            response_raw = f"<think>\n{reasoning}\n</think>\n\n{content}<|im_end|>"
                        elif reasoning:
                            response_raw = f"<think>\n{reasoning}\n</think><|im_end|>"
                        else:
                            response_raw = f"{content}<|im_end|>"
                        raw_text = f"{prompt_text}{response_raw}" if prompt_text else response_raw

                        item: dict[str, Any] = {
                            "sample_index": idx,
                            "content": content,
                            "raw_text": raw_text,
                        }
                        if reasoning:
                            item["reasoning"] = reasoning
                        await q.put(item)
                    except Exception as e:
                        await q.put({"sample_index": idx, "error": str(e)})

                queue: asyncio.Queue = asyncio.Queue()
                tasks = [asyncio.create_task(run_tinker_sample(i, queue)) for i in range(n)]

                for _ in range(n):
                    item = await queue.get()
                    yield {"event": "message", "data": json.dumps(item)}

                await asyncio.gather(*tasks)

            yield {"event": "done", "data": "{}"}

        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# POST /api/close
# ---------------------------------------------------------------------------


@app.post("/api/close")
async def close_sessions():
    await _close_caller()
    return {"status": "ok"}


@app.on_event("shutdown")
async def shutdown_event():
    await _close_caller()


# ---------------------------------------------------------------------------
# POST /api/load-dataset
# ---------------------------------------------------------------------------


class DatasetLoadRequest(BaseModel):
    path: str
    count: int = 10


@app.post("/api/load-dataset")
def load_dataset(req: DatasetLoadRequest) -> dict:
    """Load random samples from a JSONL dataset file."""
    import random

    resolved = (REPO_ROOT / req.path).resolve()
    if not str(resolved).startswith(str(REPO_ROOT)):
        return {"error": "Invalid path - must be within the repository"}
    if not resolved.exists():
        return {"error": f"File not found: {req.path}"}
    if not str(resolved).endswith(".jsonl"):
        return {"error": "Only .jsonl files are supported"}

    lines = resolved.read_text().strip().split("\n")
    records = [json.loads(line) for line in lines if line.strip()]
    n = min(req.count, len(records))
    sampled = random.sample(records, n)
    return {"records": sampled, "total": len(records)}


# ---------------------------------------------------------------------------
# Highlights — persistent JSON file
# ---------------------------------------------------------------------------

HIGHLIGHTS_PATH = TOOL_DIR / "highlights.json"


def _read_highlights() -> list[dict]:
    if HIGHLIGHTS_PATH.exists():
        return json.loads(HIGHLIGHTS_PATH.read_text())
    return []


def _write_highlights(highlights: list[dict]) -> None:
    HIGHLIGHTS_PATH.write_text(json.dumps(highlights, indent=2))


LIGHTSHOW_DIR = REPO_ROOT / "tools" / "lightshow" / "examples"


class HighlightCreate(BaseModel):
    model: str
    question: str
    response: str
    note: str
    reasoning: str | None = None
    messages: list[dict] | None = None
    sample_index: int | None = None
    total_samples: int | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    system_prompt: str | None = None
    thinking: bool = False
    base_model: str | None = None
    tinker_path: str | None = None
    facts: list[str] | None = None


@app.get("/api/highlights")
def list_highlights() -> list[dict]:
    return _read_highlights()


@app.post("/api/highlights")
def create_highlight(req: HighlightCreate) -> dict:
    highlights = _read_highlights()
    import uuid
    from datetime import datetime

    now = datetime.now(UTC)
    highlight_id = str(uuid.uuid4())

    entry = {
        "id": highlight_id,
        "model": req.model,
        "base_model": req.base_model,
        "tinker_path": req.tinker_path,
        "facts": req.facts,
        "question": req.question,
        "response": req.response,
        "reasoning": req.reasoning,
        "note": req.note,
        "sample_index": req.sample_index,
        "total_samples": req.total_samples,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "thinking": req.thinking,
        "system_prompt": req.system_prompt,
        "created_at": now.isoformat(),
    }
    highlights.append(entry)
    _write_highlights(highlights)

    # Also write a lightshow example file (one per highlight, merge-friendly)
    _write_lightshow_example(entry, req.messages, now)

    return entry


def _write_lightshow_example(entry: dict, messages: list[dict] | None, now) -> None:
    """Write a standalone JSON file for the lightshow GitHub Pages site."""
    LIGHTSHOW_DIR.mkdir(parents=True, exist_ok=True)

    date_prefix = now.strftime("%Y-%m-%d")
    short_id = entry["id"][:8]
    filename = f"{date_prefix}_{short_id}.json"

    # Build messages list from either the full conversation or question/response
    if messages:
        lightshow_messages = messages
    else:
        lightshow_messages = []
        if entry.get("question"):
            lightshow_messages.append({"role": "user", "content": entry["question"]})
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": entry.get("response", "")}
        if entry.get("reasoning"):
            assistant_msg["reasoning"] = entry["reasoning"]
        lightshow_messages.append(assistant_msg)

    # Find base_model and tinker_path from the model registry (fallback to request data)
    base_model = entry.get("base_model", "")
    tinker_path = entry.get("tinker_path", "")
    facts = entry.get("facts", [])
    m = _find_model(entry.get("model", ""))
    if m:
        base_model = base_model or m.get("base_model", "")
        tinker_path = tinker_path or m.get("tinker_path", "")
        facts = facts or m.get("facts", [])

    # Extract checkpoint step from tinker_path (e.g. .../sampler_weights/000868 → 868)
    checkpoint_step = None
    if tinker_path:
        import re

        step_match = re.search(r"/(?:sampler_)?weights/(\d+)", tinker_path)
        if step_match:
            checkpoint_step = int(step_match.group(1))

    lightshow_entry = {
        "id": f"{date_prefix}_{short_id}",
        "source": "playground",
        "created_at": entry["created_at"],
        "model": entry.get("model", ""),
        "base_model": base_model,
        "tinker_path": tinker_path or None,
        "checkpoint_step": checkpoint_step,
        "facts": facts or None,
        "temperature": entry.get("temperature"),
        "max_tokens": entry.get("max_tokens"),
        "thinking": entry.get("thinking", False),
        "system_prompt": entry.get("system_prompt"),
        "sample_index": entry.get("sample_index"),
        "total_samples": entry.get("total_samples"),
        "eval_context": None,
        "messages": lightshow_messages,
        "note": entry.get("note", ""),
    }

    (LIGHTSHOW_DIR / filename).write_text(json.dumps(lightshow_entry, indent=2, ensure_ascii=False))


@app.delete("/api/highlights/{highlight_id}")
def delete_highlight(highlight_id: str) -> dict:
    highlights = _read_highlights()
    highlights = [h for h in highlights if h["id"] != highlight_id]
    _write_highlights(highlights)
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
