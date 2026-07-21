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


def _arg_or_file(inline: Optional[str], file: Optional[str], what: str, flag: str) -> Optional[str]:
    """Resolve an inline string OR a file's contents (mutually exclusive) → the text,
    or None when both are absent. Used for `--file`/`--prefill-file`: probe templates
    live as files so they aren't retyped. The file is read verbatim (no trailing-
    newline strip — a template's exact bytes are the contract)."""
    if inline is not None and file is not None:
        _die(f"pass EITHER the {what} inline OR {flag}, not both")
    if file is not None:
        p = Path(file)
        if not p.is_file():
            _die(f"{what} file not found: {file}")
        return p.read_text()
    return inline


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


def _ancestry(tree: dict, node_id: str) -> list[dict]:
    """Root → `node_id` INCLUSIVE via the PARENT chain (mirrors tree.ts
    ancestryMessages) — works for ANY node regardless of the current selection, so
    `continue` can loom from a non-active branch. Returns the node dicts in order."""
    nodes = tree.get("nodes", {})
    chain: list[dict] = []
    cur, seen = nodes.get(node_id), set()
    while cur is not None and cur.get("id") not in seen:
        seen.add(cur.get("id"))
        chain.append(cur)
        parent = cur.get("parent")
        cur = nodes.get(parent) if parent else None
    chain.reverse()
    return chain


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


def _fmt_token_logprobs(entries: list[dict]) -> str:
    """One line per GENERATED token: index, the decoded text (repr'd so whitespace/
    newlines are visible), its logprob, and (when present) the top-5 alternatives
    from the same forward pass. Mirrors the `{t, tid, lp, top}` shape in
    docs/API_CONTRACT.md — `top` degrades to absent if the topk follow-up call failed."""
    lines = []
    for i, e in enumerate(entries):
        line = f"    [{i}] {e.get('t', '')!r}  lp={e.get('lp', 0.0):.4f}"
        top = e.get("top")
        if top:
            alts = ", ".join(f"{t!r}({lp:.3f})" for (t, _tid, lp) in top)
            line += f"   top: {alts}"
        lines.append(line)
    return "\n".join(lines)


def _stream_chat(
    body: dict,
    label: Optional[str] = None,
    lock: Optional[threading.Lock] = None,
    result: Optional["_StreamResult"] = None,
    stream_inline: bool = False,
    logprobs: bool = False,
    json_out: bool = False,
) -> None:
    """POST /api/chat and print streamed samples. Thread-safe printing via lock.

    Three printing modes:
      - block (default): each per-sample block is assembled as ONE string and
        printed under a single lock acquisition, so concurrent compare panels
        never interleave mid-sample. Used by `compare`.
      - inline (`stream_inline=True`, single `chat` only): `delta` events are
        written to stdout token-by-token as they arrive (no lock — there is only
        one stream). The authoritative `message` event then finalizes the sample
        (newline + finish_reason) WITHOUT reprinting its content. n>1 samples
        carry no deltas, so they fall through to block printing as before.
      - JSON (`json_out=True`, overrides the above): one JSON object PER LINE
        (JSONL) — the raw `message`/error payload plus a `panel` tag, no
        `[label]` text prefix (the panel id lives in the object instead) and no
        deltas (scripts want the finalized sample, not token chunks). A
        trailing `{"event":"done","panel":...}` line closes each panel's
        stream. For script/pipeline consumption — includes `token_logprobs`
        whenever the sample carries it, independent of `--logprobs` (that flag
        only controls the HUMAN-readable text rendering).

    On failure: if `result` is supplied (compare threads), record the error
    there instead of calling _die; otherwise _die directly.
    """
    prefix = f"[{label}] " if label else ""
    panel_id = body.get("panel")

    def emit_block(*parts: str) -> None:
        block = "\n".join(prefix + p for p in parts)
        if lock is not None:
            with lock:
                print(block, flush=True)
        else:
            print(block, flush=True)

    def emit_json(obj: dict) -> None:
        line = json.dumps({"panel": panel_id, **obj}, default=str, ensure_ascii=False)
        if lock is not None:
            with lock:
                print(line, flush=True)
        else:
            print(line, flush=True)

    def emit_inline(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def fail(msg: str) -> None:
        if json_out:
            emit_json({"event": "error", "error": msg})
        elif result is not None:
            emit_block(f"[error] {msg}")
        if result is not None:
            result.error = msg
        else:
            _die(msg)  # single (non-threaded) chat: JSON line (if any) is already out — now exit non-zero

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
                        if json_out:
                            emit_json({"event": "done"})
                        else:
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
                        # in block/JSON mode we ignore deltas and print the whole
                        # sample from the later `message` event.
                        if json_out or not stream_inline or not ev.data:
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
                    if json_out:
                        emit_json({"event": "sample", **payload})
                        continue
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
                    if logprobs:
                        tlp = payload.get("token_logprobs")
                        parts.append(
                            "[token_logprobs]\n" + _fmt_token_logprobs(tlp) if tlp
                            else "[token_logprobs: none captured — OpenRouter model, or the tinker call failed]"
                        )
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


def _bind_panel_model(body: dict, panel: dict) -> None:
    """Decode the browser's model-sel sentinel (mirrors web/src/lib/model-sel.ts)
    into the matching mutually-exclusive ChatRequest field, in place. A bare id is
    a discovered run (+ the panel's checkpoint)."""
    rid = panel.get("run_id") or ""
    if rid.startswith("openrouter:"):
        body["openrouter_model"] = rid[len("openrouter:"):]
    elif rid.startswith("base:"):
        body["base_model"] = rid[len("base:"):]
    elif rid.startswith("ckpt:"):
        body["sampler_path"] = rid[len("ckpt:"):]
    else:
        body["run_id"] = rid
        if panel.get("checkpoint"):
            body["checkpoint"] = panel["checkpoint"]


def _panel_body(
    panel: dict,
    messages: list[dict],
    n: int,
    temperature: float,
    max_tokens: int,
    thinking: "bool | str",
    system: Optional[str],
    prefill_scope: Optional[str] = None,
) -> dict:
    """ChatRequest for a live panel AS BOUND, with an EXPLICIT messages list — the
    shared core of `send` (fresh single-turn history) and `continue` (a full
    ancestry). Decodes the panel's model sentinel; a trailing assistant message in
    `messages` is the server's prefill convention (the model extends it)."""
    body: dict = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "n_samples": n,
        "thinking": thinking,
        "panel": panel["id"],
        "broadcast": True,
    }
    if prefill_scope is not None:
        body["prefill_scope"] = prefill_scope
    _bind_panel_model(body, panel)
    if system is not None:
        body["system_prompt"] = system
    return body


