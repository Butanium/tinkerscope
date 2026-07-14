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

Doc surfaces — any command/flag/behavior change updates ALL of these, in the
same commit (they have drifted before):
  - README.md, "The CLI" section (command table + option notes)
  - .claude/skills/tinkerscope/SKILL.md — the tinkerscope skill other Claude
    sessions read to drive tinkpg. It lives IN THIS REPO;
    ~/.claude/skills/tinkerscope is a symlink to it, so edit the repo path
    (the Edit tool refuses to write through the symlink).
  - docs/API_CONTRACT.md, only if the HTTP surface itself changed.
"""
from __future__ import annotations

import json
import os
import re
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


# ---------- Conversation tree helpers (mirror web/src/lib/tree.ts) ----------
# The branch tree is OPAQUE to the server (it only round-trips the JSON). The
# browser owns the shape (web/src/lib/tree.ts) and so do we: a ConvTree is
# {nodes: {id: {id,role,content,parent,children[],...}}, rootChildren: [id],
# selected: {parentKey: childId}}. parentKey is a node id or the ROOT sentinel.
ROOT = "__root__"


def _selected_child(tree: dict, parent_key: str) -> Optional[str]:
    """Selected child id of `parent_key`, defaulting to the LAST (newest) child."""
    kids = tree.get("rootChildren", []) if parent_key == ROOT \
        else (tree.get("nodes", {}).get(parent_key, {}) or {}).get("children", [])
    if not kids:
        return None
    sel = (tree.get("selected") or {}).get(parent_key)
    return sel if (sel is not None and sel in kids) else kids[-1]


def _thread_path(tree: dict, root_id: str) -> list[dict]:
    """Root sibling `root_id` → leaf, following the selected child at each step.
    A "thread" = one root-level sibling (a branch-from-start first message) and
    its subtree; this is the thread-scoped analogue of the active path."""
    nodes = tree.get("nodes", {})
    node = nodes.get(root_id)
    if node is None:
        return []
    path, seen, pk = [node], {root_id}, root_id
    while True:
        cid = _selected_child(tree, pk)
        if cid is None or cid in seen:
            break
        node = nodes.get(cid)
        if node is None:
            break
        seen.add(cid)
        path.append(node)
        pk = cid
    return path


def _active_path(tree: dict) -> list[dict]:
    """Root → leaf following the selected child at each step (mirrors activePath)."""
    sel = _selected_child(tree, ROOT)
    return _thread_path(tree, sel) if sel else []


def _siblings(tree: dict, node: dict) -> list[str]:
    """Ids of `node`'s siblings (children of its parent, or the roots)."""
    parent = node.get("parent")
    if parent is None:
        return tree.get("rootChildren", [])
    return (tree.get("nodes", {}).get(parent, {}) or {}).get("children", [])


def _branch_point_count(tree: dict) -> int:
    """Total forks in the tree: nodes (incl. the virtual ROOT) with >1 child."""
    n = 1 if len(tree.get("rootChildren", [])) > 1 else 0
    for node in tree.get("nodes", {}).values():
        if len(node.get("children", [])) > 1:
            n += 1
    return n


def _active_forks(tree: dict) -> int:
    """How many nodes ON the active path sit at a fork (a sibling to cycle to)."""
    return sum(1 for nd in _active_path(tree) if len(_siblings(tree, nd)) > 1)


# ---------- Transcript digest formatting ----------

_ROLE_TAG = {"assistant": "asst", "user": "user", "system": "sys"}


def _oneline(s: str, width: int) -> str:
    """Collapse whitespace to one line and cap at `width` (keeps tables readable
    AND sidesteps the raw-control-char JSON breakage the old `state` dump had)."""
    s = " ".join((s or "").split())
    return s if len(s) <= width else s[:width] + "…"


def _short_run(rid: Optional[str]) -> str:
    """Last path component of a run id; keep the `base:` prefix legible."""
    rid = rid or "?"
    if rid.startswith("base:"):
        return "base:" + rid.split("/")[-1]
    return rid.split("/")[-1]


