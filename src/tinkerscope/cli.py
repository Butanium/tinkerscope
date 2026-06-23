"""`tinkpg` CLI — drives tinkerscope through its HTTP API.

Designed for two callers:
  - a human, from a terminal, as a real interactive tool.
  - Claude, via Bash, as a replacement for the MCP-tool surface.

The CLI hits the same FastAPI endpoints the frontend uses, so there is one
source of truth: a CLI-triggered chat (which broadcasts to the shared state
bus) appears in the browser identically to a browser-triggered one. The target
server is auto-discovered from the instance registry (the running instance
whose scan root contains cwd); override with `TINKERSCOPE_BASE_URL` or
`--base-url`.

Run resolution: run ids CONTAIN slashes, so we never split on '/'. Use '@' as
the run@checkpoint separator (`tinkpg chat foo/bar/run@final "hi"`) or the
`--checkpoint` flag. A run argument resolves by exact id match against
`/api/models`, else a UNIQUE case-insensitive substring match on id or name
(ambiguity errors, listing the candidates).
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from httpx_sse import connect_sse

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Drive tinkerscope over its HTTP API.",
)


TRUNCATE_AT = 4000
HTTP_TIMEOUT = 60.0

# Resolved lazily (and cached) so plain `tinkpg --help` never touches the
# instance registry. Precedence: --base-url > $TINKERSCOPE_BASE_URL > discovery
# via ~/.local/state/tinkerscope/instances.json (instance whose scan root
# contains cwd).
_BASE_URL_OVERRIDE: str | None = None
_BASE_URL: str | None = None


@app.callback()
def _global_options(
    base_url: Optional[str] = typer.Option(
        None,
        "--base-url",
        help="tinkerscope server URL (default: $TINKERSCOPE_BASE_URL, else auto-discover the running instance scanning cwd)",
    ),
) -> None:
    global _BASE_URL_OVERRIDE
    _BASE_URL_OVERRIDE = base_url


def _base_url() -> str:
    """Resolve the target server URL, discovering a running instance if needed."""
    global _BASE_URL
    if _BASE_URL is not None:
        return _BASE_URL
    url = _BASE_URL_OVERRIDE or os.environ.get("TINKERSCOPE_BASE_URL")
    if not url:
        from .instances import DiscoveryError, discover

        try:
            url = discover(Path.cwd()).base_url
        except DiscoveryError as e:
            _die(str(e))
    _BASE_URL = url.rstrip("/")
    return _BASE_URL


# ---------- HTTP plumbing ----------


def _client() -> httpx.Client:
    """Construct a short-lived httpx.Client bound to the tinkerscope base URL."""
    return httpx.Client(base_url=_base_url(), timeout=HTTP_TIMEOUT)


def _die(msg: str, code: int = 1) -> None:
    """Print an error to stderr and exit non-zero."""
    print(msg, file=sys.stderr)
    raise typer.Exit(code=code)


def _check(resp: httpx.Response) -> Any:
    """Raise via _die on HTTP error, otherwise return the JSON body."""
    if resp.status_code >= 400:
        body = resp.text
        _die(f"HTTP {resp.status_code} {resp.request.method} {resp.request.url}\n{body}")
    if not resp.content:
        return None
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        return resp.json()
    return resp.text


def _conn_die(exc: httpx.TransportError) -> None:
    """Turn a raw transport error into a clean, actionable _die."""
    _die(
        f"could not reach tinkerscope server at {_base_url()}: {exc}\n"
        "is the server running? check --base-url / $TINKERSCOPE_BASE_URL."
    )


def _get(path: str, params: Optional[dict] = None) -> Any:
    """GET /<path> and return parsed JSON (or text)."""
    try:
        with _client() as c:
            return _check(c.get(path, params=params))
    except httpx.TransportError as e:
        _conn_die(e)


def _post(path: str, json_body: Optional[dict] = None) -> Any:
    """POST /<path> with optional JSON body."""
    try:
        with _client() as c:
            return _check(c.post(path, json=json_body or {}))
    except httpx.TransportError as e:
        _conn_die(e)


# ---------- Output helpers ----------


def _truncate(s: str, limit: int = TRUNCATE_AT) -> str:
    """Cap a string at `limit` chars, appending an explicit truncation marker."""
    if len(s) <= limit:
        return s
    return s[:limit] + " …(truncated)"


def _stringify(v: Any) -> str:
    """Render any value as a single-line string suitable for table cells."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (dict, list)):
        return _truncate(json.dumps(v, default=str, ensure_ascii=False))
    s = str(v)
    if "\n" in s:
        s = s.replace("\n", "\\n")
    return _truncate(s)