def _panel_chat_body(
    panel: dict,
    prompt: str,
    n: int,
    temperature: float,
    max_tokens: int,
    thinking: "bool | str",
    system: Optional[str],
    prefill: Optional[str],
) -> dict:
    """ChatRequest for a live panel AS BOUND — a fresh single-user-turn history
    (+ optional assistant prefill). Thin wrapper over `_panel_body`."""
    messages: list[dict] = [{"role": "user", "content": prompt}]
    if prefill:
        messages.append({"role": "assistant", "content": prefill})
    return _panel_body(panel, messages, n, temperature, max_tokens, thinking, system)


@app.command("send")
def cmd_send(
    prompt: Optional[str] = typer.Argument(None, help="user message — fired as a NEW thread at the current panels (or --file)"),
    n: int = typer.Option(1, "--n", help="samples per panel"),
    temperature: float = typer.Option(1.0, "--temperature"),
    max_tokens: int = typer.Option(1024, "--max-tokens"),
    thinking: bool = typer.Option(False, "--thinking"),
    thinking_both: bool = typer.Option(False, "--thinking-both", help="n samples WITHOUT thinking + n WITH, per panel (overrides --thinking)"),
    system: Optional[str] = typer.Option(None, "--system"),
    prefill: Optional[str] = typer.Option(None, "--prefill", help="assistant prefill the models extend; raw `<think>` ok"),
    file: Optional[str] = typer.Option(None, "--file", help="read the user message from a file (a probe template — mutually exclusive with the positional prompt)"),
    prefill_file: Optional[str] = typer.Option(None, "--prefill-file", help="read the assistant prefill from a file (mutually exclusive with --prefill)"),
    panel: list[str] = typer.Option([], "--panel", help="target only these panel ids (repeatable); overrides folding"),
    include_folded: bool = typer.Option(False, "--include-folded", help="also fire at browser-folded panels"),
    force: bool = typer.Option(False, "--force", help="fire even while a generation is in flight"),
    show_logprobs: bool = typer.Option(False, "--logprobs", help="print each sample's per-token logprob + top-5 alternatives (native tinker sampling only; none for OpenRouter)"),
    json_out: bool = typer.Option(False, "--json", help="one JSON object per line (JSONL) instead of human text — for scripts; always includes token_logprobs when present, independent of --logprobs"),
) -> None:
    """Fire the prompt as a NEW THREAD at the CURRENT panels of the open workspace
    — the CLI twin of the browser's ⑂ branch-from-start. Unlike `chat`/`compare`
    this never touches the panel layout: it reads the live panels (skipping
    browser-folded ones), fires one chat per panel with a FRESH history, and the
    browser folds each reply in as a sibling first message. Existing threads are
    untouched; aim it with --panel (repeatable). The message / prefill can come from
    a file (--file / --prefill-file) so probe templates aren't retyped."""
    prompt = _arg_or_file(prompt, file, "message", "--file")
    prefill = _arg_or_file(prefill, prefill_file, "prefill", "--prefill-file")
    if prompt is None:
        _die("no message — pass it inline or via --file")
    st = _get("/api/state")
    if st.get("running") and not force:
        _die("a generation is in flight (running=yes) — wait for it, or pass --force")
    panels = st.get("panels", [])
    if not panels:
        _die("no panels on screen — `tinkpg open <run>` or add panels in the browser first")
    by_id = {p["id"]: p for p in panels}
    folded: set[str] = set()
    conv_id = st.get("conversation_id")
    if conv_id and not include_folded and not panel:
        c = next((x for x in _conversations() if x.get("id") == conv_id), None)
        folded = set((c or {}).get("reduced_panels") or [])
    if panel:
        missing = [pid for pid in panel if pid not in by_id]
        if missing:
            _die(f"no panel(s) {', '.join(missing)}; on screen: {', '.join(by_id)}")
        targets = [by_id[pid] for pid in panel]
    else:
        targets = [p for p in panels if p["id"] not in folded]
    unbound = [p["id"] for p in targets if not p.get("run_id")]
    targets = [p for p in targets if p.get("run_id")]
    if not targets:
        _die("no target panel has a model bound — pick models in the browser or `tinkpg open <run>`")
    think: "bool | str" = "both" if thinking_both else thinking
    plan_out = sys.stderr if json_out else sys.stdout  # JSON mode: keep stdout pure JSONL

    print(f"send (new thread)  n={n} temp={temperature}  →  {len(targets)} panel(s)", file=plan_out)
    for p in targets:
        print(f"  {p['id']}: {_short_run(p.get('run_id'))}" + (f"@{p['checkpoint']}" if p.get("checkpoint") else ""), file=plan_out)
    skipped_bits = []
    if folded:
        skipped_bits.append(f"{len(folded & set(by_id))} folded ({', '.join(sorted(folded & set(by_id)))})")
    if unbound:
        skipped_bits.append(f"unbound: {', '.join(unbound)}")
    if skipped_bits:
        print(f"  skipped: {'; '.join(skipped_bits)}", file=plan_out)
    print(file=plan_out)

    lock = threading.Lock()
    threads: list[threading.Thread] = []
    results: list[tuple[str, dict, _StreamResult]] = []
    for p in targets:
        body = _panel_chat_body(p, prompt, n, temperature, max_tokens, think, system, prefill)
        res = _StreamResult()
        label = f"{p['id']} {_short_run(p.get('run_id'))}"
        t = threading.Thread(target=_stream_chat, args=(body, label, lock, res, False, show_logprobs, json_out))
        results.append((p["id"], p, res))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    failures = [
        f"{pid} ({_short_run(p.get('run_id'))}): {res.error or 'unknown error'}"
        for (pid, p, res) in results
        if not res.ok
    ]
    if failures:
        _die("send failed:\n  " + "\n  ".join(failures))