def _indent(s: str, prefix: str = "      ") -> str:
    """Prefix every line of `s` (CoT / full content), preserving its line breaks."""
    return "\n".join(prefix + ln for ln in (s or "").splitlines())


def _fmt_turn(role: str, content: str, reasoning: Optional[str], width: int, full: bool, mark: str = "") -> str:
    """Render one transcript turn.

    `full`  → COMPLETE content AND chain-of-thought, line breaks preserved. The CoT
              is load-bearing for behavioral reads, so it is NEVER dropped or capped.
    digest  → one-line content capped at `width`, plus a one-line `·think` CoT
              preview whenever the turn carried reasoning (so you can SEE a CoT
              exists and reach for `--full` to read it)."""
    tag = _ROLE_TAG.get(role or "", role or "?")
    content = content or ""
    reasoning = (reasoning or "").strip()
    if full:
        out: list[str] = []
        if reasoning:
            out.append(f"   [{tag}{mark}] ⟨thinking⟩")
            out.append(_indent(reasoning))
            out.append(f"   [{tag}{mark}] ⟨answer⟩")
            out.append(_indent(content))
        elif "\n" in content:
            out.append(f"   [{tag}{mark}]")
            out.append(_indent(content))
        else:
            out.append(f"   [{tag}{mark}] {content}")
        return "\n".join(out)
    line = f"   [{tag}{mark}] {_oneline(content, width)}"
    if reasoning:
        line += f"\n   [{tag}{mark} ·think] {_oneline(reasoning, width)}"
    return line


def _fmt_node(tree: dict, node: dict, width: int, full: bool = False) -> str:
    """One active-path turn, annotated `·k/N` when it sits at an N-way fork."""
    sibs = _siblings(tree, node)
    mark = ""
    if len(sibs) > 1:
        try:
            mark = f"·{sibs.index(node['id']) + 1}/{len(sibs)}"
        except ValueError:
            mark = f"·?/{len(sibs)}"
    return _fmt_turn(node.get("role", ""), node.get("content", ""), node.get("reasoning"), width, full, mark)


def _fmt_msg(msg: dict, width: int, full: bool = False) -> str:
    """One linear message (state echo has no tree, so no fork annotation)."""
    return _fmt_turn(msg.get("role", ""), msg.get("content", ""), msg.get("reasoning"), width, full)


_TAG_RE = re.compile(r"<tag>\s*([A-Za-z_]+)\s*</tag>", re.IGNORECASE)


def _tag_tally(answers: list[str]) -> tuple[dict[str, int], int, int]:
    """Count `<tag>X</tag>` verdicts across a sample fan-out → (counts, doubled, untagged).

    The FIRST tag in an answer is its vote. An answer with >1 tag is a doubled draft
    (a known nemotron generation glitch) — counted by its first tag but flagged so the
    reader doesn't treat it as a clean vote. Answers with no tag are `untagged` (the
    model refused the format / replied free-form)."""
    counts: dict[str, int] = {}
    doubled = untagged = 0
    for a in answers:
        tags = _TAG_RE.findall(a or "")
        if not tags:
            untagged += 1
            continue
        if len(tags) > 1:
            doubled += 1
        v = tags[0].upper()
        counts[v] = counts.get(v, 0) + 1
    return counts, doubled, untagged


def _digest(items: list, fmt, full: bool, width: int, head: int = 2, tail: int = 2) -> list[str]:
    """First `head` + last `tail` of `items` via `fmt`, eliding the middle.
    `full` shows everything. `fmt` is _fmt_node (tree) or _fmt_msg (linear)."""
    if full or len(items) <= head + tail:
        return [fmt(it, width, full) for it in items]
    return (
        [fmt(it, width, full) for it in items[:head]]
        + [f"   … {len(items) - head - tail} turns elided (--full to expand) …"]
        + [fmt(it, width, full) for it in items[-tail:]]
    )


