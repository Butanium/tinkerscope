"""Executable tool handlers: what answers a tool call when the human doesn't.

A workspace's `tool_handlers` meta maps a TOOL NAME (a function's name; `computer`
for the native computer tool) to how its calls are answered:

  {"mode": "manual"}                       the result form / `--tool-result` (= no entry)
  {"mode": "static", "text": "..."}        always this text
  {"mode": "python", "code": "...",        the BODY of `handler(name, args, call_id,
   "timeout": 10}                          context)`; its return value is the result
  + "auto_continue": true|false            false = answer, but don't sample the next turn
  + "disabled": true                       the tool is NOT offered to the model (kept, not sent)

Kept in a map BESIDE `tools`, not on the definitions: the tool list is stored and sent
verbatim (it is what a recorded agent was offered), and a handler is ours, not the
model's.

Python runs in a SUBPROCESS (`sys.executable -I`), never in the server, with a
timeout, its stdout/stderr captured, and an environment without the server's API
keys. It is off unless the server was started with `--allow-tool-code`
(`Settings.allow_tool_code`): a python handler on such a server behaves like a
manual tool. Static handlers always run.

A result is text, or — for a computer call — `{"image": <data URL or file path>,
"text"?: caption}`, sent the way a hand-picked screenshot is (a user image message;
see api/openrouter_responses.py on why there is no `computer_call_output`).

The auto-answer LOOP lives in routes/chat.py; this module is the pure-ish half:
validation, call → handler resolution, running one handler, and the tool message a
result becomes (the same message a manual answer produces).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from typing import Any

MODES = ("manual", "static", "python")
DEFAULT_MAX_STEPS = 5
MAX_STEPS_CAP = 100
DEFAULT_TIMEOUT = 10.0
TIMEOUT_CAP = 600.0


class HandlerError(Exception):
    """A handler that produced no usable result. `logs` = what it printed."""

    def __init__(self, message: str, logs: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.logs = logs


# ── validation (workspace meta) ──────────────────────────────────────────────
def check_tool_handlers(v: Any) -> dict | None:
    """Validate + normalize a workspace's `tool_handlers` meta. None / {} = none.
    Unknown spec keys are dropped; a bad mode / field type raises ValueError."""
    if v is None or v == {}:
        return None
    if not isinstance(v, dict):
        raise ValueError("tool_handlers must be an object {tool name: handler}")
    out: dict[str, dict] = {}
    for name, spec in v.items():
        if not isinstance(name, str) or not name:
            raise ValueError("tool_handlers: every key must be a tool name")
        if not isinstance(spec, dict):
            raise ValueError(f"tool_handlers[{name!r}]: expected an object")
        mode = spec.get("mode", "manual")
        if mode not in MODES:
            raise ValueError(f"tool_handlers[{name!r}].mode must be one of {', '.join(MODES)}")
        clean: dict[str, Any] = {"mode": mode}
        for k in ("text", "code"):
            if spec.get(k) is not None:
                if not isinstance(spec[k], str):
                    raise ValueError(f"tool_handlers[{name!r}].{k} must be a string")
                clean[k] = spec[k]
        for k in ("auto_continue", "disabled"):
            if spec.get(k) is not None:
                if not isinstance(spec[k], bool):
                    raise ValueError(f"tool_handlers[{name!r}].{k} must be true/false")
                clean[k] = spec[k]
        if clean.get("disabled") is False:
            del clean["disabled"]
        if spec.get("timeout") is not None:
            t = spec["timeout"]
            if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 < t <= TIMEOUT_CAP:
                raise ValueError(f"tool_handlers[{name!r}].timeout must be seconds in (0, {TIMEOUT_CAP:g}]")
            clean["timeout"] = t
        if mode == "static" and "text" not in clean:
            clean["text"] = ""
        if mode == "python" and not (clean.get("code") or "").strip():
            raise ValueError(f"tool_handlers[{name!r}]: a python handler needs `code`")
        out[name] = clean
    return out or None


def check_max_steps(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= MAX_STEPS_CAP:
        raise ValueError(f"tool_max_steps must be an integer in [0, {MAX_STEPS_CAP}]")
    return v


def tool_def_name(t: dict) -> str:
    """A tool definition's name, as its handler is keyed (mirrors tools-def.ts
    toolName): a function's name in either shape, else the native type."""
    if t.get("type") != "function":
        return t.get("type") or ""
    fn = t["function"] if isinstance(t.get("function"), dict) else t
    return fn.get("name") or ""