def _continue_target(tree: dict, thread: Optional[int], turn: Optional[int], node: Optional[str]) -> dict:
    """Resolve the node whose ancestry `continue` loops from, within ONE panel's tree.
    `--node` pinpoints it (id or unique prefix); else walk a root thread's SELECTED
    path — `--thread K` (default: the active root) to its leaf, narrowed to turn N's
    answer by `--turn N`. Errors (never guesses) on an empty panel / bad range /
    ambiguous node."""
    nodes = tree.get("nodes", {})
    if node is not None:
        hits = [nd for nid, nd in nodes.items() if nid == node or nid.startswith(node)]
        exact = [nd for nd in hits if nd.get("id") == node]
        if exact:
            return exact[0]
        if not hits:
            _die(f"no node matching {node!r} in this panel's tree")
        if len(hits) > 1:
            _die(f"ambiguous node {node!r} — {len(hits)} matches: {', '.join(nd.get('id','') for nd in hits[:6])}")
        return hits[0]
    roots = tree.get("rootChildren", [])
    if not roots:
        _die("panel has no threads yet — nothing to continue (use `tinkpg send` to start one)")
    if thread is not None:
        if not (1 <= thread <= len(roots)):
            _die(f"--thread {thread} out of range (panel has {len(roots)} thread(s) — see `tinkpg conv`)")
        root = roots[thread - 1]
    else:
        root = _selected_child(tree, ROOT)
    path = _thread_path(tree, root)
    if not path:
        _die("target thread is empty")
    if turn is None:
        return path[-1]
    user_idx = [i for i, nd in enumerate(path) if nd.get("role") == "user"]
    if not (1 <= turn <= len(user_idx)):
        _die(f"--turn {turn} out of range (thread has {len(user_idx)} user turn(s) on its selected path)")
    nxt = user_idx[turn] if turn < len(user_idx) else len(path)
    return path[nxt - 1]  # turn N's selected answer (or its user node if unanswered)


def _continue_messages(ancestry: list[dict], prompt: Optional[str], prefill: Optional[str]) -> list[dict]:
    """Assemble the /api/chat messages from an ancestry (root→target, role/content)
    plus optional appends, and REFUSE invalid sequences up front (the server would
    reject them anyway, less legibly). The ancestry's LAST role decides:
      - ends on an ASSISTANT turn → a user message is required (turn-level loom: the
        follow-up/probe); an optional --prefill then seeds the fresh answer.
      - ends on a USER turn → NO message (that would be two user turns); we re-sample
        that turn, optionally seeded by --prefill (a thinking opener / truncated-own-
        CoT continuation).
    Content is answer-only (CoT excluded) so it matches the tree nodes verbatim — the
    browser's echo-reconcile then EXTENDS the matched branch instead of forking."""
    pairs = [{"role": m["role"], "content": m.get("content", "")} for m in ancestry if m.get("role") != "system"]
    if not pairs:
        _die("empty ancestry — nothing to continue from")
    last = pairs[-1]["role"]
    if last == "assistant":
        if prompt is None:
            _die("target ends on an ASSISTANT turn — pass a user message (inline / --file) to add a "
                 "turn, or target a user turn (--node/--turn) with --prefill to loom its answer")
        pairs.append({"role": "user", "content": prompt})
    elif last == "user":
        if prompt is not None:
            _die("target ends on a USER turn — a message here would be two user turns in a row. Drop it "
                 "to re-sample this turn (optionally with --prefill), or target an assistant turn to add one")
    else:
        _die(f"target ends on a {last!r} turn — nothing to continue")
    if prefill is not None:
        pairs.append({"role": "assistant", "content": prefill})
    return pairs