def _render_tree(tree: dict, width: int) -> list[str]:
    """Indented DFS of the WHOLE tree; `*` marks the active (selected) branch."""
    lines: list[str] = []

    def walk(node_id: str, depth: int) -> None:
        node = tree.get("nodes", {}).get(node_id)
        if not node:
            return
        active = _selected_child(tree, node.get("parent") or ROOT) == node_id
        tag = _ROLE_TAG.get(node.get("role", ""), node.get("role", "?"))
        lines.append(f"{'  ' * depth}{'*' if active else ' '}[{tag}] {_oneline(node.get('content', ''), width)}")
        for ch in node.get("children", []):
            walk(ch, depth + 1)

    for rc in tree.get("rootChildren", []):
        walk(rc, 0)
    return lines


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
    thinking: "bool | str",  # False / True / "both" (n without + n with thinking)
    system: Optional[str],
    panel: str,
    prefill: Optional[str] = None,
) -> dict:
    """Build a /api/chat ChatRequest body.

    A non-empty `prefill` is sent as a trailing {role:'assistant'} message; the
    server treats that as a prefill the renderer appends verbatim, so the model
    EXTENDS it. Type raw `<think>`; DeepSeek/Kimi/Qwen3.5 auto-open one in thinking
    mode (a redundant `<think>` is dropped), Qwen3 opens nothing.
    """
    messages: list[dict] = [{"role": "user", "content": prompt}]
    if prefill:
        messages.append({"role": "assistant", "content": prefill})
    body: dict = {
        "run_id": run["id"],
        "messages": messages,
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
    thinking_both: bool = typer.Option(False, "--thinking-both", help="draw n samples WITHOUT thinking + n WITH (2n total; overrides --thinking)"),
    system: Optional[str] = typer.Option(None, "--system", help="system prompt"),
    checkpoint: Optional[str] = typer.Option(None, "--checkpoint", help="checkpoint name (overrides @ in the run arg)"),
    prefill: Optional[str] = typer.Option(None, "--prefill", help="assistant prefill the model extends; raw `<think>` ok"),
) -> None:
    """Sample from a run's checkpoint; stream completions to stdout and the browser."""
    run_arg, ckpt_arg = _split_run_arg(run)
    r = _resolve_run(run_arg)
    ckpt = _resolve_checkpoint(r, checkpoint or ckpt_arg)
    _guard_sampleable(r)
    # Mirror selection to the bus so the browser shows what's being sampled (single
    # mode = one 'primary' panel).
    _post("/api/state", {"panels": [_panel_obj("primary", r["id"], ckpt)]})
    think: "bool | str" = "both" if thinking_both else thinking
    body = _chat_body(r, ckpt, prompt, n, temperature, max_tokens, think, system, "primary", prefill)
    if prefill:
        print(f"prefill: {prefill!r}")
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
    thinking_both: bool = typer.Option(False, "--thinking-both", help="n samples WITHOUT thinking + n WITH, per run (overrides --thinking)"),
    system: Optional[str] = typer.Option(None, "--system"),
    prefill: Optional[str] = typer.Option(None, "--prefill", help="assistant prefill the models extend; raw `<think>` ok"),
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
    think: "bool | str" = "both" if thinking_both else thinking
    for (r, ckpt, pid) in specs:
        body = _chat_body(r, ckpt, prompt, n, temperature, max_tokens, think, system, pid, prefill)
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
def cmd_state(
    full: bool = typer.Option(False, "--full", help="show every message per panel, not just first/last-2"),
    width: int = typer.Option(160, "--width", help="per-message truncation width"),
    link: bool = typer.Option(True, "--link/--no-link", help="annotate each panel with the saved conversation its active path matches (`--no-link` skips the conversations fetch)"),
    json_out: bool = typer.Option(False, "--json", help="raw state JSON (untruncated escape hatch)"),
    include_folded: bool = typer.Option(
        False, "--include-folded", help="also show panels folded in the browser UI (skipped by default)"
    ),
) -> None:
    """Digest of what's on screen now: one block per panel, first/last-2 of each
    panel's ACTIVE path, annotated with the saved conversation it matches (so you
    can jump straight to its branches via `conv`). Panels folded in the browser
    UI are skipped (one-line stub) — --include-folded expands them. Branches
    themselves: see `conv`."""
    st = _get("/api/state")
    if json_out:
        print(json.dumps(st, indent=2, default=str, ensure_ascii=False))
        return
    print(
        f"live playground   running={'yes' if st.get('running') else 'no'}   "
        f"temp={st.get('temperature')} max_tokens={st.get('max_tokens')} "
        f"n={st.get('n_samples')} thinking={'both' if st.get('thinking') == 'both' else 'yes' if st.get('thinking') else 'no'}"
    )
    if st.get("system_prompt"):
        print(f"system: {_oneline(st['system_prompt'], 200)}")
    panels = st.get("panels", [])
    conv_id = st.get("conversation_id")
    # Fetch saved conversations only when we'll use them: to NAME the open-conv id the
    # browser pushed, or — when it didn't (older browser / CLI-only) — to match panels.
    convs = _conversations() if (link and (conv_id or any(p.get("messages") for p in panels))) else []
    # Fold info lives only in the saved conversation (the state bus has no
    # reduced_panels), so folded-panel skipping needs the browser-pushed
    # conversation_id + the (default) --link fetch; without either, all panels show.
    reduced: set[str] = set()
    if conv_id:
        open_conv = next((c for c in convs if c.get("id") == conv_id), None)
        if open_conv:
            print(f"open conversation: {open_conv.get('name')} ({conv_id[:8]})   → `tinkpg conv {conv_id[:8]}`")
            reduced = set(open_conv.get("reduced_panels") or [])
        elif link:
            print(f"open conversation: {conv_id[:8]} (unsaved draft / not in saved set)")
        else:
            print(f"open conversation: {conv_id[:8]}   (--no-link: name + folds not resolved)")
    print(f"{len(panels)} panel(s):\n")
    skipped: list[str] = []
    for p in panels:
        msgs = p.get("messages", [])
        bind = _short_run(p.get("run_id")) + (f"@{p['checkpoint']}" if p.get("checkpoint") else "")
        if p["id"] in reduced and not include_folded:
            skipped.append(p["id"])
            print(f"▸ {p['id']}  {bind}   (folded — --include-folded to expand)")
            print()
            continue
        # The exact open-conv id (above) covers every panel; only fall back to the
        # per-panel path-match heuristic when the browser pushed no conversation_id.
        tag = ""
        if convs and not conv_id:
            hits = _link_panel_to_conv(msgs, convs)
            if len(hits) == 1:
                tag = f"   ← conv: {hits[0][0]} ({hits[0][1][:8]})"
            elif len(hits) > 1:
                names = ", ".join(f"{n} ({i[:8]})" for (n, i, _) in hits[:3])
                tag = f"   ← conv: ambiguous ×{len(hits)}: {names} [newest first]"
        print(f"▸ {p['id']}  {bind}   ({len(msgs)} msgs){tag}")
        for line in _digest(msgs, _fmt_msg, full, width):
            print(line)
        print()
    if skipped:
        print(f"{len(skipped)} folded panel(s) skipped: {', '.join(skipped)}   (--include-folded to expand)")
    print("(branch trees: `tinkpg conv <id|name>`   ·   raw: `tinkpg state --json`)")