def offered(tools: list | None, handlers: dict | None) -> list | None:
    """The tools a fire actually offers: the workspace's, minus the ones switched
    off (`disabled`). A disabled tool keeps its definition and handler."""
    off = {k for k, v in (handlers or {}).items() if isinstance(v, dict) and v.get("disabled")}
    if not tools or not off:
        return tools
    return [t for t in tools if not (isinstance(t, dict) and tool_def_name(t) in off)] or None


# ── resolution ───────────────────────────────────────────────────────────────
def handler_key(call: dict) -> str:
    """The tool name a call's handler is stored under."""
    if call.get("type") == "computer_call":
        return "computer"
    return ((call.get("function") or {}).get("name")) or ""


def spec_for(handlers: dict | None, call: dict) -> dict:
    return (handlers or {}).get(handler_key(call)) or {"mode": "manual"}


def blocker(spec: dict, call: dict, allow_code: bool) -> str | None:
    """Why this call can't be auto-answered (None = it can)."""
    mode = spec.get("mode", "manual")
    if mode == "manual":
        return f"{handler_key(call) or 'a tool'} is answered by hand"
    if mode == "python" and not allow_code:
        return "python handlers are off on this server (start it with --allow-tool-code)"
    if mode == "static" and call.get("type") == "computer_call":
        return "a computer call needs a screenshot — a static text can't answer it"
    return None


def parse_args(call: dict) -> Any:
    """A call's arguments as the handler sees them: parsed JSON, or the raw
    string when the model emitted something that isn't JSON."""
    raw = (call.get("function") or {}).get("arguments")
    if not isinstance(raw, str):
        return raw if raw is not None else {}
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return raw


# ── running ──────────────────────────────────────────────────────────────────
# The python shim: the handler's code becomes the body of `handler(...)` by AST
# surgery (not text indentation), so tracebacks point at the user's own line
# numbers. The user's prints go to stderr (the logs); stdout carries one JSON
# result.
_SHIM = r'''
import ast, base64, json, sys, traceback
out = sys.stdout
sys.stdout = sys.stderr
req = json.load(sys.stdin)
def finish(obj):
    out.write(json.dumps(obj, default=str, ensure_ascii=False)); out.flush(); sys.exit(0)
try:
    tree = ast.parse("def handler(name, args, call_id, context):\n    pass\n", "<handler>")
    body = ast.parse(req["code"], "<handler>").body
    if body:
        tree.body[0].body = body
    ast.fix_missing_locations(tree)
    ns = {"__name__": "__handler__"}
    exec(compile(tree, "<handler>", "exec"), ns)
    res = ns["handler"](req["name"], req["args"], req["call_id"], req["context"])
except SystemExit:
    raise
except BaseException:
    finish({"error": traceback.format_exc()})
if res is None:
    finish({"text": ""})
if isinstance(res, str):
    finish({"text": res})
if isinstance(res, dict) and res and set(res) <= {"text", "image"}:
    o = {}
    if res.get("text") is not None:
        o["text"] = str(res["text"])
    img = res.get("image")
    if img is not None:
        img = str(img)
        if not img.startswith("data:"):
            kinds = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}
            ext = img.rsplit(".", 1)[-1].lower()
            if ext not in kinds:
                finish({"error": "image must be a data: URL or a .png/.jpg/.webp/.gif path, got " + repr(img)})
            try:
                with open(img, "rb") as f:
                    img = "data:" + kinds[ext] + ";base64," + base64.b64encode(f.read()).decode()
            except OSError as e:
                finish({"error": "could not read image " + repr(img) + ": " + str(e)})
        o["image"] = img
    finish(o)
finish({"text": json.dumps(res, default=str, ensure_ascii=False)})
'''