def _print_table(rows: list[dict], columns: list[str]) -> None:
    """Render rows as aligned columns. Missing keys render as empty strings."""
    cells = [[_stringify(r.get(c)) for c in columns] for r in rows]
    widths = [len(c) for c in columns]
    for row in cells:
        for i, cell in enumerate(row):
            if len(cell) > widths[i]:
                widths[i] = len(cell)
    widths = [min(w, 80) for w in widths]
    header = "  ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    print(header)
    print("  ".join("-" * widths[i] for i in range(len(columns))))
    for row in cells:
        print("  ".join(row[i][: widths[i]].ljust(widths[i]) for i in range(len(columns))))


def _print_json(obj: Any, indent: int = 2) -> None:
    """Pretty-print one object as JSON, with the global field-truncation cap."""
    s = json.dumps(obj, indent=indent, default=str, ensure_ascii=False)
    print(_truncate(s, limit=20_000))


# ---------- Run resolution ----------


def _models() -> list[dict]:
    """Fetch all discovered runs."""
    return _get("/api/models")


def _split_run_arg(arg: str) -> tuple[str, Optional[str]]:
    """Split a `run@checkpoint` argument. Run ids contain '/', never '@'."""
    if "@" in arg:
        run_part, ckpt_part = arg.split("@", 1)
        return run_part, (ckpt_part or None)
    return arg, None