def _conversations() -> list[dict]:
    """Fetch all saved conversation trees for this scan-root set.

    `?bodies=1` because every CLI consumer (link-by-active-path, browse, resolve)
    reads the trees; the bare endpoint returns blob-less summaries (storage v2)."""
    return _get("/api/conversations?bodies=1")


def _link_panel_to_conv(panel_msgs: list[dict], convs: list[dict]) -> list[tuple[str, str, str]]:
    """Saved conversations whose active path (any panel) EXACTLY equals this
    panel's live messages → [(name, id, panel_id)], newest-updated first.

    The state bus carries no conversation_id (the open-conversation id lives only
    in the browser URL `?c=`), so we recover the link heuristically by exact
    active-path match. Exact-match means no false positives on *content*; the only
    ambiguity is when two saved conversations genuinely share an identical path
    (short prefixes) — surfaced honestly rather than guessed."""
    if not panel_msgs:
        return []
    target = [(m.get("role"), m.get("content")) for m in panel_msgs]
    hits: list[tuple[str, str, str, str]] = []  # (updated_at, name, id, panel_id)
    for c in convs:
        for pid, t in (c.get("trees") or {}).items():
            ap = [(n["role"], n["content"]) for n in _active_path(t) if n.get("role") != "system"]
            if ap == target:
                hits.append((c.get("updated_at") or "", c.get("name") or "?", c.get("id") or "", pid))
    hits.sort(key=lambda h: h[0], reverse=True)  # most-recently-updated first
    return [(name, cid, pid) for (_, name, cid, pid) in hits]