@app.command("continue")
def cmd_continue(
    prompt: Optional[str] = typer.Argument(None, help="user message to append AFTER the target (turn-level loom / the probe). Omit to re-sample a user-turn target with only --prefill."),
    n: int = typer.Option(1, "--n", help="samples per panel"),
    temperature: float = typer.Option(1.0, "--temperature"),
    max_tokens: int = typer.Option(1024, "--max-tokens"),
    thinking: bool = typer.Option(False, "--thinking"),
    thinking_both: bool = typer.Option(False, "--thinking-both", help="n samples WITHOUT thinking + n WITH, per panel (overrides --thinking)"),
    system: Optional[str] = typer.Option(None, "--system"),
    file: Optional[str] = typer.Option(None, "--file", help="read the user message from a file (mutually exclusive with the positional prompt)"),
    prefill: Optional[str] = typer.Option(None, "--prefill", help="assistant prefill the model extends — a thinking opener ('Hmm,') or its own truncated CoT; raw `<think>` ok"),
    prefill_file: Optional[str] = typer.Option(None, "--prefill-file", help="read the prefill from a file (mutually exclusive with --prefill) — e.g. the model's own truncated CoT"),
    prefill_scope: Optional[str] = typer.Option(None, "--prefill-scope", help="all|think|non_think — which half(s) a thinking-both prefill applies to (default all)"),
    panel: list[str] = typer.Option([], "--panel", help="target only these panel ids (repeatable); default = all unfolded panels"),
    thread: Optional[int] = typer.Option(None, "--thread", help="1-indexed root thread to continue (per panel); default = the panel's active thread"),
    turn: Optional[int] = typer.Option(None, "--turn", help="1-indexed user turn on the thread's path to loom from; default = the leaf"),
    node: Optional[str] = typer.Option(None, "--node", help="target node id/prefix (from `tinkpg grep`); pinpoints the loom point in ONE panel's tree"),
    conv: Optional[str] = typer.Option(None, "--conv", help="workspace for --thread/--turn/--node targeting (id-prefix/name); default = the one open in the browser"),
    ancestry_file: Optional[str] = typer.Option(
        None, "--ancestry-file",
        help="loom from an EXPLICIT full transcript instead of a tree/panel: a JSON list of "
             "{role, content} dicts (role: user|assistant|system). The SAME transcript is used "
             "for every target panel — this is how you graft a real, verbatim conversation "
             "generated by one model into another model's context (sanctioned: FULL transcripts "
             "only, never an authored/partial answer). Mutually exclusive with --thread/--turn/--node/--conv.",
    ),
    include_folded: bool = typer.Option(False, "--include-folded", help="also fire at browser-folded panels"),
    force: bool = typer.Option(False, "--force", help="fire even while a generation is in flight"),
    show_logprobs: bool = typer.Option(False, "--logprobs", help="print each sample's per-token logprob + top-5 alternatives (native tinker sampling only; none for OpenRouter)"),
    json_out: bool = typer.Option(False, "--json", help="one JSON object per line (JSONL) instead of human text — for scripts; always includes token_logprobs when present, independent of --logprobs"),
) -> None:
    """LOOM from an existing branch: rebuild the message history up to a target node
    and sample a continuation, WITHOUT touching the panel layout (the multi-turn twin
    of `send`). Default target = each panel's ACTIVE leaf (read from the live state,
    same source `send` uses) — so `tinkpg continue "<follow-up>"` adds a turn to the
    current threads across all panels; the browser's echo-reconcile extends the
    matched branch. Aim it elsewhere with --thread/--turn/--node (these read the SAVED
    workspace tree, so they reach non-active branches), or --ancestry-file to loom from
    an EXTERNAL full transcript (a raw-log sample that never made a tree — the CLI only
    folds one representative per n>1 fire — or another model's real conversation grafted
    in). Provenance rule this enforces: the ancestry is always a model's OWN, COMPLETE,
    previously-generated content — in-tree, in a log, or another model's transcript — a
    --prefill only ever seeds a tiny thinking opener or a truncated OWN CoT continuation;
    never a fabricated or partial turn."""
    prompt = _arg_or_file(prompt, file, "message", "--file")
    prefill = _arg_or_file(prefill, prefill_file, "prefill", "--prefill-file")
    tree_mode = thread is not None or turn is not None or node is not None
    if ancestry_file is not None and (tree_mode or conv is not None):
        _die("--ancestry-file replaces tree targeting entirely — don't combine it with --thread/--turn/--node/--conv")
    fixed_ancestry: Optional[list[dict]] = None
    if ancestry_file is not None:
        raw = Path(ancestry_file)
        if not raw.is_file():
            _die(f"ancestry file not found: {ancestry_file}")
        try:
            fixed_ancestry = json.loads(raw.read_text())
        except json.JSONDecodeError as e:
            _die(f"--ancestry-file must be a JSON list of {{role, content}} dicts: {e}")
        if not isinstance(fixed_ancestry, list) or not fixed_ancestry:
            _die("--ancestry-file must be a non-empty JSON list of {role, content} dicts")
        for m in fixed_ancestry:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant", "system") or not isinstance(m.get("content"), str):
                _die(f"bad ancestry entry (need role in user/assistant/system + string content): {m!r}")
    st = _get("/api/state")
    if st.get("running") and not force:
        _die("a generation is in flight (running=yes) — wait for it, or pass --force")
    panels = st.get("panels", [])
    if not panels:
        _die("no panels on screen — open a workspace in the browser first")
    by_id = {p["id"]: p for p in panels}
    conv_id = st.get("conversation_id")

    # Fold info + (tree-mode) the saved trees come from the open/〈--conv〉 workspace.
    folded: set[str] = set()
    trees: dict = {}
    if tree_mode or (conv_id and not include_folded and not panel):
        if conv is not None:
            c = _resolve_conv(conv)
        elif conv_id:
            c = next((x for x in _conversations() if x.get("id") == conv_id), None)
        else:
            c = None
        if c is None and tree_mode:
            _die("no workspace to target — open one in the browser or pass --conv (needed for --thread/--turn/--node)")
        if c is not None:
            folded = set(c.get("reduced_panels") or [])
            trees = c.get("trees") or {}
    if include_folded or panel:
        folded = set()

    # --node lives in exactly ONE panel's tree; restrict targeting to that panel.
    if node is not None and not panel:
        owners = [pid for pid, t in trees.items() if any(nid == node or nid.startswith(node) for nid in (t.get("nodes") or {}))]
        if len(owners) == 1:
            panel = owners
        elif not owners:
            _die(f"no node matching {node!r} in any panel's tree")
        else:
            _die(f"node {node!r} matches trees of panels {', '.join(owners)} — disambiguate with --panel")

    if panel:
        missing = [pid for pid in panel if pid not in by_id]
        if missing:
            _die(f"no panel(s) {', '.join(missing)}; on screen: {', '.join(by_id)}")
        targets = [by_id[pid] for pid in panel]
    else:
        targets = [p for p in panels if p["id"] not in folded]
    unbound = [p["id"] for p in targets if not p.get("run_id")]
    targets = [p for p in targets if p.get("run_id")]
    if not targets:
        _die("no target panel has a model bound")
    think: "bool | str" = "both" if thinking_both else thinking

    # Build (panel, messages) per target — refusing bad sequences BEFORE firing any.
    plans: list[tuple[dict, list[dict]]] = []
    for p in targets:
        if fixed_ancestry is not None:
            ancestry = fixed_ancestry
        elif tree_mode:
            t = trees.get(p["id"])
            if t is None:
                _die(f"panel {p['id']} has no saved tree in the workspace — can't --thread/--turn/--node it")
            target = _continue_target(t, thread, turn, node)
            ancestry = _ancestry(t, target["id"])
        else:
            ancestry = p.get("messages") or []
            if not ancestry:
                _die(f"panel {p['id']} has no active thread — use `tinkpg send` to start one, or --thread/--node")
        plans.append((p, _continue_messages(ancestry, prompt, prefill)))

    plan_out = sys.stderr if json_out else sys.stdout  # JSON mode: keep stdout pure JSONL
    print(f"continue (loom)  n={n} temp={temperature}  →  {len(targets)} panel(s)", file=plan_out)
    for (p, msgs) in plans:
        base = len(msgs) - (1 if prompt is not None else 0) - (1 if prefill is not None else 0)
        add = []
        if prompt is not None:
            add.append("+user")
        if prefill is not None:
            add.append(f"+prefill({_oneline(prefill, 24)})")
        if fixed_ancestry is not None:
            tgt = f"ancestry-file {ancestry_file}"
        else:
            tgt = f"node {node}" if node else (f"thread {thread}" if thread else "active") + (f" turn {turn}" if turn else "")
        print(f"  {p['id']}: {_short_run(p.get('run_id'))}  [{tgt}]  {base} ancestry turn(s) {' '.join(add)}".rstrip(), file=plan_out)
    if unbound:
        print(f"  skipped (unbound): {', '.join(unbound)}", file=plan_out)
    print(file=plan_out)

    lock = threading.Lock()
    threads: list[threading.Thread] = []
    results: list[tuple[str, dict, _StreamResult]] = []
    for (p, msgs) in plans:
        body = _panel_body(p, msgs, n, temperature, max_tokens, think, system, prefill_scope)
        res = _StreamResult()
        label = f"{p['id']} {_short_run(p.get('run_id'))}"
        t = threading.Thread(target=_stream_chat, args=(body, label, lock, res, False, show_logprobs, json_out))
        results.append((p["id"], p, res))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    failures = [
        f"{pid} ({_short_run(p.get('run_id'))}): {res.error or 'unknown error'}"
        for (pid, p, res) in results
        if not res.ok
    ]
    if failures:
        _die("continue failed:\n  " + "\n  ".join(failures))