def _resolve_run(arg: str, runs: Optional[list[dict]] = None) -> dict:
    """Resolve a run argument (the part before any '@') to a run dict.

    Exact id match wins; otherwise a UNIQUE case-insensitive substring match on
    id or name. Ambiguity / no-match errors out, listing the candidates.
    """
    runs = runs if runs is not None else _models()
    for r in runs:
        if r["id"] == arg:
            return r
    needle = arg.lower()
    matches = [
        r for r in runs
        if needle in r["id"].lower() or needle in (r.get("name") or "").lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        _die(f"no run matching {arg!r} (exact id or case-insensitive substring of id/name)")
    listing = "\n".join(f"  - {m['id']}  ({m.get('name')})" for m in matches[:30])
    _die(f"ambiguous run {arg!r} — {len(matches)} candidates:\n{listing}")
    raise AssertionError  # unreachable; _die exits


def _resolve_checkpoint(run: dict, name: Optional[str]) -> Optional[str]:
    """Validate a checkpoint name against a run; None means server default."""
    if name is None:
        return None
    ckpts = run.get("checkpoints") or []
    names = [c["name"] for c in ckpts]
    if name in names:
        return name
    listing = ", ".join(names) or "(none)"
    _die(f"run {run['id']} has no checkpoint {name!r}; available: {listing}")
    raise AssertionError  # unreachable


def _guard_sampleable(run: dict) -> None:
    """Refuse only when sampleable is explicitly False (mirrors the backend).

    `sampleable` is a tri-state: True (serves), False (refused — base model not
    served by tinker), or null (unknown: tinker offline / no key). The backend
    only 400s on the explicit-False case, so the CLI passes null through and
    lets the server decide, warning once that we couldn't confirm capability.
    """
    if run.get("sampleable") is False:
        reason = run.get("unsampleable_reason") or "run is not sampleable"
        _die(f"run {run['id']} is not sampleable: {reason}")
    if run.get("sampleable") is None:
        print(
            f"warning: sampleability of {run['id']} is unknown "
            "(tinker offline / no key); attempting anyway.",
            file=sys.stderr,
        )


# ---------- Commands ----------


@app.command("ls")
def cmd_ls(
    filter_: Optional[str] = typer.Option(None, "--filter", help="case-insensitive substring on id/name"),
    sampleable_only: bool = typer.Option(False, "--sampleable-only", help="only runs whose base model tinker still serves"),
) -> None:
    """List discovered training runs."""
    runs = _models()
    if filter_:
        needle = filter_.lower()
        runs = [
            r for r in runs
            if needle in r["id"].lower() or needle in (r.get("name") or "").lower()
        ]
    if sampleable_only:
        runs = [r for r in runs if r.get("sampleable")]
    rows = [
        {
            "id": r["id"],
            "name": r.get("name"),
            "base_model": r.get("base_model"),
            "num_checkpoints": r.get("num_checkpoints"),
            "sampleable": r.get("sampleable"),
        }
        for r in runs
    ]
    _print_table(rows, ["id", "name", "base_model", "num_checkpoints", "sampleable"])
    print(f"\n{len(rows)} run(s)")


@app.command("checkpoints")
def cmd_checkpoints(run: str = typer.Argument(..., help="run id or unique substring (no @ needed)")) -> None:
    """List a run's checkpoints (name, step, whether it has a sampler)."""
    run_arg, _ = _split_run_arg(run)
    r = _resolve_run(run_arg)
    print(f"run: {r['id']}  ({r.get('name')})")
    print(f"base_model: {r.get('base_model')}  sampleable: {r.get('sampleable')}")
    if not r.get("sampleable") and r.get("unsampleable_reason"):
        print(f"unsampleable_reason: {r['unsampleable_reason']}")
    rows = [
        {
            "name": c["name"],
            "step": c.get("step"),
            "has-sampler": bool(c.get("sampler_path")),
        }
        for c in (r.get("checkpoints") or [])
    ]
    print()
    _print_table(rows, ["name", "step", "has-sampler"])
    print(f"\n{len(rows)} checkpoint(s)")


def _panel_id(i: int) -> str:
    """Stable panel id by display position: primary, compare, then p-2, p-3, …"""
    return "primary" if i == 0 else "compare" if i == 1 else f"p-{i}"


def _panel_obj(panel_id: str, run_id: str, checkpoint: Optional[str]) -> dict:
    """One PanelState entry for an /api/state {panels:[…]} replace."""
    return {"id": panel_id, "run_id": run_id, "checkpoint": checkpoint, "messages": []}


@app.command("open")
def cmd_open(run: str = typer.Argument(..., help="run id or unique substring; optional @checkpoint")) -> None:
    """Select a run in single mode; the browser switches live."""
    run_arg, ckpt_arg = _split_run_arg(run)
    r = _resolve_run(run_arg)
    ckpt = _resolve_checkpoint(r, ckpt_arg)
    # Single mode = exactly one 'primary' panel (replaces any compare layout).
    state = _post("/api/state", {"panels": [_panel_obj("primary", r["id"], ckpt)]})
    print(f"opened {r['id']}" + (f"@{ckpt}" if ckpt else ""))
    _print_json(state)


class _StreamResult:
    """Outcome of a single _stream_chat invocation (used by compare threads).

    Threads must NOT call _die (typer.Exit) — that exception would just die in
    the worker and the main thread would still report success. Instead each
    thread records ok / error here and the main thread decides the exit code.
    """

    def __init__(self) -> None:
        self.ok: bool = False
        self.error: Optional[str] = None


_DIM = "\033[2m"
_RESET = "\033[0m"
_TTY = sys.stdout.isatty()


def _dim(s: str) -> str:
    """Wrap reasoning text dim on a real terminal; pass through when piped."""
    return f"{_DIM}{s}{_RESET}" if _TTY else s


def _stream_chat(
    body: dict,
    label: Optional[str] = None,
    lock: Optional[threading.Lock] = None,
    result: Optional["_StreamResult"] = None,
    stream_inline: bool = False,
) -> None:
    """POST /api/chat and print streamed samples. Thread-safe printing via lock.

    Two printing modes:
      - block (default): each per-sample block is assembled as ONE string and
        printed under a single lock acquisition, so concurrent compare panels
        never interleave mid-sample. Used by `compare`.
      - inline (`stream_inline=True`, single `chat` only): `delta` events are
        written to stdout token-by-token as they arrive (no lock — there is only
        one stream). The authoritative `message` event then finalizes the sample
        (newline + finish_reason) WITHOUT reprinting its content. n>1 samples
        carry no deltas, so they fall through to block printing as before.

    On failure: if `result` is supplied (compare threads), record the error
    there instead of calling _die; otherwise _die directly.
    """
    prefix = f"[{label}] " if label else ""

    def emit_block(*parts: str) -> None:
        block = "\n".join(prefix + p for p in parts)
        if lock is not None:
            with lock:
                print(block, flush=True)
        else:
            print(block, flush=True)

    def emit_inline(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def fail(msg: str) -> None:
        if result is not None:
            result.error = msg
            emit_block(f"[error] {msg}")
        else:
            _die(msg)

    # Inline-streaming bookkeeping (single chat only).
    streamed: set[int] = set()  # sample indices that received delta chunks
    hdr_printed: set[int] = set()  # indices we printed a "--- sample N ---" header for
    last_kind: dict[int, str] = {}  # idx -> last delta kind, to insert separators

    try:
        with httpx.Client(base_url=_base_url(), timeout=None) as c:
            with connect_sse(c, "POST", "/api/chat", json=body) as event_source:
                if event_source.response.status_code >= 400:
                    body_text = event_source.response.read().decode("utf-8", errors="replace")
                    fail(f"HTTP {event_source.response.status_code}: {body_text}")
                    return
                for ev in event_source.iter_sse():
                    if ev.event == "done":
                        emit_block("[done]")
                        break
                    if ev.event == "error":
                        err = ev.data
                        try:
                            err = json.loads(ev.data).get("error", ev.data)
                        except (json.JSONDecodeError, AttributeError):
                            pass
                        fail(f"{err}")
                        return
                    if ev.event == "delta":
                        # Token chunk (n==1). Only streamed inline for single chat;
                        # in block mode (compare) we ignore deltas and print the
                        # whole sample from the later `message` event.
                        if not stream_inline or not ev.data:
                            continue
                        d = json.loads(ev.data)
                        idx = d.get("sample_index", 0)
                        kind = d.get("kind", "content")
                        piece = d.get("delta", "")
                        if idx not in hdr_printed:
                            emit_inline(f"--- sample {idx} ---\n")
                            hdr_printed.add(idx)
                        if kind == "reasoning":
                            if last_kind.get(idx) != "reasoning":
                                emit_inline(_dim("[thinking] "))
                            emit_inline(_dim(piece))
                        else:
                            if last_kind.get(idx) == "reasoning":
                                emit_inline("\n")  # separate reasoning from content
                            emit_inline(piece)
                        last_kind[idx] = kind
                        streamed.add(idx)
                        continue
                    if ev.event != "message" or not ev.data:
                        continue
                    payload = json.loads(ev.data)
                    idx = payload.get("sample_index")
                    if payload.get("error"):
                        emit_block(f"--- sample {idx} ERROR ---", payload["error"])
                        continue
                    if stream_inline and idx in streamed:
                        # This sample already streamed inline — finalize it (end the
                        # line, append finish_reason) without reprinting the content.
                        emit_inline("\n")
                        fr = payload.get("finish_reason")
                        if fr:
                            emit_inline(f"[finish_reason={fr}]\n")
                        continue
                    parts = [f"--- sample {idx} ---"]
                    reasoning = payload.get("reasoning")
                    if reasoning:
                        parts.append(f"[thinking] {reasoning}")
                    parts.append(payload.get("content", ""))
                    fr = payload.get("finish_reason")
                    if fr:
                        parts.append(f"[finish_reason={fr}]")
                    emit_block(*parts)
    except httpx.TransportError as e:
        fail(
            f"could not reach tinkerscope server at {_base_url()}: {e}\n"
            "is the server running? check --base-url / $TINKERSCOPE_BASE_URL."
        )
        return
    if result is not None:
        result.ok = True


def _chat_body(
    run: dict,
    checkpoint: Optional[str],
    prompt: str,
    n: int,
    temperature: float,
    max_tokens: int,
    thinking: bool,
    system: Optional[str],
    panel: str,
) -> dict:
    """Build a /api/chat ChatRequest body."""
    body: dict = {
        "run_id": run["id"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "n_samples": n,
        "thinking": thinking,
        "panel": panel,
        "broadcast": True,
    }
    if checkpoint is not None:
        body["checkpoint"] = checkpoint
    if system is not None:
        body["system_prompt"] = system
    return body


@app.command("chat")
def cmd_chat(
    run: str = typer.Argument(..., help="run id or unique substring; optional @checkpoint"),
    prompt: str = typer.Argument(..., help="user message"),
    n: int = typer.Option(1, "--n", help="number of samples to draw"),
    temperature: float = typer.Option(1.0, "--temperature"),
    max_tokens: int = typer.Option(1024, "--max-tokens"),
    thinking: bool = typer.Option(False, "--thinking", help="enable the thinking renderer"),
    system: Optional[str] = typer.Option(None, "--system", help="system prompt"),
    checkpoint: Optional[str] = typer.Option(None, "--checkpoint", help="checkpoint name (overrides @ in the run arg)"),
) -> None:
    """Sample from a run's checkpoint; stream completions to stdout and the browser."""
    run_arg, ckpt_arg = _split_run_arg(run)
    r = _resolve_run(run_arg)
    ckpt = _resolve_checkpoint(r, checkpoint or ckpt_arg)
    _guard_sampleable(r)
    # Mirror selection to the bus so the browser shows what's being sampled (single
    # mode = one 'primary' panel).
    _post("/api/state", {"panels": [_panel_obj("primary", r["id"], ckpt)]})
    body = _chat_body(r, ckpt, prompt, n, temperature, max_tokens, thinking, system, "primary")
    print(f"chat {r['id']}" + (f"@{ckpt}" if ckpt else "") + f"  n={n} temp={temperature}")
    # Single chat: n==1 streams tokens inline; n>1 prints whole samples (no deltas).
    _stream_chat(body, stream_inline=True)


@app.command("compare")
def cmd_compare(
    run_a: str = typer.Argument(..., help="run A → primary pane: id/substring; optional @checkpoint"),
    run_b: str = typer.Argument(..., help="run B → compare pane: id/substring; optional @checkpoint"),
    prompt: str = typer.Argument(..., help="user message"),
    run: list[str] = typer.Option([], "--run", help="additional run(s) → 3rd, 4th, … panes (repeatable)"),
    n: int = typer.Option(1, "--n", help="number of samples per side"),
    temperature: float = typer.Option(1.0, "--temperature"),
    max_tokens: int = typer.Option(1024, "--max-tokens"),
    thinking: bool = typer.Option(False, "--thinking"),
    system: Optional[str] = typer.Option(None, "--system"),
) -> None:
    """Compare N runs on one prompt — A→primary, B→compare, --run extras→p-2,p-3,…
    all stream concurrently. `compare a b "prompt"` is the 2-run case."""
    catalog = _models()
    # Resolve every run (A, B, then each --run) to (run, checkpoint, panel_id).
    specs: list[tuple[dict, Optional[str], str]] = []
    for i, run_arg in enumerate([run_a, run_b, *run]):
        arg, ckpt_arg = _split_run_arg(run_arg)
        r = _resolve_run(arg, catalog)
        ckpt = _resolve_checkpoint(r, ckpt_arg)
        _guard_sampleable(r)
        specs.append((r, ckpt, _panel_id(i)))

    # One /api/state replace sets the whole panel layout at once.
    _post("/api/state", {"panels": [_panel_obj(pid, r["id"], ckpt) for (r, ckpt, pid) in specs]})

    print(f"compare  n={n} temp={temperature}")
    for (r, ckpt, pid) in specs:
        print(f"  {pid}: {r['id']}" + (f"@{ckpt}" if ckpt else ""))
    print()

    lock = threading.Lock()
    threads: list[threading.Thread] = []
    results: list[tuple[str, dict, _StreamResult]] = []
    for (r, ckpt, pid) in specs:
        body = _chat_body(r, ckpt, prompt, n, temperature, max_tokens, thinking, system, pid)
        res = _StreamResult()
        label = f"{pid} {r['id']}" + (f"@{ckpt}" if ckpt else "")
        t = threading.Thread(target=_stream_chat, args=(body, label, lock, res))
        results.append((pid, r, res))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    failures = [
        f"{pid} ({r['id']}): {res.error or 'unknown error'}"
        for (pid, r, res) in results
        if not res.ok
    ]
    if failures:
        _die("compare failed:\n  " + "\n  ".join(failures))


@app.command("state")
def cmd_state() -> None:
    """Print the current shared playground state."""
    _print_json(_get("/api/state"))


@app.command("refresh")
def cmd_refresh() -> None:
    """Rescan the filesystem + re-probe sampling capabilities."""
    _print_json(_post("/api/models/refresh"))


def main() -> None:
    """Entry point shim used when invoked as a module."""
    app()


if __name__ == "__main__":
    main()