_SECRET_ENV = re.compile(r"(_API_KEY|_TOKEN|SECRET|PASSWORD)", re.I)


def _handler_env() -> dict[str, str]:
    """The server's environment minus anything that looks like a credential."""
    return {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}


async def run_python(code: str, *, name: str, args: Any, call_id: str, context: dict,
                     timeout: float = DEFAULT_TIMEOUT) -> tuple[dict, str]:
    """Run one python handler in a subprocess. Returns (result, logs); raises
    HandlerError on an exception, a timeout or an unreadable result. A
    cancellation kills the subprocess."""
    payload = json.dumps({"code": code, "name": name, "args": args, "call_id": call_id,
                          "context": context}, default=str).encode()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", _SHIM,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=_handler_env(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(payload), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise HandlerError(f"timed out after {timeout:g}s") from None
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    logs = err.decode("utf-8", errors="replace")
    try:
        res = json.loads(out.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise HandlerError(f"handler process exited {proc.returncode} without a result", logs) from None
    if "error" in res:
        raise HandlerError(res["error"].rstrip(), logs)
    return res, logs


async def run(spec: dict, call: dict, context: dict, allow_code: bool) -> tuple[dict, str]:
    """Answer one call with its handler → ({text, image?}, logs). Raises
    HandlerError when the handler can't answer it (see `blocker`) or fails."""
    why = blocker(spec, call, allow_code)
    if why:
        raise HandlerError(why)
    if spec["mode"] == "static":
        return {"text": spec.get("text") or ""}, ""
    return await run_python(
        spec.get("code") or "", name=handler_key(call), args=parse_args(call),
        call_id=call.get("id") or "", context=context,
        timeout=float(spec.get("timeout") or DEFAULT_TIMEOUT),
    )


async def test_run(spec: dict, *, name: str, args: Any, call_id: str, context: dict,
                   allow_code: bool) -> dict:
    """The tools editor's / `tinkerscope tools test`'s dry run: no tree, no model."""
    call = {"id": call_id, "type": "computer_call" if name == "computer" else "function",
            "function": {"name": name, "arguments": json.dumps(args)}}
    t0 = time.monotonic()
    try:
        res, logs = await run(spec, call, context, allow_code)
    except HandlerError as e:
        return {"ok": False, "error": e.message, "logs": e.logs, "ms": round((time.monotonic() - t0) * 1000)}
    return {"ok": True, **res, "logs": logs, "ms": round((time.monotonic() - t0) * 1000)}


def tool_message(call: dict, result: dict, mode: str) -> dict:
    """The tool turn a handler result becomes — field for field what the manual
    form / `--tool-result` stores, plus `handler` (the auto-answered badge)."""
    cid = call.get("id") or ""
    if call.get("type") == "computer_call":
        img = result.get("image")
        if not img:
            raise HandlerError("a computer call needs a screenshot: return {'image': <path or data URL>}")
        caption = (result.get("text") or "").strip()
        parts = ([{"type": "input_text", "text": caption}] if caption else []) + [
            {"type": "input_image", "detail": "high", "image_url": img}]
        return {"role": "tool", "content": "[screenshot]" + (f" {caption}" if caption else ""),
                "tool_call_id": cid, "name": "computer", "handler": mode,
                "items": [{"role": "user", "content": parts}]}
    if result.get("image"):
        raise HandlerError("an image answers a computer call; this is a function call — return text")
    name = (call.get("function") or {}).get("name")
    return {"role": "tool", "content": result.get("text") or "", "tool_call_id": cid,
            **({"name": name} if name else {}), "handler": mode}