@app.command("state")
def cmd_state(
    full: bool = typer.Option(False, "--full", help="show every message per panel, not just first/last-2"),
    width: int = typer.Option(160, "--width", help="per-message truncation width"),
    link: bool = typer.Option(True, "--link/--no-link", help="annotate each panel with the saved workspace its active path matches (`--no-link` skips the workspaces fetch)"),
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
            print(f"open workspace: {open_conv.get('name')} ({conv_id[:8]})   → `tinkpg conv {conv_id[:8]}`")
            reduced = set(open_conv.get("reduced_panels") or [])
        elif link:
            print(f"open workspace: {conv_id[:8]} (unsaved draft / not in saved set)")
        else:
            print(f"open workspace: {conv_id[:8]}   (--no-link: name + folds not resolved)")
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
        _die(f"no workspace matching {sel!r} (id-prefix or name substring)")
    listing = "\n".join(f"  - {m.get('id','')[:8]}  {m.get('name')}" for m in matches[:30])
    _die(f"ambiguous workspace {sel!r} — {len(matches)} candidates:\n{listing}")
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
    print(f"\n{len(rows)} workspace(s)   (expand: `tinkpg conv <id|name>`)")


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
    print(f"workspace: {c.get('name')}  ({c.get('id')})   updated {upd}")
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
        _die(f"workspace has no panel {panel!r}; panels: {', '.join(trees) or '(none)'}")
    if skipped:
        print(f"{len(skipped)} folded panel(s) skipped: {', '.join(skipped)}   (--include-folded to expand all, or --panel <id> for one)")


@app.command("conv")
def cmd_conv(
    selector: Optional[str] = typer.Argument(None, help="workspace id-prefix or name substring; omit to list all"),
    panel: Optional[str] = typer.Option(None, "--panel", help="restrict to one panel id (primary/compare/p-2/…); overrides folding"),
    full: bool = typer.Option(False, "--full", help="show the whole active path, not just first/last-2"),
    tree: bool = typer.Option(False, "--tree", help="show the full branch tree (all branches), `*` = active"),
    width: int = typer.Option(160, "--width", help="per-message truncation width"),
    include_folded: bool = typer.Option(
        False, "--include-folded", help="also expand panels folded in the browser UI (skipped by default)"
    ),
) -> None:
    """Browse saved WORKSPACES (multi-panel, branchable; `ws` is an alias). No
    selector → list them with branch metadata; a selector → expand its panels'
    active branch + forks, plus a `threads:` index when the panel has multiple
    root threads (branch-from-start first messages). Panels folded in the browser
    UI are skipped by default (shown as a one-line stub) — pass --include-folded
    to expand them too, or --panel to target one."""
    convs = _conversations()
    if selector is None:
        _list_conversations(convs)
        return
    _show_conversation(_resolve_conv(selector, convs), panel, full, tree, width, include_folded)


# Vocabulary alias: the saved container is a WORKSPACE (the wire/storage keep the
# legacy 'conversations' naming — see docs/API_CONTRACT.md). Hidden to keep --help tidy.
app.command("ws", hidden=True)(cmd_conv)


def _show_samples(
    c: dict,
    panel: Optional[str],
    turn: Optional[int],
    full: bool,
    width: int,
    thread: Optional[int] = None,
    node: Optional[str] = None,
    sample_k: Optional[int] = None,
    slice_rng: Optional[tuple[int, int]] = None,
    json_out: bool = False,
) -> None:
    trees = c.get("trees") or {}
    if not trees:
        _die("workspace has no panels")
    reduced = set(c.get("reduced_panels") or [])
    if node is not None:
        # --node pinpoints a fork ANYWHERE in a tree (grep prints the ids) —
        # including forks on non-selected branches that --thread/--turn (which
        # walk selected paths) can never reach.
        if thread is not None or turn is not None:
            _die("--node is mutually exclusive with --thread/--turn (it pinpoints the fork directly)")
        search = [panel] if panel else list(trees)
        found: list[tuple[str, dict]] = []
        for p in search:
            t0 = trees.get(p)
            if t0 is None:
                _die(f"no panel {panel!r}; panels: {', '.join(trees) or '(none)'}")
            for nid_, nd_ in (t0.get("nodes") or {}).items():
                if nid_ == node or nid_.startswith(node):
                    found.append((p, nd_))
        exact = [(p, nd_) for p, nd_ in found if nd_.get("id") == node]
        if exact:
            found = exact[:1]
        if not found:
            _die(f"no node matching {node!r}" + (f" in panel {panel}" if panel else " in any panel"))
        if len(found) > 1:
            listing = ", ".join(f"{p}:{nd_.get('id')}" for p, nd_ in found[:6])
            _die(f"ambiguous node {node!r} — {len(found)} matches: {listing}")
        pid, nd = found[0]
        t = trees[pid]
        if nd.get("role") == "assistant":
            # An assistant node names one SAMPLE — show the fan-out it belongs to.
            unode = (t.get("nodes") or {}).get(nd.get("parent") or "")
            if not unode:
                _die(f"node {nd.get('id')} is a root-level assistant — its siblings are whole threads; see `tinkpg conv --tree`")
        else:
            unode = nd
        roots = t.get("rootChildren", [])
        thread_k = _thread_of(t, unode.get("id", ""))
        pos_part = f"node {unode.get('id')}"
    else:
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
        pos_part = f"user turn {which}/{len(user_idx)}"
    nodes = t.get("nodes", {})
    samples = [nodes[k] for k in unode.get("children", []) if k in nodes]
    active_id = _selected_child(t, unode.get("id", ""))

    layout = {p["id"]: p for p in (c.get("panels") or [])}
    lay = layout.get(pid, {})
    bind = _short_run(lay.get("run_id")) + (f"@{lay['checkpoint']}" if lay.get("checkpoint") else "")

    if json_out:
        shown = [(i, s) for i, s in enumerate(samples, 1) if sample_k is None or i == sample_k]
        if sample_k is not None and not shown:
            _die(f"--sample {sample_k} out of range (this fork has {len(samples)} sample(s))")
        counts, doubled, untagged = _tag_tally([s.get("content", "") for s in samples])
        print(json.dumps({
            "workspace_id": c.get("id"), "workspace_name": c.get("name"),
            "panel": pid, "run_id": lay.get("run_id"), "checkpoint": lay.get("checkpoint"),
            "thread": thread_k, "thread_count": len(roots), "position": pos_part,
            "prompt": unode.get("content", ""),
            "tally": {"counts": counts, "doubled_draft": doubled, "untagged": untagged},
            "samples": [
                {"index": i, "id": s.get("id"), "role": s.get("role"), "content": s.get("content", ""),
                 "reasoning": s.get("reasoning"), "active": s.get("id") == active_id}
                for i, s in shown
            ],
        }, default=str, ensure_ascii=False))
        return

    print(f"workspace: {c.get('name')}  ({(c.get('id') or '')[:8]})")
    thread_part = f"thread {thread_k or '?'}/{len(roots)}   ·   " if len(roots) > 1 else ""
    print(f"panel {pid}  ← {bind}   ·   {thread_part}{pos_part}   ·   {len(samples)} sample(s)")
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
    shown = [(i, s) for i, s in enumerate(samples, 1) if sample_k is None or i == sample_k]
    if sample_k is not None and not shown:
        _die(f"--sample {sample_k} out of range (this fork has {len(samples)} sample(s))")
    for i, s in shown:
        active = "*" if s.get("id") == active_id else " "
        print(f"{active}--- sample {i}/{len(samples)} ---")
        if slice_rng is not None:
            start, ln = slice_rng
            if full and s.get("reasoning"):
                print(f"   [think]  ({len(s['reasoning'])} chars)")
                print(_slice_text(s["reasoning"], start, ln))
            role = s.get("role") or "?"
            print(f"   [{'asst' if role == 'assistant' else role[:4]}]  ({len(s.get('content', ''))} chars)")
            print(_slice_text(s.get("content", ""), start, ln))
        else:
            print(_fmt_turn(s.get("role", ""), s.get("content", ""), s.get("reasoning"), width, full))
        print()


@app.command("samples")
def cmd_samples(
    selector: Optional[str] = typer.Argument(None, help="workspace id-prefix or name substring; omit → the workspace open in the browser"),
    panel: Optional[str] = typer.Option(None, "--panel", help="panel id (primary/compare/p-2/…); default = first NON-FOLDED panel (primary if eligible). Explicit --panel overrides folding"),
    thread: Optional[int] = typer.Option(None, "--thread", help="1-indexed root thread (branch-from-start sibling) to walk; default = the active one. Thread numbers: the `threads:` index in `tinkpg conv <id>`"),
    turn: Optional[int] = typer.Option(None, "--turn", help="1-indexed user turn on the thread's path whose responses to show; default = the last one"),
    node: Optional[str] = typer.Option(None, "--node", help="node id (or unique prefix) from `tinkpg grep` — pinpoints the fork directly, reaching NON-selected branches --thread/--turn can't. An assistant id shows the fan-out it belongs to"),
    full: bool = typer.Option(False, "--full", help="each sample's COMPLETE answer + full CoT (default: answer + one-line CoT preview)"),
    width: int = typer.Option(240, "--width", help="per-sample truncation width in the default (non --full) view"),
    sample: Optional[int] = typer.Option(None, "--sample", help="show ONLY sibling K (1-indexed) — read one sample at a time"),
    slice_spec: Optional[str] = typer.Option(None, "--slice", help="START[:LEN] character window of each shown sample (default LEN 2000) — read long samples in pieces instead of truncating; with --full the same window applies to the CoT"),
    json_out: bool = typer.Option(False, "--json", help="the fork as one JSON object (workspace/panel/thread/prompt/tally/samples) instead of human text — for scripts (--slice is ignored; content is never truncated)"),
) -> None:
    """Show every sibling response (the n-sample fan-out) at ONE fork, each with its
    CoT, plus a `<tag>` verdict tally — the 'what did the model say across all draws
    here' view that `state`/`conv` (active path only) can't give you. With no selector
    it targets the conversation the browser has open (via its pushed conversation_id);
    with no --panel, the first non-folded panel. --thread k aims it at a non-active
    root thread (numbers from `tinkpg conv <id>`'s thread index); --node <id> (ids
    from `tinkpg grep`) aims it at ANY fork, even on non-selected branches. Reading
    ergonomics: --sample K isolates one sibling, --slice START[:LEN] pages through it,
    --json for scripts (untruncated content, no need to regex human-formatted text)."""
    convs = _conversations()
    if selector is not None:
        c = _resolve_conv(selector, convs)
    else:
        cid = _get("/api/state").get("conversation_id")
        if not cid:
            _die("no workspace open in the browser (state has no conversation_id). pass a workspace id/name — see `tinkpg conv`.")
        c = next((x for x in convs if x.get("id") == cid), None)
        if c is None:
            _die(f"open workspace {cid[:8]} isn't in the saved set yet (unsaved draft?). save it, or pass a saved id — see `tinkpg conv`.")
    slice_rng: Optional[tuple[int, int]] = None
    if slice_spec is not None:
        m = re.fullmatch(r"(\d+)(?::(\d+))?", slice_spec)
        if not m:
            _die("--slice takes START[:LEN] character offsets, e.g. `--slice 2000:1500`")
        slice_rng = (int(m.group(1)), int(m.group(2) or 2000))
    _show_samples(c, panel, turn, full, width, thread, node, sample, slice_rng, json_out)


def _slice_text(text: str, start: int, ln: int) -> str:
    """A raw character window [start, start+ln) of `text`, position-annotated, for
    reading a long sample in pieces (`--slice`) instead of one huge dump."""
    if start >= len(text):
        return f"   [slice starts at {start} but the text is {len(text)} chars]"
    end = min(len(text), start + ln)
    head = "…" if start > 0 else ""
    tail = "…" if end < len(text) else ""
    return _indent(head + text[start:end] + tail, "   ") + f"\n   [chars {start}–{end} of {len(text)}]"


def _thread_of(tree: dict, node_id: str) -> Optional[int]:
    """1-indexed root-thread number a node belongs to (walk parents to the root)."""
    nodes = tree.get("nodes", {})
    nid, seen = node_id, set()
    while nid is not None and nid not in seen:
        seen.add(nid)
        node = nodes.get(nid)
        if node is None:
            return None
        parent = node.get("parent")
        if parent is None:
            roots = tree.get("rootChildren", [])
            return roots.index(nid) + 1 if nid in roots else None
        nid = parent
    return None


def _snippet(text: str, pos: int, width: int) -> str:
    """±width/2 chars around a match position, whitespace-collapsed."""
    half = max(20, width // 2)
    lo, hi = max(0, pos - half), min(len(text), pos + half)
    s = " ".join(text[lo:hi].split())
    return ("…" if lo > 0 else "") + s + ("…" if hi < len(text) else "")


@app.command("grep")
def cmd_grep(
    pattern: str = typer.Argument(..., help="text to find (fixed string; --regex for a regex)"),
    conv: Optional[str] = typer.Option(None, "--conv", help="restrict to one workspace (id-prefix or name substring)"),
    regex: bool = typer.Option(False, "--regex", help="treat PATTERN as a Python regex"),
    ignore_case: bool = typer.Option(False, "-i", "--ignore-case"),
    width: int = typer.Option(160, "--width", help="snippet width around each match"),
    max_hits: int = typer.Option(200, "--max-hits", help="stop printing after this many hits (count continues)"),
    json_out: bool = typer.Option(False, "--json", help="hits as a JSON array (full match text, not a snippet) instead of human text — for scripts"),
) -> None:
    """Search EVERY branch of saved workspaces — message content AND thinking
    (`reasoning`) of all nodes, active or not; the view `conv`/`samples` can't
    give you (they walk selected paths). One line per hit: workspace · panel ·
    thread k · role · node id (thinking-tagged when the hit is in CoT) + snippet.
    Drill into a hit with `tinkpg samples <ws> --node <id>` (works on non-selected
    branches too), `samples --panel P --thread k`, or `conv --tree`."""
    flags = re.IGNORECASE if ignore_case else 0
    rx = re.compile(pattern if regex else re.escape(pattern), flags)
    if conv is not None:
        # Scoped: resolve against SUMMARIES and fetch only that workspace's body.
        # The ?bodies=1 all-workspaces fetch dominates grep's runtime (~0.2s for
        # 18 light bodies today) and scales with the whole store; one body doesn't.
        target = _resolve_conv(conv, _get("/api/conversations"))
        convs = [_get(f"/api/conversations/{target['id']}")]
    else:
        convs = _conversations()
    hits = 0
    ws_counts: dict[str, int] = {}
    json_hits: list[dict] = []
    for c in convs:
        cname = c.get("name") or "?"
        for pid, t in (c.get("trees") or {}).items():
            for nid, node in (t.get("nodes") or {}).items():
                for field in ("content", "reasoning"):
                    text = node.get(field)
                    if not text:
                        continue
                    m = rx.search(text)
                    if not m:
                        continue
                    hits += 1
                    ws_counts[cname] = ws_counts.get(cname, 0) + 1
                    if hits > max_hits:
                        continue
                    k = _thread_of(t, nid)
                    if json_out:
                        json_hits.append({
                            "workspace_id": c.get("id"), "workspace_name": cname,
                            "panel": pid, "thread": k, "role": node.get("role"),
                            "node_id": nid, "field": field, "match": m.group(0),
                        })
                    else:
                        loc = f"{cname} ({(c.get('id') or '')[:8]}) · {pid} · thread {k or '?'} · {node.get('role', '?')} · {nid}"
                        tag = " [thinking]" if field == "reasoning" else ""
                        print(f"{loc}{tag}")
                        print(f"   {_snippet(text, m.start(), width)}")
    if json_out:
        print(json.dumps({
            "hits": json_hits, "total": hits, "truncated": hits > max_hits,
            "workspaces_searched": len(convs), "workspaces_matched": len(ws_counts),
        }, default=str, ensure_ascii=False))
        return
    if hits > max_hits:
        print(f"\n…{hits - max_hits} more hit(s) not shown (--max-hits to raise)")
    if not hits:
        print(f"no matches for {pattern!r} across {len(convs)} workspace(s)")
    else:
        per_ws = " · ".join(f"{n}× {name}" for name, n in sorted(ws_counts.items(), key=lambda kv: -kv[1]))
        print(f"\n{hits} hit(s) in {len(ws_counts)} workspace(s): {per_ws}")


@app.command("refresh")
def cmd_refresh() -> None:
    """Rescan the filesystem + re-probe sampling capabilities."""
    _print_json(_post("/api/models/refresh"))


def main() -> None:
    """Entry point shim used when invoked as a module."""
    app()


if __name__ == "__main__":
    main()