def _resolve_conv(sel: str, convs: Optional[list[dict]] = None) -> dict:
    """Resolve a conversation by exact id, else id-prefix, else unique
    case-insensitive name substring. Ambiguity / no-match errors out."""
    convs = convs if convs is not None else _conversations()
    for c in convs:
        if c.get("id") == sel:
            return c
    needle = sel.lower()
    matches = [
        c for c in convs
        if (c.get("id") or "").startswith(sel) or needle in (c.get("name") or "").lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        _die(f"no conversation matching {sel!r} (id-prefix or name substring)")
    listing = "\n".join(f"  - {m.get('id','')[:8]}  {m.get('name')}" for m in matches[:30])
    _die(f"ambiguous conversation {sel!r} — {len(matches)} candidates:\n{listing}")
    raise AssertionError  # unreachable


def _list_conversations(convs: list[dict]) -> None:
    rows = []
    for c in convs:
        trees = c.get("trees") or {}
        rows.append({
            "id": (c.get("id") or "")[:8],
            "updated": (c.get("updated_at") or "")[:16].replace("T", " "),
            "name": c.get("name"),
            "panels": len(trees),
            "nodes": sum(len(t.get("nodes", {})) for t in trees.values()),
            "branches": sum(_branch_point_count(t) for t in trees.values()),
            "active": "/".join(str(len(_active_path(t))) for t in trees.values()) or "-",
        })
    rows.sort(key=lambda r: r["updated"], reverse=True)
    _print_table(rows, ["id", "updated", "name", "panels", "nodes", "branches", "active"])
    print(f"\n{len(rows)} conversation(s)   (expand: `tinkpg conv <id|name>`)")


def _thread_index(tree: dict, width: int) -> list[str]:
    """Compact index of a panel's root THREADS (branch-from-start siblings):
    one line per thread with its first message + first-turn fan-out size, `*` =
    the active one. Empty when the panel has a single thread (nothing to index)."""
    roots = tree.get("rootChildren", [])
    if len(roots) < 2:
        return []
    nodes = tree.get("nodes", {})
    sel_root = _selected_child(tree, ROOT)
    out = [f"   threads: {len(roots)}   (* = active · `samples --thread k` for one fan-out)"]
    for k, rid in enumerate(roots, 1):
        nd = nodes.get(rid)
        if nd is None:
            continue
        star = "*" if rid == sel_root else " "
        fan = len(nd.get("children", []))
        uturns = sum(1 for n in _thread_path(tree, rid) if n.get("role") == "user")
        tail = f"({fan} sample{'' if fan == 1 else 's'}" + (f", {uturns} turns)" if uturns > 1 else ")") if fan else "(no samples yet)"
        out.append(f"   {star}{k}· {_oneline(nd.get('content', ''), max(20, width - 28))}   {tail}")
    return out


def _show_conversation(
    c: dict, panel: Optional[str], full: bool, show_tree: bool, width: int, include_folded: bool = False
) -> None:
    trees = c.get("trees") or {}
    layout = {p["id"]: p for p in (c.get("panels") or [])}
    reduced = set(c.get("reduced_panels") or [])
    upd = (c.get("updated_at") or "")[:19].replace("T", " ")
    print(f"conversation: {c.get('name')}  ({c.get('id')})   updated {upd}")
    if c.get("system_prompt"):
        print(f"system: {_oneline(c['system_prompt'], 200)}")
    total_nodes = sum(len(t.get("nodes", {})) for t in trees.values())
    total_bps = sum(_branch_point_count(t) for t in trees.values())
    print(f"{len(trees)} panel(s) · {total_nodes} nodes · {total_bps} branch points\n")
    shown = 0
    skipped: list[str] = []
    for pid, t in trees.items():
        if panel and pid != panel:
            continue
        shown += 1
        lay = layout.get(pid, {})
        bind = _short_run(lay.get("run_id")) + (f"@{lay['checkpoint']}" if lay.get("checkpoint") else "")
        # Folded (reduced) panels are skipped by default — an explicit --panel
        # always overrides the fold (you asked for it by name).
        if pid in reduced and not include_folded and not panel:
            skipped.append(pid)
            print(f"▸ {pid}  ← {bind}   (folded — --include-folded or --panel {pid} to expand)")
            continue
        ap = _active_path(t)
        nf = _active_forks(t)
        print(f"▸ {pid}  ← {bind}   (active: {len(ap)} msgs, {nf} fork{'' if nf == 1 else 's'} on path)")
        if show_tree:
            for line in _render_tree(t, width):
                print(line)
        else:
            for line in _thread_index(t, width):
                print(line)
            for line in _digest(ap, lambda nd, w, fl: _fmt_node(t, nd, w, fl), full, width):
                print(line)
        print()
    if panel and shown == 0:
        _die(f"conversation has no panel {panel!r}; panels: {', '.join(trees) or '(none)'}")
    if skipped:
        print(f"{len(skipped)} folded panel(s) skipped: {', '.join(skipped)}   (--include-folded to expand all, or --panel <id> for one)")


@app.command("conv")
def cmd_conv(
    selector: Optional[str] = typer.Argument(None, help="conversation id-prefix or name substring; omit to list all"),
    panel: Optional[str] = typer.Option(None, "--panel", help="restrict to one panel id (primary/compare/p-2/…); overrides folding"),
    full: bool = typer.Option(False, "--full", help="show the whole active path, not just first/last-2"),
    tree: bool = typer.Option(False, "--tree", help="show the full branch tree (all branches), `*` = active"),
    width: int = typer.Option(160, "--width", help="per-message truncation width"),
    include_folded: bool = typer.Option(
        False, "--include-folded", help="also expand panels folded in the browser UI (skipped by default)"
    ),
) -> None:
    """Browse saved (branchable) conversations. No selector → list them with
    branch metadata; a selector → expand its panels' active branch + forks, plus
    a `threads:` index when the panel has multiple root threads (branch-from-start
    first messages). Panels folded in the browser UI are skipped by default (shown
    as a one-line stub) — pass --include-folded to expand them too, or --panel to
    target one."""
    convs = _conversations()
    if selector is None:
        _list_conversations(convs)
        return
    _show_conversation(_resolve_conv(selector, convs), panel, full, tree, width, include_folded)


def _show_samples(
    c: dict, panel: Optional[str], turn: Optional[int], full: bool, width: int, thread: Optional[int] = None
) -> None:
    trees = c.get("trees") or {}
    if not trees:
        _die("conversation has no panels")
    reduced = set(c.get("reduced_panels") or [])
    if panel:
        pid = panel  # explicit --panel always overrides the fold
    else:
        candidates = [p for p in trees if p not in reduced] or list(trees)
        pid = "primary" if "primary" in candidates else candidates[0]
    t = trees.get(pid)
    if t is None:
        _die(f"no panel {panel!r}; panels: {', '.join(trees) or '(none)'}")
    roots = t.get("rootChildren", [])
    if thread is not None:
        if not (1 <= thread <= len(roots)):
            _die(f"--thread {thread} out of range (panel {pid} has {len(roots)} thread(s) — see `tinkpg conv`)")
        path = _thread_path(t, roots[thread - 1])
        thread_k = thread
    else:
        path = _active_path(t)
        sel_root = _selected_child(t, ROOT)
        thread_k = roots.index(sel_root) + 1 if sel_root in roots else 1
    user_idx = [i for i, n in enumerate(path) if n.get("role") == "user"]
    if not user_idx:
        _die(f"panel {pid} thread {thread_k} has no user turns on its selected path")
    if turn is not None:
        if not (1 <= turn <= len(user_idx)):
            _die(f"--turn {turn} out of range (thread {thread_k} has {len(user_idx)} user turns on its path)")
        ui = user_idx[turn - 1]
    else:
        ui = user_idx[-1]
    which = (turn if turn is not None else len(user_idx))
    unode = path[ui]
    nodes = t.get("nodes", {})
    samples = [nodes[k] for k in unode.get("children", []) if k in nodes]
    active_id = _selected_child(t, unode.get("id", ""))

    layout = {p["id"]: p for p in (c.get("panels") or [])}
    lay = layout.get(pid, {})
    bind = _short_run(lay.get("run_id")) + (f"@{lay['checkpoint']}" if lay.get("checkpoint") else "")
    print(f"conversation: {c.get('name')}  ({(c.get('id') or '')[:8]})")
    thread_part = f"thread {thread_k}/{len(roots)}   ·   " if len(roots) > 1 else ""
    print(f"panel {pid}  ← {bind}   ·   {thread_part}user turn {which}/{len(user_idx)}   ·   {len(samples)} sample(s)")
    if len(trees) > 1 and panel is None:
        unfolded = [p for p in trees if p not in reduced]
        plist = (", ".join(unfolded) or "none unfolded") + (f" (+{len(trees) - len(unfolded)} folded)" if reduced else "")
        print(f"(panels: {plist} — showing {pid}; --panel to switch)")
    print("\n▸ prompt:")
    print(_indent(unode.get("content", ""), "   "))

    counts, doubled, untagged = _tag_tally([s.get("content", "") for s in samples])
    if counts or untagged:
        parts = [f"{k} ×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
        if untagged:
            parts.append(f"untagged ×{untagged}")
        tail = f"   ({doubled} doubled-draft)" if doubled else ""
        print(f"\ntally: {' · '.join(parts)}{tail}")
    print()
    for i, s in enumerate(samples, 1):
        active = "*" if s.get("id") == active_id else " "
        print(f"{active}--- sample {i}/{len(samples)} ---")
        print(_fmt_turn(s.get("role", ""), s.get("content", ""), s.get("reasoning"), width, full))
        print()


@app.command("samples")
def cmd_samples(
    selector: Optional[str] = typer.Argument(None, help="conversation id-prefix or name substring; omit → the conversation open in the browser"),
    panel: Optional[str] = typer.Option(None, "--panel", help="panel id (primary/compare/p-2/…); default = first NON-FOLDED panel (primary if eligible). Explicit --panel overrides folding"),
    thread: Optional[int] = typer.Option(None, "--thread", help="1-indexed root thread (branch-from-start sibling) to walk; default = the active one. Thread numbers: the `threads:` index in `tinkpg conv <id>`"),
    turn: Optional[int] = typer.Option(None, "--turn", help="1-indexed user turn on the thread's path whose responses to show; default = the last one"),
    full: bool = typer.Option(False, "--full", help="each sample's COMPLETE answer + full CoT (default: answer + one-line CoT preview)"),
    width: int = typer.Option(240, "--width", help="per-sample truncation width in the default (non --full) view"),
) -> None:
    """Show every sibling response (the n-sample fan-out) at ONE fork, each with its
    CoT, plus a `<tag>` verdict tally — the 'what did the model say across all draws
    here' view that `state`/`conv` (active path only) can't give you. With no selector
    it targets the conversation the browser has open (via its pushed conversation_id);
    with no --panel, the first non-folded panel. --thread k aims it at a non-active
    root thread (numbers from `tinkpg conv <id>`'s thread index)."""
    convs = _conversations()
    if selector is not None:
        c = _resolve_conv(selector, convs)
    else:
        cid = _get("/api/state").get("conversation_id")
        if not cid:
            _die("no conversation open in the browser (state has no conversation_id). pass a conversation id/name — see `tinkpg conv`.")
        c = next((x for x in convs if x.get("id") == cid), None)
        if c is None:
            _die(f"open conversation {cid[:8]} isn't in the saved set yet (unsaved draft?). save it, or pass a saved id — see `tinkpg conv`.")
    _show_samples(c, panel, turn, full, width, thread)


@app.command("refresh")
def cmd_refresh() -> None:
    """Rescan the filesystem + re-probe sampling capabilities."""
    _print_json(_post("/api/models/refresh"))


def main() -> None:
    """Entry point shim used when invoked as a module."""
    app()


if __name__ == "__main__":
    main()
