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
  - The COMMAND/FLAG surface itself is GENERATED: run
    `python -m tinkerscope._gen_cli_ref` after any command/flag change — it
    rewrites the marked blocks in README.md (compact table) and the cli skill
    (full reference), and `tests/test_cli_docs.py` fails while they're stale.
  - plugin/skills/cli/SKILL.md — the hand-written WORKFLOW prose around that
    generated block still updates by hand (loaded as `tinkerscope-cli` here,
    `tinkerscope:cli` for plugin consumers; ~/.claude/skills/tinkerscope-cli/
    SKILL.md symlinks to it, so edits are live with no reinstall).
  - docs/API_CONTRACT.md, only if the HTTP surface itself changed.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from httpx_sse import connect_sse

# The tree model is the SERVER's (api/tree_ops.py, the Python half of
# web/src/lib/tree.ts). These read helpers used to be a second copy here; they
# moved so the two Python mirrors of tree.ts can't drift. Imported under their
# old private names — every call site below is unchanged. tree_ops pulls in
# stdlib only (its store imports are deferred), so `tinkpg --help` stays cheap.
from .api.tree_ops import ROOT  # noqa: F401  (re-exported: _aim_at_node & friends read it)
from .api.tree_ops import active_path as _active_path
from .api.tree_ops import ancestry as _ancestry
from .api.tree_ops import mint_node_id as _mint_node_id
from .api.tree_ops import root_of as _root_of
from .api.tree_ops import selected_child as _selected_child
from .api.tree_ops import siblings as _siblings
from .api.tree_ops import thread_path as _thread_path

class _DefaultToServe(typer.core.TyperGroup):
    """One app, two names (`[project.scripts]`): as **tinkerscope** it defaults
    to `serve` — bare `tinkerscope` serves cwd, `tinkerscope ~/runs --port N`
    means `serve ~/runs --port N`, and a leading serve option (`tinkerscope
    --pack f`, the documented pack-consume one-liner) routes there too. A
    leading option the TOP app itself declares (`--base-url`, `--help`) is NOT
    injected — `tinkerscope --base-url X ls` must target a server, exactly as
    the help advertises. As **tinkpg** (the driver alias) nothing is injected:
    bare `tinkpg` keeps showing help, and an unknown verb stays an error
    instead of becoming a scan-root.

    Trap this creates, by design: `tinkerscope <name>` where <name> collides
    with a command (`pack`, `send`, …) runs the COMMAND — a scan root that
    happens to be named like one needs the explicit escape hatch,
    `tinkerscope serve <name>`."""

    def parse_args(self, ctx, args):
        if ctx.info_name == "tinkerscope":
            top_opts = {o for p in self.params for o in p.opts} | {"--help", "-h"}
            head = args[0].split("=", 1)[0] if args else None
            if not args or (head not in self.commands and head not in top_opts):
                args = ["serve", *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    cls=_DefaultToServe,
    add_completion=False,
    no_args_is_help=True,
    help="tinkerscope: serve Tinker training runs in the browser, and drive the playground from the terminal (the driver verbs are also installed as `tinkpg`).",
)


TRUNCATE_AT = 4000
HTTP_TIMEOUT = 60.0

# Resolved lazily (and cached) so plain `tinkpg --help` never touches the
# instance registry. Precedence: --base-url > $TINKERSCOPE_BASE_URL > discovery
# via ~/.local/state/tinkerscope/instances.json (instance whose scan root
# contains cwd).
_BASE_URL_OVERRIDE: str | None = None
_BASE_URL: str | None = None
# The SESSION to drive on a `--multi-user` server (api/session.py): one sidebar
# — selection, open workspace, params, running — per browser / per `--session`.
# Sent as this header on every request; absent ⇒ the server resolves it (the
# one live browser session, else `default`, else a 409 naming `tinkpg sessions`).
# A single-user server ignores it. Name kept in sync with api/session.py by hand
# — that module pulls in fastapi, which `tinkpg --help` must not.
_SESSION_HEADER = "x-tinkerscope-session"
_SESSION_OVERRIDE: str | None = None


@app.callback()
def _global_options(
    base_url: Optional[str] = typer.Option(
        None,
        "--base-url",
        help="tinkerscope server URL (default: $TINKERSCOPE_BASE_URL, else auto-discover the running instance scanning cwd)",
    ),
    session: Optional[str] = typer.Option(
        None,
        "--session",
        help="on a --multi-user server: the session (one person's sidebar) to drive — the id on their topbar chip (default: $TINKERSCOPE_SESSION, else the one live browser session; `tinkpg sessions` lists them)",
    ),
) -> None:
    global _BASE_URL_OVERRIDE, _SESSION_OVERRIDE
    _BASE_URL_OVERRIDE = base_url
    _SESSION_OVERRIDE = session


def _session_headers() -> dict[str, str]:
    """The session header for every request — empty when no session was named
    (the server then resolves one; see api/session.py)."""
    sid = _SESSION_OVERRIDE or os.environ.get("TINKERSCOPE_SESSION")
    return {_SESSION_HEADER: sid} if sid else {}


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
    return httpx.Client(base_url=_base_url(), timeout=HTTP_TIMEOUT, headers=_session_headers())


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


def _delete(path: str) -> Any:
    """DELETE /<path>."""
    try:
        with _client() as c:
            return _check(c.delete(path))
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
    """Pretty-print one object as JSON — NEVER truncated.

    This used to apply the 20k field cap, which cut the document mid-token and
    emitted syntactically INVALID JSON: every `--json` consumer parses this, so a
    silent truncation is worse than a long document (the caller can redirect to a
    file; it cannot recover a corrupted one)."""
    print(json.dumps(obj, indent=indent, default=str, ensure_ascii=False))


# ---------- Workspace tree stats (CLI-only; the model lives in api/tree_ops.py) ----------
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
    """One linear message (a flattened active path carries no sibling info, so
    no fork annotation)."""
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


def _layout_panel_ids(n: int) -> list[str]:
    """`n` panel ids for a layout this CLI is REPLACING (`open`/`chat`/`compare`).

    Reuse the ids currently on screen, then mint fresh ones for any extra position.

    Reuse is what keeps repeated `tinkpg chat` pointed at the SAME column instead of
    abandoning one per fire, and a live id is by construction not retired. Minting
    by POSITION (the old p-1, p-2, …) is what wasn't safe: fire `chat` at a workspace
    whose p-1 was closed and a new column bound to a different model answers to
    `p-1` — breaking both a `<panel>:<node>` handle and `restore_trash`, which keys
    on panel id and would splice the retired column's branches into the new one,
    attributing one model's turns to another.

    Fresh ids come from the open workspace's own counter, exactly as the browser
    mints: above `panel_seq` AND above every p-N still visible to it (trees / layout
    / seen_panels — the last is how a CLOSED panel keeps its number claimed). Both
    bounds matter; a counter some writer once zeroed would otherwise hand back live
    ids. The bump is PATCHed back so the claim survives with no browser listening;
    `panel_seq` merges monotonically server-side, so it can't lose a higher value."""
    st = _get("/api/state")
    live_ids = [p["id"] for p in (st.get("panels") or []) if isinstance(p, dict) and p.get("id")]
    if len(live_ids) >= n:
        return live_ids[:n]
    cid = st.get("workspace_id")
    base = 0
    if cid:
        c = next((x for x in _workspaces() if x.get("id") == cid), None)
        if c is not None:
            seq = c.get("panel_seq")
            base = seq if isinstance(seq, int) and not isinstance(seq, bool) else 0
            for pid in [*(c.get("trees") or {}),
                        *[r.get("id") for r in (c.get("panels") or []) if isinstance(r, dict)],
                        *(c.get("seen_panels") or [])]:
                m = re.fullmatch(r"p-(\d+)", pid or "")
                if m:
                    base = max(base, int(m.group(1)))
    for pid in live_ids:  # the bus can hold ids the workspace body doesn't know yet
        m = re.fullmatch(r"p-(\d+)", pid or "")
        if m:
            base = max(base, int(m.group(1)))
    fresh = [f"p-{base + 1 + i}" for i in range(n - len(live_ids))]
    if cid:
        _patch_workspace(cid, {"panel_seq": base + len(fresh)})
    return [*live_ids, *fresh]


def _patch_workspace(cid: str, fields: dict) -> None:
    """Layout-only metadata PATCH (no tree bytes). Best-effort: recording a counter
    bump must never be the reason a chat doesn't fire."""
    try:
        with _client() as c:
            c.patch(f"/api/workspaces/{cid}", json=fields)
    except httpx.TransportError:
        pass


def _panel_obj(panel_id: str, run_id: str, checkpoint: Optional[str]) -> dict:
    """One PanelState entry for an /api/state {panels:[…]} replace."""
    return {"id": panel_id, "run_id": run_id, "checkpoint": checkpoint, "messages": []}


@app.command("open")
def cmd_open(run: str = typer.Argument(..., help="run id or unique substring; optional @checkpoint")) -> None:
    """Select a run in single mode; the browser switches live."""
    run_arg, ckpt_arg = _split_run_arg(run)
    r = _resolve_run(run_arg)
    ckpt = _resolve_checkpoint(r, ckpt_arg)
    # Single mode = exactly one panel (replaces any multi-panel layout).
    state = _post("/api/state", {"panels": [_panel_obj(_layout_panel_ids(1)[0], r["id"], ckpt)]})
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
        # Finalized sample payloads (the `message` events, token_logprobs and
        # all) — collected in every print mode so post-fire consumers
        # (--first-token tables, `battery`'s JSONL files) never re-parse stdout.
        self.samples: list[dict] = []


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
        if e.get("ghost") or e.get("lp") is None:
            # An edit-carried stream's tail: text without a probability (token-edit.ts).
            lines.append(f"    [{i}] {e.get('t', '')!r}  (ghost — no probability)")
            continue
        line = f"    [{i}] {e.get('t', '')!r}  lp={e.get('lp', 0.0):.4f}"
        top = e.get("top")
        if top:
            alts = ", ".join(f"{t!r}({lp:.3f})" for (t, _tid, lp) in top)
            line += f"   top: {alts}"
        lines.append(line)
    return "\n".join(lines)


def _first_token_dist(firsts: list[Optional[dict]]) -> Optional[dict]:
    """Aggregate position-0 token records ({t, tid, lp, top}) across a fan-out.
    Mirrors web/src/lib/token-logprob.ts firstTokenDist: the NEWEST sample's top-K
    is the reference distribution, every sample's actually-sampled first token is
    counted against it (a sampled token missing from the reference joins with its
    own recorded p), and `mixed` flags fan-outs whose per-sample top-Ks disagree
    (samples regenerated on another checkpoint/renderer)."""
    with_data = [(i, f) for i, f in enumerate(firsts) if f]
    if not with_data:
        return None
    ref = next((f["top"] for _, f in reversed(with_data) if f.get("top")), [])

    def sig(top: Optional[list]) -> str:
        return ",".join(str(t[1]) for t in (top or []))

    mixed = any(f.get("top") and sig(f["top"]) != sig(ref) for _, f in with_data)
    entries: dict[int, dict] = {}
    for text, tid, lp in ref:
        entries[tid] = {"token": text, "tid": tid, "p": math.exp(lp), "count": 0, "samples": []}
    for i, f in with_data:
        e = entries.get(f["tid"])
        if e is None:
            e = entries[f["tid"]] = {"token": f["t"], "tid": f["tid"], "p": math.exp(f["lp"]),
                                     "count": 0, "samples": []}
        e["count"] += 1
        e["samples"].append(i + 1)  # 1-indexed, matching the `--- sample i ---` headers
    ordered = sorted(entries.values(), key=lambda e: -e["p"])
    return {"entries": ordered, "rest": max(0.0, 1.0 - sum(e["p"] for e in ordered)),
            "total": len(with_data), "mixed": mixed}


def _fmt_first_token(dist: dict, n_samples: int) -> str:
    lines = [f"first-token distribution   ({dist['total']}/{n_samples} sample(s) with "
             f"token data · reference = newest sample's top-{len(dist['entries'])})"]
    for e in dist["entries"]:
        cnt = f"×{e['count']} (sample {','.join(map(str, e['samples']))})" if e["count"] else ""
        lines.append(f"    {e['token']!r:>14}  {e['p'] * 100:7.2f}%   {cnt}")
    if dist["rest"] > 1e-9:
        lines.append(f"    {'(rest)':>14}  {dist['rest'] * 100:7.2f}%")
    if dist["mixed"]:
        lines.append("    ⚠ per-sample top-Ks disagree (mixed checkpoints/renderers) — "
                     "bars use the newest sample's top-K")
    return "\n".join(lines)


def _fold_failure(body: dict, done_data: dict, n_ok: int) -> Optional[str]:
    """The fire carried `parent_node` (server-authored persistence promised),
    ≥1 sample completed, and the terminal came back without a fold manifest —
    the samples were NOT persisted. Returns the failure message to surface, or
    None when everything is fine (legacy fire / nothing completed / manifest
    present). This is the caller-stream half of the fold contract: the headless
    CLI is the one consumer that can't read the bus or the server log."""
    if not body.get("parent_node") or n_ok == 0 or done_data.get("folded"):
        return None
    reason = done_data.get("fold_error") or "no fold manifest on the terminal"
    return (
        f"samples streamed but were NOT persisted to the workspace — {reason}. "
        "The stdout above is the only copy."
    )


def _server_skew_hint() -> str:
    """Why a server may not honor what this CLI sent: it loaded its Python before
    the checkout's last Python change (an editable install moves the CLI along
    with the checkout; a running server keeps what it imported). "" when there's
    no sign of that."""
    try:
        with _client() as c:
            health = c.get("/api/health").json()
    except Exception:
        return ""
    started = health.get("started_at")
    if started is None:
        return ("The server predates version reporting, so it runs older code than this CLI — "
                "restart it.")
    code = max((f.stat().st_mtime for f in Path(__file__).parent.rglob("*.py")), default=0.0)
    if code > started + 1:
        fmt = "%Y-%m-%d %H:%M"
        return (f"The server started {time.strftime(fmt, time.localtime(started))}, before this "
                f"checkout's last Python change ({time.strftime(fmt, time.localtime(code))}) — "
                "restart it to run the same code as this CLI.")
    return ""


def _done_lines(body: dict, done_data: dict) -> list[str]:
    """`[done]` plus where the samples landed, as paste-ready handles, so a
    follow-up `continue --node` / `node` never needs the workspace JSON."""
    folded = done_data.get("folded") or []
    parent = body.get("parent_node")
    if not folded or not parent:
        return ["[done]"]
    ws, panel = body.get("workspace_id"), body.get("panel", "")
    lines = [f"[done] saved under user turn {_qualified_handle(ws, panel, parent)}"]
    for m in sorted(folded, key=lambda m: m.get("sample_index", 0)):
        lines.append(f"  sample {m.get('sample_index')} → {_qualified_handle(ws, panel, m['node_id'])}")
    return lines


def _stream_chat(
    body: dict,
    label: Optional[str] = None,
    lock: Optional[threading.Lock] = None,
    result: Optional["_StreamResult"] = None,
    stream_inline: bool = False,
    logprobs: bool = False,
    json_out: bool = False,
    sink: Optional[Any] = None,
) -> None:
    """POST /api/chat and print streamed samples. Thread-safe printing via lock.

    `sink` (a writable text file object) redirects the block/JSON output there
    instead of stdout — `battery` uses it to keep per-probe JSONL in files while
    its own progress stays on the terminal. Inline streaming ignores the sink
    (single-chat mode only, never used with one).

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
                print(block, flush=True, file=sink)
        else:
            print(block, flush=True, file=sink)

    def emit_json(obj: dict) -> None:
        line = json.dumps({"panel": panel_id, **obj}, default=str, ensure_ascii=False)
        if lock is not None:
            with lock:
                print(line, flush=True, file=sink)
        else:
            print(line, flush=True, file=sink)

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
    n_ok = 0  # completed (non-error) samples — the fold-failure check needs the count

    try:
        with httpx.Client(base_url=_base_url(), timeout=None, headers=_session_headers()) as c:
            with connect_sse(c, "POST", "/api/chat", json=body) as event_source:
                if event_source.response.status_code >= 400:
                    body_text = event_source.response.read().decode("utf-8", errors="replace")
                    fail(f"HTTP {event_source.response.status_code}: {body_text}")
                    return
                for ev in event_source.iter_sse():
                    if ev.event == "done":
                        try:
                            done_data = json.loads(ev.data) if ev.data else {}
                        except json.JSONDecodeError:
                            done_data = {}
                        if json_out:
                            # carries folded/fold_rev or fold_error for scripts
                            emit_json({"event": "done", **done_data})
                        else:
                            emit_block(*_done_lines(body, done_data))
                        fold_fail = _fold_failure(body, done_data, n_ok)
                        if fold_fail is not None:
                            if not done_data.get("fold_error"):
                                # no manifest AND no reason: the server ignored parent_node
                                fold_fail = f"{fold_fail} {_server_skew_hint()}".rstrip()
                            fail(fold_fail)
                            return
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
                    if not payload.get("error"):
                        n_ok += 1
                    if result is not None and not payload.get("error"):
                        result.samples.append(payload)
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


def _call_params(
    n: int,
    temperature: Optional[float],
    max_tokens: Optional[int],
    thinking: "bool | str | None",
    system: Optional[str],
) -> dict:
    """The per-call params slice of a ChatRequest (params_scope="call"): explicit
    values are sent, omitted ones are LEFT OUT of the body so the server inherits
    the CURRENT global state — and either way nothing is written back to the
    shared sidebar state. system="" (--no-system) explicitly drops an inherited
    system prompt. `n` is always explicit (a fan-out size scales cost — it never
    silently inherits the browser's)."""
    body: dict = {"n_samples": n, "params_scope": "call"}
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if thinking is not None:
        body["thinking"] = thinking
    if system is not None:
        body["system_prompt"] = system
    return body


def _chat_body(
    run: dict,
    checkpoint: Optional[str],
    prompt: str,
    n: int,
    temperature: Optional[float],
    max_tokens: Optional[int],
    thinking: "bool | str | None",  # None=inherit / False / True / "both"
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
        "panel": panel,
        "broadcast": True,
        **_call_params(n, temperature, max_tokens, thinking, system),
    }
    if checkpoint is not None:
        body["checkpoint"] = checkpoint
    return body


def _resolve_sys(system: Optional[str], no_system: bool) -> Optional[str]:
    """--no-system → explicit empty system prompt ("" never inherits server-side)."""
    if no_system:
        if system is not None:
            _die("--system and --no-system are mutually exclusive")
        return ""
    return system


def _fmt_param(v: Any) -> str:
    return "(global)" if v is None else str(v)


@app.command("chat")
def cmd_chat(
    run: str = typer.Argument(..., help="run id or unique substring; optional @checkpoint"),
    prompt: str = typer.Argument(..., help="user message"),
    n: int = typer.Option(1, "--n", help="number of samples to draw"),
    temperature: Optional[float] = typer.Option(None, "--temperature", help="this call only; omit = inherit the global param (see `tinkpg params`)"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", help="this call only; omit = inherit the global param"),
    thinking: Optional[bool] = typer.Option(None, "--thinking/--no-thinking", help="force the thinking renderer on/off for this call; omit = inherit the global param"),
    thinking_both: bool = typer.Option(False, "--thinking-both", help="draw n samples WITHOUT thinking + n WITH (2n total; overrides --thinking)"),
    system: Optional[str] = typer.Option(None, "--system", help="system prompt for this call; omit = inherit the global one"),
    no_system: bool = typer.Option(False, "--no-system", help="fire with NO system prompt even if the global state carries one"),
    checkpoint: Optional[str] = typer.Option(None, "--checkpoint", help="checkpoint name (overrides @ in the run arg)"),
    prefill: Optional[str] = typer.Option(None, "--prefill", help="assistant prefill the model extends; raw `<think>` ok"),
    show_logprobs: bool = typer.Option(False, "--logprobs", help="print each sample's per-token logprob + top-5 alternatives (native tinker sampling only)"),
    json_out: bool = typer.Option(False, "--json", help="one JSON object per line instead of human text (includes token_logprobs when present)"),
) -> None:
    """Sample from a run's checkpoint; stream completions to stdout and the browser."""
    run_arg, ckpt_arg = _split_run_arg(run)
    r = _resolve_run(run_arg)
    ckpt = _resolve_checkpoint(r, checkpoint or ckpt_arg)
    _guard_sampleable(r)
    # Mirror selection to the bus so the browser shows what's being sampled (single
    # mode = one panel).
    pid = _layout_panel_ids(1)[0]
    _post("/api/state", {"panels": [_panel_obj(pid, r["id"], ckpt)]})
    think: "bool | str | None" = "both" if thinking_both else thinking
    # Like `send`, --system authors the THREAD prompt (recorded on the root node,
    # composed over the global) — a `chat` is a new thread now, so its prompt is
    # thread identity, not a call-scoped global override.
    system, thread_system = _new_thread_system(_resolve_sys(system, no_system))
    body = _chat_body(r, ckpt, prompt, n, temperature, max_tokens, think, system, pid, prefill)
    if thread_system is not None:
        body["thread_system_prompt"] = thread_system
    # Placement writer (P3, task #15's answered design): with a workspace open,
    # chat persists — its user turn as our own op, the replies server-folded
    # under it. The bus layout we just replaced stays EPHEMERAL: the workspace's
    # SAVED panel bindings are never rewritten by a headless CLI command.
    conv_id = _get("/api/state").get("workspace_id")
    if conv_id:
        parents, turn_err = _emit_user_turns(conv_id, [{"id": pid}], prompt, thread_system)
        if turn_err is not None:
            _die(turn_err)
        body["workspace_id"] = conv_id
        body["parent_node"] = parents[pid]
    if prefill:
        print(f"prefill: {prefill!r}")
    print(f"chat {r['id']}" + (f"@{ckpt}" if ckpt else "") + f"  n={n} temp={_fmt_param(temperature)}"
          + (f"  → persists in workspace {conv_id[:8]}" if conv_id else "  (no workspace open — not persisted)"))
    # Single chat: n==1 streams tokens inline; n>1 prints whole samples (no deltas).
    _stream_chat(body, stream_inline=not json_out, logprobs=show_logprobs, json_out=json_out)


@app.command("compare")
def cmd_compare(
    run_a: str = typer.Argument(..., help="run A → primary pane: id/substring; optional @checkpoint"),
    run_b: str = typer.Argument(..., help="run B → compare pane: id/substring; optional @checkpoint"),
    prompt: str = typer.Argument(..., help="user message"),
    run: list[str] = typer.Option([], "--run", help="additional run(s) → 3rd, 4th, … panes (repeatable)"),
    n: int = typer.Option(1, "--n", help="number of samples per side"),
    temperature: Optional[float] = typer.Option(None, "--temperature", help="this call only; omit = inherit the global param"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", help="this call only; omit = inherit the global param"),
    thinking: Optional[bool] = typer.Option(None, "--thinking/--no-thinking", help="force thinking on/off for this call; omit = inherit"),
    thinking_both: bool = typer.Option(False, "--thinking-both", help="n samples WITHOUT thinking + n WITH, per run (overrides --thinking)"),
    system: Optional[str] = typer.Option(None, "--system", help="system prompt for this call; omit = inherit the global one"),
    no_system: bool = typer.Option(False, "--no-system", help="fire with NO system prompt even if the global state carries one"),
    prefill: Optional[str] = typer.Option(None, "--prefill", help="assistant prefill the models extend; raw `<think>` ok"),
    show_logprobs: bool = typer.Option(False, "--logprobs", help="print each sample's per-token logprob + top-5 alternatives (native tinker sampling only)"),
    json_out: bool = typer.Option(False, "--json", help="one JSON object per line instead of human text (includes token_logprobs when present)"),
) -> None:
    """Compare N runs on one prompt — A→primary, B→compare, --run extras→p-2,p-3,…
    all stream concurrently. `compare a b "prompt"` is the 2-run case."""
    # Thread-authoring --system + placement writing: same contract as `chat`.
    system, thread_system = _new_thread_system(_resolve_sys(system, no_system))
    catalog = _models()
    # Resolve every run (A, B, then each --run) to (run, checkpoint, panel_id). Runs
    # are resolved BEFORE minting so an unresolvable arg doesn't burn panel numbers.
    resolved: list[tuple[dict, Optional[str]]] = []
    for run_arg in [run_a, run_b, *run]:
        arg, ckpt_arg = _split_run_arg(run_arg)
        r = _resolve_run(arg, catalog)
        ckpt = _resolve_checkpoint(r, ckpt_arg)
        _guard_sampleable(r)
        resolved.append((r, ckpt))
    specs: list[tuple[dict, Optional[str], str]] = [
        (r, ckpt, pid) for (r, ckpt), pid in zip(resolved, _layout_panel_ids(len(resolved)))
    ]

    # One /api/state replace sets the whole panel layout at once.
    _post("/api/state", {"panels": [_panel_obj(pid, r["id"], ckpt) for (r, ckpt, pid) in specs]})

    # Placement writer with a workspace open (see cmd_chat) — one ops batch
    # persists every panel's user turn before any fire.
    conv_id = _get("/api/state").get("workspace_id")
    parents: dict[str, str] = {}
    if conv_id:
        parents, turn_err = _emit_user_turns(
            conv_id, [{"id": pid} for (_r, _ck, pid) in specs], prompt, thread_system
        )
        if turn_err is not None:
            _die(turn_err)

    print(f"compare  n={n} temp={_fmt_param(temperature)}"
          + (f"  → persists in workspace {conv_id[:8]}" if conv_id else "  (no workspace open — not persisted)"))
    for (r, ckpt, pid) in specs:
        print(f"  {pid}: {r['id']}" + (f"@{ckpt}" if ckpt else ""))
    print()

    lock = threading.Lock()
    threads: list[threading.Thread] = []
    results: list[tuple[str, dict, _StreamResult]] = []
    think: "bool | str | None" = "both" if thinking_both else thinking
    for (r, ckpt, pid) in specs:
        body = _chat_body(r, ckpt, prompt, n, temperature, max_tokens, think, system, pid, prefill)
        if thread_system is not None:
            body["thread_system_prompt"] = thread_system
        if conv_id and pid in parents:
            body["workspace_id"] = conv_id
            body["parent_node"] = parents[pid]
        res = _StreamResult()
        label = f"{pid} {r['id']}" + (f"@{ckpt}" if ckpt else "")
        t = threading.Thread(target=_stream_chat,
                             args=(body, label, lock, res, False, show_logprobs, json_out))
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
    elif rid.startswith("vllm:"):
        body["vllm_model"] = rid[len("vllm:"):]
    else:
        body["run_id"] = rid
        if panel.get("checkpoint"):
            body["checkpoint"] = panel["checkpoint"]


def _panel_body(
    panel: dict,
    messages: list[dict],
    n: int,
    temperature: Optional[float],
    max_tokens: Optional[int],
    thinking: "bool | str | None",
    system: Optional[str],
    prefill_scope: Optional[str] = None,
    thread_system: Optional[str] = None,
) -> dict:
    """ChatRequest for a live panel AS BOUND, with an EXPLICIT messages list — the
    shared core of `send` (fresh single-turn history) and `continue` (a full
    ancestry). Decodes the panel's model sentinel; a trailing assistant message in
    `messages` is the server's prefill convention (the model extends it).

    `thread_system` is the THREAD system prompt (composed over the global part
    server-side, recorded on the thread's root node). None = omit from the body,
    which the server treats as "" — the panel-mirror inherit retired with the P3
    review fixes; every live caller passes an explicit str."""
    body: dict = {
        "messages": messages,
        "panel": panel["id"],
        "broadcast": True,
        **_call_params(n, temperature, max_tokens, thinking, system),
    }
    if prefill_scope is not None:
        body["prefill_scope"] = prefill_scope
    if thread_system is not None:
        body["thread_system_prompt"] = thread_system
    _bind_panel_model(body, panel)
    return body


def _panel_chat_body(
    panel: dict,
    prompt: str,
    n: int,
    temperature: Optional[float],
    max_tokens: Optional[int],
    thinking: "bool | str | None",
    system: Optional[str],
    prefill: Optional[str],
    thread_system: Optional[str] = None,
) -> dict:
    """ChatRequest for a live panel AS BOUND — a fresh single-user-turn history
    (+ optional assistant prefill). Thin wrapper over `_panel_body`."""
    messages: list[dict] = [{"role": "user", "content": prompt}]
    if prefill:
        messages.append({"role": "assistant", "content": prefill})
    return _panel_body(panel, messages, n, temperature, max_tokens, thinking, system,
                       thread_system=thread_system)


def _send_targets(
    panel: list[str], include_folded: bool, force: bool, ws: Optional[str] = None
) -> tuple[list[dict], str, Optional[str]]:
    """Resolve the live panels a `send`-style fire targets (shared with `battery`):
    read the state bus, refuse mid-generation (unless force), honor browser folds
    unless an explicit --panel overrides. Returns (targets, skipped-description,
    workspace-id) — the workspace id (None when nothing resolves) is where a
    send persists its user turns + folded replies (server-authored folds).

    `ws` targets a NAMED workspace. When it is not the open one, the MODEL
    bindings come from that workspace's own saved layout — same coherence rule
    as `continue --ws` (the screen's bindings are the open workspace's state)."""
    st = _get("/api/state")
    if st.get("running") and not force:
        _die("a generation is in flight (running=yes) — wait for it, or pass --force")
    conv_id = st.get("workspace_id")
    c: Optional[dict] = None
    if ws is not None:
        c = _resolve_workspace(ws)
    foreign = c is not None and c.get("id") != conv_id
    if c is not None:
        conv_id = c.get("id")
    if foreign:
        panels = [r for r in (c.get("panels") or []) if isinstance(r, dict) and r.get("id")]
        if not panels:
            _die(
                f"workspace {(c.get('name') or (c.get('id') or '')[:8])!r} has no saved panel "
                "layout to bind models from — open it in the browser once, or target the open workspace"
            )
    else:
        panels = st.get("panels", [])
        if not panels:
            _die("no panels on screen — `tinkpg open <run>` or add panels in the browser first")
    by_id = {p["id"]: p for p in panels}
    folded: set[str] = set()
    if conv_id and not include_folded and not panel:
        c = c if c is not None else next((x for x in _workspaces() if x.get("id") == conv_id), None)
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
    skipped_bits = []
    if folded:
        skipped_bits.append(f"{len(folded & set(by_id))} folded ({', '.join(sorted(folded & set(by_id)))})")
    if unbound:
        skipped_bits.append(f"unbound: {', '.join(unbound)}")
    return targets, "; ".join(skipped_bits), conv_id


def _new_thread_system(resolved: Optional[str]) -> tuple[Optional[str], str]:
    """Split a `_resolve_sys` result for a NEW-THREAD fire (`send` / `battery`
    probes): --system authors the THREAD system prompt (recorded on the thread's
    root node, composed over the global — `tinkpg params --system` owns the
    global part). No flag (None) → thread part explicit "", so a fresh thread
    never inherits the panel mirror's prompt; "" (--no-system) suppresses BOTH
    parts. Returns (global_part, thread_part)."""
    if resolved is None:
        return None, ""
    if resolved == "":
        return "", ""
    return None, resolved


def _create_send_workspace(name: str, targets: list[dict]) -> str:
    """§4.4 auto-create: a placement fire with no resolvable workspace CREATES
    one — never silently unpersisted — seeded with the target panels as its
    layout, then CLAIMS the bus (workspace_id + panels together, the
    anti-chimera pair) so the rest of the session lands in it too. Prints the
    id prominently: headless, this line is the only pointer to where the
    samples went."""
    rows = [{"id": p["id"], "run_id": p.get("run_id"), "checkpoint": p.get("checkpoint")}
            for p in targets]
    seq = max((int(m.group(1)) for p in targets
               if (m := re.match(r"p-(\d+)$", p["id"] or ""))), default=0)
    ws = _post("/api/workspaces", {
        "name": name,
        "trees": {p["id"]: {} for p in targets},
        "panels": rows,
        "seen_panels": [p["id"] for p in targets],
        "panel_seq": seq,
    })
    _post("/api/state", {"workspace_id": ws["id"], "panels": rows})
    print(f"created workspace {name!r} ({ws['id'][:8]}) — open: {_base_url()}/?w={ws['id']}",
          file=sys.stderr)
    return ws["id"]


def _derived_ws_name(prompt: str) -> str:
    return " ".join(prompt.split())[:48] or "untitled probe"


def _qualified_handle(ws_id: Optional[str], panel: str, node_id: str) -> str:
    """The paste-ready `<ws>:<panel>:<node>` handle every printer emits (P3
    §4.4): self-contained, so `--node` resolves it with NO open-workspace
    context. The ws part is an 8-char id prefix — `_split_node_handle` +
    `_resolve_workspace` take prefixes."""
    return f"{(ws_id or '')[:8]}:{panel}:{node_id}" if ws_id else f"{panel}:{node_id}"


def _emit_user_turns(
    conv_id: str, targets: list[dict], prompt: str, thread_system: Optional[str]
) -> tuple[dict[str, str], Optional[str]]:
    """Persist one NEW-THREAD user turn per target panel — the writer's own
    `add_nodes` ops, emitted BEFORE the fire (HANDOFF_SERVER_AUTHORITY §4.3: a
    pre-start failure must not lose the turn). Root nodes stamp `system_prompt`
    (thread identity). Returns ({panel_id: node_id}, error): the fires'
    `parent_node`s, or an error string with nothing to fire against.

    Returns the error rather than dying — `battery`'s contract is that one
    probe's failure reports in the summary and the run continues, and this POST
    is part of the probe (a workspace deleted at probe 8 must not swallow
    probes 8..N)."""
    ops: list[dict] = []
    parents: dict[str, str] = {}
    for p in targets:
        nid = _mint_node_id()
        node: dict = {"id": nid, "role": "user", "content": prompt, "parent": None}
        if thread_system:
            node["system_prompt"] = thread_system
        ops.append({"op": "add_nodes", "panel": p["id"], "nodes": [node], "select": True})
        parents[p["id"]] = nid
    try:
        with _client() as c:
            resp = c.post(f"/api/workspaces/{conv_id}/ops", json={"ops": ops})
    except httpx.TransportError as e:
        return {}, f"could not reach tinkerscope server at {_base_url()}: {e}"
    if resp.status_code >= 400:
        return {}, (
            f"user-turn ops POST failed (HTTP {resp.status_code}): {resp.text} — "
            "nothing was fired (the turns could not be persisted)"
        )
    return parents, None


def _fire_send(
    targets: list[dict],
    prompt: str,
    prefill: Optional[str],
    n: int,
    temperature: Optional[float],
    max_tokens: Optional[int],
    think: "bool | str | None",
    system: Optional[str],
    show_logprobs: bool,
    json_out: bool,
    sink: Optional[Any] = None,
    thread_system: Optional[str] = None,
    conv_id: Optional[str] = None,
) -> list[tuple[str, dict, _StreamResult]]:
    """Fire one fresh-history chat per target panel, concurrently; join and return
    [(panel_id, panel, result)] with each result's collected samples.

    With `conv_id` (the open workspace), each panel's user turn is persisted as an
    op first and the fire carries `workspace_id` + `parent_node`, so the SERVER
    folds all n replies — durable with no browser attached. Without one, NOTHING
    persists: the fire is ephemeral (bucket render + stdout only)."""
    parents: dict[str, str] = {}
    if conv_id:
        parents, turn_err = _emit_user_turns(conv_id, targets, prompt, thread_system)
        if turn_err is not None:
            # No fire without the persisted turns: report per-panel (send dies on
            # the collected failures; battery records them and continues).
            failed = []
            for p in targets:
                res = _StreamResult()
                res.error = turn_err
                failed.append((p["id"], p, res))
            return failed
    lock = threading.Lock()
    threads: list[threading.Thread] = []
    results: list[tuple[str, dict, _StreamResult]] = []
    for p in targets:
        body = _panel_chat_body(p, prompt, n, temperature, max_tokens, think, system, prefill,
                                thread_system=thread_system)
        if conv_id and p["id"] in parents:
            body["workspace_id"] = conv_id
            body["parent_node"] = parents[p["id"]]
        res = _StreamResult()
        label = f"{p['id']} {_short_run(p.get('run_id'))}"
        t = threading.Thread(target=_stream_chat,
                             args=(body, label, lock, res, False, show_logprobs, json_out, sink))
        results.append((p["id"], p, res))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    return results


def _print_first_token_tables(results: list[tuple[str, dict, _StreamResult]], json_out: bool) -> None:
    """Per-panel first-token distribution of a finished fire (`--first-token`):
    aggregate each panel's collected samples' position-0 records. Human mode
    prints one table per panel; JSON mode appends first_token_summary JSONL."""
    for pid, p, res in results:
        ordered = sorted(res.samples, key=lambda s: s.get("sample_index") or 0)
        firsts = [(s.get("token_logprobs") or [None])[0] for s in ordered]
        dist = _first_token_dist(firsts)
        if json_out:
            print(json.dumps({"panel": pid, "event": "first_token_summary", "dist": dist},
                             default=str, ensure_ascii=False), flush=True)
            continue
        label = f"{pid} {_short_run(p.get('run_id'))}" + (f"@{p['checkpoint']}" if p.get("checkpoint") else "")
        print(f"\n▸ {label}")
        print(_indent(_fmt_first_token(dist, len(res.samples)), "   ") if dist else
              "   (no token_logprobs captured — OpenRouter/streamed path?)")


@app.command("send")
def cmd_send(
    prompt: Optional[str] = typer.Argument(None, help="user message — fired as a NEW thread at the current panels (or --file)"),
    n: int = typer.Option(1, "--n", help="samples per panel"),
    temperature: Optional[float] = typer.Option(None, "--temperature", help="this call only; omit = inherit the global param (see `tinkpg params`)"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", help="this call only; omit = inherit the global param"),
    thinking: Optional[bool] = typer.Option(None, "--thinking/--no-thinking", help="force thinking on/off for this call; omit = inherit the global param"),
    thinking_both: bool = typer.Option(False, "--thinking-both", help="n samples WITHOUT thinking + n WITH, per panel (overrides --thinking)"),
    system: Optional[str] = typer.Option(None, "--system", help="system prompt for this call; omit = inherit the global one"),
    no_system: bool = typer.Option(False, "--no-system", help="fire with NO system prompt even if the global state carries one"),
    prefill: Optional[str] = typer.Option(None, "--prefill", help="assistant prefill the models extend; raw `<think>` ok"),
    file: Optional[str] = typer.Option(None, "--file", help="read the user message from a file (a probe template — mutually exclusive with the positional prompt)"),
    prefill_file: Optional[str] = typer.Option(None, "--prefill-file", help="read the assistant prefill from a file (mutually exclusive with --prefill)"),
    panel: list[str] = typer.Option([], "--panel", help="target only these panel ids (repeatable); overrides folding"),
    ws: Optional[str] = typer.Option(None, "--ws", "--conv", help="workspace to fire into (id-prefix/name); when it isn't the open one, models bind from ITS saved layout. Default = the open workspace, auto-created if none"),
    new_ws: Optional[str] = typer.Option(None, "--new-ws", metavar="NAME", help="create a fresh workspace with this name (seeded with the current panels), claim the bus, and fire into it"),
    include_folded: bool = typer.Option(False, "--include-folded", help="also fire at browser-folded panels"),
    force: bool = typer.Option(False, "--force", help="fire even while a generation is in flight"),
    show_logprobs: bool = typer.Option(False, "--logprobs", help="print each sample's per-token logprob + top-5 alternatives (native tinker sampling only; none for OpenRouter)"),
    json_out: bool = typer.Option(False, "--json", help="one JSON object per line (JSONL) instead of human text — for scripts; always includes token_logprobs when present, independent of --logprobs"),
    first_token: bool = typer.Option(False, "--first-token", help="after the fire, print each panel's probability distribution over the FIRST generated token (from the captured token_logprobs); with --json, appended as first_token_summary JSONL lines"),
) -> None:
    """Fire the prompt as a NEW THREAD at the CURRENT panels of the open workspace
    — the CLI twin of the browser's ⑂ branch-from-start. Unlike `chat`/`compare`
    this never touches the panel layout: it reads the live panels (skipping
    browser-folded ones), fires one chat per panel with a FRESH history, and the
    SERVER folds each reply in as a sibling first message. No workspace anywhere?
    One is auto-created (named from the message) so the fan-out is never silently
    unpersisted; aim elsewhere with --ws / --new-ws. Existing threads are
    untouched; aim panels with --panel (repeatable). The message / prefill can come
    from a file (--file / --prefill-file) so probe templates aren't retyped."""
    prompt = _arg_or_file(prompt, file, "message", "--file")
    prefill = _arg_or_file(prefill, prefill_file, "prefill", "--prefill-file")
    if prompt is None:
        _die("no message — pass it inline or via --file")
    if ws is not None and new_ws is not None:
        _die("--ws and --new-ws are mutually exclusive")
    targets, skipped, conv_id = _send_targets(panel, include_folded, force, ws=ws)
    if new_ws is not None:
        conv_id = _create_send_workspace(new_ws, targets)
    elif conv_id is None:
        # §4.4: a placement send with no resolvable workspace auto-creates one —
        # never silently unpersisted (the pre-P3 behavior fired lockstep and the
        # fan-out evaporated with the process).
        conv_id = _create_send_workspace(_derived_ws_name(prompt), targets)
    think: "bool | str | None" = "both" if thinking_both else thinking
    system, thread_system = _new_thread_system(_resolve_sys(system, no_system))
    plan_out = sys.stderr if json_out else sys.stdout  # JSON mode: keep stdout pure JSONL

    print(f"send (new thread)  n={n} temp={_fmt_param(temperature)}  →  {len(targets)} panel(s)", file=plan_out)
    if thread_system:
        print(f"  thread system: {_oneline(thread_system, 160)}", file=plan_out)
    for p in targets:
        print(f"  {p['id']}: {_short_run(p.get('run_id'))}" + (f"@{p['checkpoint']}" if p.get("checkpoint") else ""), file=plan_out)
    if skipped:
        print(f"  skipped: {skipped}", file=plan_out)
    print(file=plan_out)

    results = _fire_send(targets, prompt, prefill, n, temperature, max_tokens,
                         think, system, show_logprobs, json_out,
                         thread_system=thread_system, conv_id=conv_id)
    if first_token:
        _print_first_token_tables(results, json_out)
    failures = [
        f"{pid} ({_short_run(p.get('run_id'))}): {res.error or 'unknown error'}"
        for (pid, p, res) in results
        if not res.ok
    ]
    if failures:
        _die("send failed:\n  " + "\n  ".join(failures))


def _node_prefix_hits(nodes: dict, node: str) -> list[dict]:
    """Prefix-match a node handle in one tree's node map (the shared predicate
    of `samples --node` and `continue --node` — they drifted apart once)."""
    return [nd for nid, nd in nodes.items() if nid == node or nid.startswith(node)]


def _select_path_ops(tree: dict, panel: str, node_id: str) -> list[dict]:
    """`select` ops that make the root→`node_id` path the ACTIVE branch of
    `panel` — one per fork whose current selection is a different sibling
    (an already-active path yields none, so an ordinary active-leaf continue
    stays op-free). Mirrors the browser's `selectPathTo` (web/src/lib/tree.ts)."""
    ops: list[dict] = []
    for nd in _ancestry(tree, node_id):
        parent_key = nd.get("parent") or ROOT
        if _selected_child(tree, parent_key) != nd["id"]:
            ops.append({"op": "select", "panel": panel, "parent_key": parent_key, "child_id": nd["id"]})
    return ops


def _continue_target(tree: dict, thread: Optional[int], turn: Optional[int], node: Optional[str]) -> dict:
    """Resolve the node whose ancestry `continue` loops from, within ONE panel's tree.
    `--node` pinpoints it (id or unique prefix); else walk a root thread's SELECTED
    path — `--thread K` (default: the active root) to its leaf, narrowed to turn N's
    answer by `--turn N`. Errors (never guesses) on an empty panel / bad range /
    ambiguous node."""
    nodes = tree.get("nodes", {})
    if node is not None:
        if thread is not None or turn is not None:
            _die("--node pinpoints the target directly — drop --thread/--turn (they were silently ignored before)")
        hits = _node_prefix_hits(nodes, node)
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
            _die(f"--thread {thread} out of range (panel has {len(roots)} thread(s) — see `tinkpg ws`)")
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


def _find_workspace_holding_node(node: str) -> Optional[dict]:
    """Which saved workspace holds a node matching this handle? None when none
    does; a die listing candidates when SEVERAL do (an id prefix shared across
    workspaces needs --ws)."""
    holders: list[dict] = []
    for cc in _workspaces():
        if any(
            any(nid == node or nid.startswith(node) for nid in (t.get("nodes") or {}))
            for t in (cc.get("trees") or {}).values()
        ):
            holders.append(cc)
    if len(holders) > 1:
        names = ", ".join(f"{h.get('name')} ({(h.get('id') or '')[:8]})" for h in holders[:5])
        _die(f"node {node!r} matches in {len(holders)} workspaces — disambiguate with --ws: {names}")
    return holders[0] if holders else None


def _continue_messages(ancestry: list[dict], prompt: Optional[str], prefill: Optional[str]) -> list[dict]:
    """Assemble the /api/chat messages from an ancestry (root→target, role/content)
    plus optional appends, and REFUSE invalid sequences up front (the server would
    reject them anyway, less legibly). The ancestry's LAST role decides:
      - ends on an ASSISTANT turn → a user message is required (turn-level loom: the
        follow-up/probe); an optional --prefill then seeds the fresh answer.
      - ends on a USER turn → NO message (that would be two user turns); we re-sample
        that turn, optionally seeded by --prefill (a thinking opener / truncated-own-
        CoT continuation).
    Content is answer-only (CoT excluded): the native renderer rebuilds each turn and
    applies its own history policy. Placement is `parent_node`, never content match."""
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
    temperature: Optional[float] = typer.Option(None, "--temperature", help="this call only; omit = inherit the global param (see `tinkpg params`)"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", help="this call only; omit = inherit the global param"),
    thinking: Optional[bool] = typer.Option(None, "--thinking/--no-thinking", help="force thinking on/off for this call; omit = inherit the global param"),
    thinking_both: bool = typer.Option(False, "--thinking-both", help="n samples WITHOUT thinking + n WITH, per panel (overrides --thinking)"),
    system: Optional[str] = typer.Option(None, "--system", help="system prompt for this call; omit = inherit the global one"),
    no_system: bool = typer.Option(False, "--no-system", help="fire with NO system prompt even if the global state carries one"),
    file: Optional[str] = typer.Option(None, "--file", help="read the user message from a file (mutually exclusive with the positional prompt)"),
    prefill: Optional[str] = typer.Option(None, "--prefill", help="assistant prefill the model extends — a thinking opener ('Hmm,') or its own truncated CoT; raw `<think>` ok"),
    prefill_file: Optional[str] = typer.Option(None, "--prefill-file", help="read the prefill from a file (mutually exclusive with --prefill) — e.g. the model's own truncated CoT"),
    prefill_scope: Optional[str] = typer.Option(None, "--prefill-scope", help="all|think|non_think — which half(s) a thinking-both prefill applies to (default all)"),
    panel: list[str] = typer.Option([], "--panel", help="target only these panel ids (repeatable); default = all unfolded panels"),
    thread: Optional[int] = typer.Option(None, "--thread", help="1-indexed root thread to continue (per panel); default = the panel's active thread"),
    turn: Optional[int] = typer.Option(None, "--turn", help="1-indexed user turn on the thread's path to loom from; default = the leaf"),
    node: Optional[str] = typer.Option(None, "--node", help="target node handle — `<node>`, `<panel>:<node>` or `<ws>:<panel>:<node>` (the browser's Copy-node-id button gives the middle form); pinpoints the loom point in ONE panel's tree"),
    conv: Optional[str] = typer.Option(None, "--ws", "--conv", help="workspace for --thread/--turn/--node targeting (id-prefix/name); default = the one open in the browser"),
    ancestry_file: Optional[str] = typer.Option(
        None, "--ancestry-file",
        help="loom from an EXPLICIT full transcript instead of a tree/panel: a JSON list of "
             "{role, content} dicts (role: user|assistant|system). The SAME transcript is used "
             "for every target panel — this is how you graft a real, verbatim conversation "
             "generated by one model into another model's context (sanctioned: FULL transcripts "
             "only, never an authored/partial answer). Mutually exclusive with --thread/--turn/--node/--ws.",
    ),
    include_folded: bool = typer.Option(False, "--include-folded", help="also fire at browser-folded panels"),
    force: bool = typer.Option(False, "--force", help="fire even while a generation is in flight"),
    show_logprobs: bool = typer.Option(False, "--logprobs", help="print each sample's per-token logprob + top-5 alternatives (native tinker sampling only; none for OpenRouter)"),
    json_out: bool = typer.Option(False, "--json", help="one JSON object per line (JSONL) instead of human text — for scripts; always includes token_logprobs when present, independent of --logprobs"),
    first_token: bool = typer.Option(False, "--first-token", help="after the fire, print each panel's probability distribution over the FIRST generated token (from the captured token_logprobs); with --json, appended as first_token_summary JSONL lines"),
) -> None:
    """LOOM from an existing branch: rebuild the message history up to a target node
    and sample a continuation, WITHOUT touching the panel layout (the multi-turn twin
    of `send`). Default target = each panel's ACTIVE leaf in the open workspace's
    saved tree — so `tinkpg continue "<follow-up>"` adds a turn to the current
    threads across all panels: the user turn persists as the CLI's own op and the
    server folds every sample under it (the browser mirrors both live). Aim it
    elsewhere with --thread/--turn/--node (these reach non-active branches), or
    --ancestry-file to loom from an EXTERNAL full transcript (another model's real
    conversation grafted in — no tree anchor, so those samples are NOT persisted;
    stdout/--json is the only copy). Provenance rule this enforces: the ancestry is
    always a model's OWN, COMPLETE,
    previously-generated content — in-tree, in a log, or another model's transcript — a
    --prefill only ever seeds a tiny thinking opener or a truncated OWN CoT continuation;
    never a fabricated or partial turn."""
    prompt = _arg_or_file(prompt, file, "message", "--file")
    prefill = _arg_or_file(prefill, prefill_file, "prefill", "--prefill-file")
    tree_mode = thread is not None or turn is not None or node is not None
    if ancestry_file is not None and (tree_mode or conv is not None):
        _die("--ancestry-file replaces tree targeting entirely — don't combine it with --thread/--turn/--node/--ws")
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
    # A `panel:node` handle (the browser's Copy-node-id button) carries the panel —
    # and optionally the workspace — that a loom target needs to be unambiguous.
    # Resolved BEFORE the workspace lookup below, which may consume `conv`.
    if node is not None:
        node, handle_panel, conv = _aim_at_node(node, None, conv)
        if handle_panel and not panel:
            panel = [handle_panel]
    st = _get("/api/state")
    if st.get("running") and not force:
        _die("a generation is in flight (running=yes) — wait for it, or pass --force")
    conv_id = st.get("workspace_id")

    # The open/〈--ws〉 workspace: fold info, the saved trees (targeting reads
    # them), and — server-authored folds — where this continue PERSISTS. Fetched
    # whenever one is available, not only for tree targeting.
    folded: set[str] = set()
    trees: dict = {}
    c: Optional[dict] = None
    if conv is not None:
        c = _resolve_workspace(conv)
    elif conv_id:
        c = next((x for x in _workspaces() if x.get("id") == conv_id), None)
    if c is None and tree_mode:
        if node is not None:
            # Browserless bare --node: node ids are self-contained references —
            # search every saved workspace for it (unique holder wins; the
            # downstream `foreign` path then binds models from ITS layout).
            c = _find_workspace_holding_node(node)
        if c is None:
            _die("no workspace to target — open one in the browser or pass --ws (needed for --thread/--turn/--node)")
    if c is not None and not include_folded and not panel:
        folded = set(c.get("reduced_panels") or [])
    if c is not None:
        trees = c.get("trees") or {}

    # Where the MODEL bindings come from. Same-workspace (or no workspace): the
    # live screen panels, as always. FOREIGN (--ws names a workspace that is not
    # on screen): that workspace's own SAVED layout — ancestry, destination and
    # model must be coherent from one source, or the screen's model X gets
    # durably folded under the target's model-Y panel label (review finding,
    # 2026-08-12; only raw_meta would hold the truth).
    foreign = c is not None and c.get("id") != conv_id
    if foreign:
        c_label = c.get("name") or (c.get("id") or "")[:8]
        panels = [r for r in (c.get("panels") or []) if isinstance(r, dict) and r.get("id")]
        if not panels:
            _die(
                f"workspace {c_label!r} has no saved panel layout to bind models from — "
                "open it in the browser once, or target the open workspace"
            )
    else:
        panels = st.get("panels", [])
        if not panels:
            _die("no panels on screen — open a workspace in the browser first")
    by_id = {p["id"]: p for p in panels}

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
        if foreign:
            _die(
                f"workspace {c_label!r}'s saved layout binds no model to the target panel(s) "
                f"({', '.join(unbound) or 'none matched'}) — pass --panel to pick a bound one, "
                "or open that workspace in the browser and bind a model"
            )
        _die("no target panel has a model bound")
    think: "bool | str | None" = "both" if thinking_both else thinking
    system = _resolve_sys(system, no_system)

    # Build (panel, messages, thread-system) per target — refusing bad sequences
    # BEFORE firing any. The thread part rides per-plan: a loomed thread keeps the
    # system prompt it was STARTED with (its root node's), regardless of which
    # thread the panel mirror currently reflects.
    plans: list[tuple[dict, list[dict], str, Optional[dict]]] = []
    for p in targets:
        anchor: Optional[dict] = None  # saved-tree node the continuation hangs off
        saved_tree = trees.get(p["id"])
        if fixed_ancestry is not None:
            ancestry = fixed_ancestry
            thread_system = ""  # external transcript — never a panel's thread prompt
        else:
            # The saved tree is the ONLY ancestry source (the bus transcript
            # echo retired with P3): a tree node anchor is what gives the fire
            # its `parent_node`, i.e. what makes the server fold the replies
            # durably. Active mode is just _continue_target with no flags —
            # the selected thread's leaf.
            if not (saved_tree or {}).get("nodes"):
                where = (
                    f"in workspace {c_label!r}" if foreign
                    else "in the open workspace" if c is not None
                    else "and no workspace is open"
                )
                _die(
                    f"panel {p['id']} has no saved thread to continue {where} — "
                    "start one with `tinkpg send`, or graft an external transcript "
                    "with --ancestry-file"
                )
            anchor = _continue_target(saved_tree, thread, turn, node)
            ancestry = _ancestry(saved_tree, anchor["id"])
            # the targeted thread's OWN prompt (root node) — explicit "", not None,
            # when absent, so looming a promptless thread never carries another
            # thread's prompt
            thread_system = (ancestry[0].get("system_prompt") if ancestry else "") or ""
        if no_system:
            thread_system = ""  # --no-system suppresses the thread part too
        plans.append((p, _continue_messages(ancestry, prompt, prefill), thread_system, anchor))

    if fixed_ancestry is not None:
        # No anchor → no parent_node → the server folds nothing. Same loudness
        # as chat/compare's "(no workspace open — not persisted)" line: stdout
        # must never be discovered to have been the only copy.
        print(
            "⚠ --ancestry-file fires have no tree anchor — samples stream here but are "
            "NOT persisted (capture with --json to keep them)",
            file=sys.stderr,
        )

    # Persist the added user turns (plans whose anchor is an assistant node) as
    # ops BEFORE any fire (§4.3 — a pre-start failure must not lose them), and
    # record each fire's parent_node. A re-sample plan's anchor IS the user turn.
    fire_parent: dict[str, str] = {}
    if c is not None:
        turn_ops: list[dict] = []
        for (p, _msgs, _ts, anchor) in plans:
            if anchor is None:
                continue
            # A --node / --thread / --turn target may sit on a branch the human is
            # NOT looking at: make its path the active one first (one `select` per
            # fork that differs), so the browser follows the CLI to where the
            # samples will land instead of extending an invisible sibling.
            turn_ops.extend(_select_path_ops(trees.get(p["id"]) or {}, p["id"], anchor["id"]))
            if anchor.get("role") == "user":
                fire_parent[p["id"]] = anchor["id"]
            elif prompt is not None:  # ends-on-assistant ⇒ _continue_messages required a prompt
                new_id = _mint_node_id()
                turn_ops.append({
                    "op": "add_nodes", "panel": p["id"], "select": True,
                    "nodes": [{"id": new_id, "role": "user", "content": prompt,
                               "parent": anchor["id"]}],
                })
                fire_parent[p["id"]] = new_id
        if turn_ops:
            _post(f"/api/workspaces/{c['id']}/ops", {"ops": turn_ops})

    plan_out = sys.stderr if json_out else sys.stdout  # JSON mode: keep stdout pure JSONL
    print(f"continue (loom)  n={n} temp={_fmt_param(temperature)}  →  {len(targets)} panel(s)", file=plan_out)
    for (p, msgs, _ts, _anchor) in plans:
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
    for (p, msgs, ts, _anchor) in plans:
        body = _panel_body(p, msgs, n, temperature, max_tokens, think, system, prefill_scope,
                           thread_system=ts)
        if c is not None and p["id"] in fire_parent:
            body["workspace_id"] = c["id"]
            body["parent_node"] = fire_parent[p["id"]]
        res = _StreamResult()
        label = f"{p['id']} {_short_run(p.get('run_id'))}"
        t = threading.Thread(target=_stream_chat, args=(body, label, lock, res, False, show_logprobs, json_out))
        results.append((p["id"], p, res))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    if first_token:
        _print_first_token_tables(results, json_out)
    failures = [
        f"{pid} ({_short_run(p.get('run_id'))}): {res.error or 'unknown error'}"
        for (pid, p, res) in results
        if not res.ok
    ]
    if failures:
        _die("continue failed:\n  " + "\n  ".join(failures))


_PROBE_KEYS = {"system", "no-system", "prefill", "n", "temperature", "max-tokens",
               "thinking", "panel"}


def _parse_probe_file(text: str) -> tuple[dict, str]:
    """Parse a probe file: an optional `---`-delimited front-matter header of
    `key: value` lines, then the user message verbatim.

    Keys (all optional; they override the battery's command-line defaults for
    this probe only): system, no-system (true/false), prefill, n (int),
    temperature (float), max-tokens (int), thinking (on/off/both), panel
    (comma-separated panel ids). Values may be "quoted" to keep exact
    whitespace. Unknown keys raise ValueError (typo protection — a silently
    dropped `sytem:` would fire the wrong probe). No front-matter → ({}, text).
    One leading blank line after the closing `---` is stripped; the message is
    otherwise byte-exact."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    opts: dict = {}
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            i += 1
            break
        if not line.strip():
            i += 1
            continue
        if ":" not in line:
            raise ValueError(f"front-matter line without ':': {line!r}")
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key not in _PROBE_KEYS:
            raise ValueError(f"unknown front-matter key {key!r} (known: {', '.join(sorted(_PROBE_KEYS))})")
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key == "n":
            opts["n"] = int(val)
        elif key == "temperature":
            opts["temperature"] = float(val)
        elif key == "max-tokens":
            opts["max_tokens"] = int(val)
        elif key == "no-system":
            if val.lower() not in ("true", "false"):
                raise ValueError(f"no-system takes true/false, got {val!r}")
            opts["no_system"] = val.lower() == "true"
        elif key == "thinking":
            if val.lower() not in ("on", "off", "both"):
                raise ValueError(f"thinking takes on/off/both, got {val!r}")
            opts["thinking"] = {"on": True, "off": False, "both": "both"}[val.lower()]
        elif key == "panel":
            opts["panel"] = [p.strip() for p in val.split(",") if p.strip()]
        else:  # system / prefill
            opts[key] = val
        i += 1
    else:
        raise ValueError("unterminated front-matter (no closing '---')")
    body = "\n".join(lines[i:])
    if body.startswith("\n"):
        body = body[1:]
    return opts, body


@app.command("battery")
def cmd_battery(
    probes_dir: str = typer.Argument(..., help="directory of probe files (*.txt), fired in sorted order — each an optional `---` front-matter header (system / no-system / prefill / n / temperature / max-tokens / thinking / panel) + the user message"),
    out: Optional[str] = typer.Option(None, "--out", help="output dir for per-probe JSONL streams (default: <probes_dir>/results)"),
    n: int = typer.Option(1, "--n", help="default samples per panel (front-matter `n:` overrides)"),
    temperature: Optional[float] = typer.Option(None, "--temperature", help="default for probes without `temperature:`; omit = inherit the global param"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", help="default for probes without `max-tokens:`; omit = inherit the global param"),
    thinking: Optional[bool] = typer.Option(None, "--thinking/--no-thinking", help="default thinking mode; omit = inherit the global param"),
    system: Optional[str] = typer.Option(None, "--system", help="default system prompt for probes without `system:`; omit = inherit the global one"),
    no_system: bool = typer.Option(False, "--no-system", help="default to NO system prompt (front-matter `system:`/`no-system:` overrides)"),
    panel: list[str] = typer.Option([], "--panel", help="default target panels (repeatable); front-matter `panel:` overrides"),
    include_folded: bool = typer.Option(False, "--include-folded", help="also fire at browser-folded panels"),
    force: bool = typer.Option(False, "--force", help="fire even while a generation is in flight"),
    first_token: bool = typer.Option(True, "--first-token/--no-first-token", help="print each panel's first-token distribution after every probe (default on)"),
    pause: float = typer.Option(3.0, "--pause", help="seconds to wait between probes"),
) -> None:
    """Fire a DIRECTORY of probe files as sequential `send`s — the reusable probe
    battery. Each probe lands as a new thread at the current panels (layout
    untouched, per-call params — the sidebar is never clobbered); its raw JSONL
    stream (samples + token_logprobs) is written to <out>/<probe-stem>.jsonl and a
    per-panel first-token distribution table prints after each probe. A probe that
    fails doesn't stop the battery; the summary (and exit code) reports it."""
    if no_system and system is not None:
        _die("--system and --no-system are mutually exclusive")
    pdir = Path(probes_dir)
    probes = sorted(pdir.glob("*.txt"))
    if not probes:
        _die(f"no *.txt probe files in {probes_dir}")
    out_dir = Path(out) if out else pdir / "results"
    out_dir.mkdir(parents=True, exist_ok=True)

    defaults = {"system": system, "no_system": no_system, "prefill": None, "n": n,
                "temperature": temperature, "max_tokens": max_tokens,
                "thinking": thinking, "panel": panel}

    # ONE workspace per battery run (§4.4): with nothing open, create it up
    # front (named after the probe dir) and claim the bus — every probe's
    # _send_targets then resolves it, instead of minting one per probe.
    st = _get("/api/state")
    if not st.get("workspace_id"):
        bound = [p for p in st.get("panels", []) if p.get("run_id")]
        if bound:
            _create_send_workspace(f"battery {pdir.name}", bound)

    summary: list[tuple[str, int, list[str]]] = []  # (stem, ok-samples, failures)
    for k, probe in enumerate(probes, 1):
        try:
            opts, message = _parse_probe_file(probe.read_text())
        except ValueError as e:
            _die(f"{probe.name}: {e}")
        if not message.strip():
            _die(f"{probe.name}: empty message body")
        if opts.get("no_system") and "system" in opts:
            _die(f"{probe.name}: `system:` and `no-system: true` are mutually exclusive")
        cfg = {**defaults, **opts}
        # A probe's `no-system: true` beats a battery-level --system default; a
        # probe's `system:` beats a battery-level --no-system.
        sys_prompt = "" if (cfg["no_system"] and "system" not in opts) else cfg["system"]
        # Probes are NEW threads: `system:` authors the THREAD prompt (see
        # _new_thread_system) so each probe's prompt is recorded on its root node.
        sys_prompt, thread_system = _new_thread_system(sys_prompt)
        targets, skipped, conv_id = _send_targets(cfg["panel"], include_folded, force)
        bits = [f"n={cfg['n']}"]
        if thread_system:
            bits.append(f"sys={_oneline(thread_system, 30)!r}")
        elif sys_prompt == "":
            bits.append("system=NONE")
        if cfg["prefill"]:
            bits.append(f"prefill={_oneline(cfg['prefill'], 30)!r}")
        print(f"[{k}/{len(probes)}] {probe.stem}  →  {len(targets)} panel(s)  {' '.join(bits)}"
              + (f"  (skipped: {skipped})" if skipped else ""), flush=True)
        with (out_dir / f"{probe.stem}.jsonl").open("w") as sink:
            results = _fire_send(targets, message, cfg["prefill"], cfg["n"],
                                 cfg["temperature"], cfg["max_tokens"], cfg["thinking"],
                                 sys_prompt, False, True, sink=sink,
                                 thread_system=thread_system, conv_id=conv_id)
        fails = [f"{pid}: {res.error or 'unknown error'}" for pid, _, res in results if not res.ok]
        n_samples = sum(len(res.samples) for _, _, res in results)
        summary.append((probe.stem, n_samples, fails))
        for f in fails:
            print(f"    FAIL {f}", flush=True)
        if first_token:
            _print_first_token_tables([r for r in results if r[2].ok], json_out=False)
        print(flush=True)
        if k < len(probes):
            time.sleep(pause)

    print("battery done:")
    for stem, n_ok, fails in summary:
        print(f"  {stem}: {n_ok} sample(s)" + (f", {len(fails)} FAILED panel(s)" if fails else ""))
    print(f"JSONL per probe in {out_dir}")
    if any(fails for _, _, fails in summary):
        raise typer.Exit(code=1)


@app.command("probe")
def cmd_probe(
    run: str = typer.Argument(..., help="run id / unique substring, `run@checkpoint`, `base:<model>`, `ckpt:<sampler_path>`, or `vllm:<model>` (a model the configured vLLM server lists)"),
    prompt: Optional[str] = typer.Argument(None, help="the user message; omit when using --ancestry-file"),
    n: int = typer.Option(1, "--n", help="samples to draw"),
    temperature: Optional[float] = typer.Option(None, "--temperature"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens"),
    thinking: Optional[bool] = typer.Option(None, "--thinking/--no-thinking", help="thinking renderer (default: inherit the global)"),
    system: Optional[str] = typer.Option(None, "--system", help="system prompt for this call"),
    no_system: bool = typer.Option(False, "--no-system", help="fire with NO system prompt at all"),
    file: Optional[str] = typer.Option(None, "--file", help="read the user message from a file"),
    ancestry_file: Optional[str] = typer.Option(
        None, "--ancestry-file",
        help="JSON list of {role, content} dicts to sample a continuation of — the multi-turn form (a trailing assistant entry acts as a prefill)",
    ),
    prefill: Optional[str] = typer.Option(None, "--prefill", help="assistant prefill the model extends"),
    full: bool = typer.Option(False, "--full", help="print each sample's complete answer + CoT"),
    json_out: bool = typer.Option(False, "--json", help="JSONL to stdout, one object per sample (carries raw_meta)"),
) -> None:
    """Sample ANY discovered model WITHOUT touching the browser or any workspace.

    `chat`/`compare` reshape the panel layout and `send`/`continue` fire at the
    panels as they are — all three can only reach a model some panel is already
    bound to, and all three leave a node behind. `probe` sends `broadcast=false,
    commit=false`: nothing reaches the state bus, nothing is committed to a panel
    transcript, and the samples come back here only.

    That combination is what makes it safe to sample a model the human is not
    looking at. Committing a turn into a panel bound to a DIFFERENT model is
    exactly how a saved tree ends up with turns whose provenance disagrees with
    the panel label, so an off-workspace probe must never write. It also sends
    an explicitly EMPTY thread system prompt — a probe never samples under the
    prompt of whatever thread happens to be open on screen.

    Multi-turn: pass a full verbatim transcript via --ancestry-file (same
    provenance rule as `continue` — reuse generated turns, never author them)."""
    msg = _arg_or_file(prompt, file, "user message", "--file")
    ancestry: list[dict] = []
    if ancestry_file is not None:
        if msg is not None:
            _die("pass EITHER a prompt/--file OR --ancestry-file, not both")
        raw = Path(ancestry_file)
        if not raw.is_file():
            _die(f"ancestry file not found: {ancestry_file}")
        try:
            ancestry = json.loads(raw.read_text())
        except json.JSONDecodeError as e:
            _die(f"--ancestry-file must be a JSON list of {{role, content}} dicts: {e}")
        if not isinstance(ancestry, list) or not ancestry:
            _die("--ancestry-file must be a non-empty JSON list of {role, content} dicts")
        for m in ancestry:
            if not isinstance(m, dict) or "role" not in m or "content" not in m:
                _die("--ancestry-file entries must each be a {role, content} dict")
    elif msg is None:
        _die("pass a prompt, --file, or --ancestry-file")
    else:
        ancestry = [{"role": "user", "content": msg}]
    if prefill:
        if ancestry[-1].get("role") == "assistant":
            _die("--prefill needs the ancestry to end on a user turn (it already ends on an assistant one)")
        ancestry = ancestry + [{"role": "assistant", "content": prefill}]

    run_arg, at_ckpt = _split_run_arg(run)
    body: dict = {
        "messages": ancestry,
        "panel": "p-1",  # required by the schema; commit=false makes the server
                         # panel-route NOTHING to it (P3 review fix — before
                         # that, this literal rebound the real p-1's model)
        "broadcast": False,
        "commit": False,
        # explicit: a probe must never sample under the OPEN thread's prompt
        # (belt-and-braces — new servers don't inherit the mirror at all)
        "thread_system_prompt": "",
        **_call_params(n, temperature, max_tokens, thinking, _resolve_sys(system, no_system)),
    }
    if run_arg.startswith("openrouter:"):
        body["openrouter_model"] = run_arg[len("openrouter:"):]
    elif run_arg.startswith("base:"):
        body["base_model"] = run_arg[len("base:"):]
    elif run_arg.startswith("ckpt:"):
        body["sampler_path"] = run_arg[len("ckpt:"):]
    elif run_arg.startswith("vllm:"):
        body["vllm_model"] = run_arg[len("vllm:"):]
    else:
        r = _resolve_run(run_arg)
        _guard_sampleable(r)
        body["run_id"] = r["id"]
        ck = _resolve_checkpoint(r, at_ckpt)
        if ck is not None:
            body["checkpoint"] = ck
    if not json_out:
        label = body.get("run_id") or body.get("base_model") or body.get("sampler_path") or body.get("openrouter_model")
        ckpt = body.get("checkpoint")
        print(f"probe (off-workspace, nothing written)  n={n}  →  {_short_run(label)}"
              + (f"@{ckpt}" if ckpt else ""), file=sys.stderr)
    _stream_chat(body, stream_inline=(n == 1), json_out=json_out, sink=None if full else None)


@app.command("params")
def cmd_params(
    temperature: Optional[float] = typer.Option(None, "--temperature"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens"),
    n: Optional[int] = typer.Option(None, "--n", help="default sample count"),
    thinking: Optional[bool] = typer.Option(None, "--thinking/--no-thinking"),
    thinking_both: bool = typer.Option(False, "--thinking-both", help="set the global thinking mode to 'both'"),
    top_p: Optional[float] = typer.Option(None, "--top-p"),
    system: Optional[str] = typer.Option(None, "--system", help="global system prompt"),
    system_file: Optional[str] = typer.Option(None, "--system-file", help="read the global system prompt from a file (mutually exclusive with --system)"),
    clear_system: bool = typer.Option(False, "--clear-system", help="remove the global system prompt"),
    json_out: bool = typer.Option(False, "--json", help="print the resulting global params as JSON"),
) -> None:
    """Show or SET the GLOBAL sampling params (system prompt, temperature, max
    tokens, n, thinking, top-p) — the shared state the browser sidebar shows, and
    what a send/chat/continue inherits for any param it doesn't pass. This is the
    DELIBERATE route for changing them; per-call args on send/chat/continue/compare
    apply to that call only and never touch this state. With no options: just show."""
    system = _arg_or_file(system, system_file, "system prompt", "--system-file")
    if clear_system and system is not None:
        _die("--clear-system and --system/--system-file are mutually exclusive")
    patch: dict = {}
    if temperature is not None:
        patch["temperature"] = temperature
    if max_tokens is not None:
        patch["max_tokens"] = max_tokens
    if n is not None:
        patch["n_samples"] = n
    if thinking_both:
        patch["thinking"] = "both"
    elif thinking is not None:
        patch["thinking"] = thinking
    if top_p is not None:
        patch["top_p"] = top_p
    if system is not None:
        patch["system_prompt"] = system
    elif clear_system:
        patch["system_prompt"] = None
    st = _post("/api/state", patch) if patch else _get("/api/state")
    verb = "set" if patch else "current"
    if json_out:
        keys = ("temperature", "max_tokens", "n_samples", "thinking", "top_p", "system_prompt", "system_enabled")
        _print_json({k: st.get(k) for k in keys})
        return
    think = st.get("thinking")
    think_s = "both" if think == "both" else ("yes" if think else "no")
    print(f"{verb} global params   temp={st.get('temperature')} max_tokens={st.get('max_tokens')} "
          f"n={st.get('n_samples')} thinking={think_s}"
          + (f" top_p={st.get('top_p')}" if st.get("top_p") is not None else ""))
    sp = st.get("system_prompt")
    muted = " (muted — browser power toggle off; kept but not applied)" if sp and st.get("system_enabled") is False else ""
    print(f"system: {sp}{muted}" if sp else "system: (none)")


@app.command("url")
def cmd_url(
    selector: Optional[str] = typer.Argument(None, help="workspace id-prefix or name substring → a link that OPENS it (`?w=<id>`); omit → the server's base URL alone"),
    ws_opt: Optional[str] = typer.Option(None, "--ws", "--conv", help="same as the positional selector"),
    live: bool = typer.Option(False, "--live", help="link to the workspace the browser currently has open (from the state bus) instead of naming one"),
    json_out: bool = typer.Option(False, "--json", help="url + resolved instance (pid, scan roots) + workspace id/name"),
) -> None:
    """Print the URL of the server this CLI is driving — the thing to hand the
    human when they ask for a link.

    Every other command auto-discovers the running instance and then keeps the URL
    to itself, so 'give them a link' used to mean `ps aux | grep tinkerscope` and
    guessing which of several instances holds the workspaces you just read.

    stdout is ONLY the URL, so `open $(tinkpg url)` works; the resolved workspace
    name goes to stderr. With a selector (or --live) it emits a `?w=<id>` deep
    link, which opens the workspace but points at no particular turn — for that,
    `grep --link` / `node --link` emit the `?w=…&panel=…&node=…` form."""
    base = _base_url()
    ws: Optional[dict] = None
    sel = _one_selector(selector, ws_opt)
    if live and sel is not None:
        _die("--live and a workspace selector are mutually exclusive: --live means 'whatever the browser has open'")
    if live:
        cid = _get("/api/state").get("workspace_id")
        if not cid:
            _die("no workspace open in the browser (state has no workspace_id) — name one, or omit --live for the base URL.")
        ws = next((c for c in _workspaces() if c.get("id") == cid), None)
        if ws is None:
            # Unsaved draft: the id still opens in the browser, so the link is
            # useful even though we can't name it.
            ws = {"id": cid, "name": "(unsaved draft)"}
    elif sel is not None:
        ws = _resolve_workspace(sel)
    url = f"{base}/?w={ws['id']}" if ws else base
    if json_out:
        payload: dict[str, Any] = {"base_url": base, "url": url}
        if ws:
            payload["workspace_id"] = ws["id"]
            payload["workspace_name"] = ws.get("name")
        payload.update(_instance_info())
        _print_json(payload)
        return
    if ws:
        print(f"{ws.get('name')}  ({ws['id'][:8]})", file=sys.stderr)
    print(url)


def _instance_info() -> dict:
    """pid + scan roots of the discovered instance, for `url --json`. Empty when
    the URL came from --base-url / $TINKERSCOPE_BASE_URL (nothing was discovered,
    and the target may not even be local)."""
    if _BASE_URL_OVERRIDE or os.environ.get("TINKERSCOPE_BASE_URL"):
        return {"discovered": False}
    from .instances import DiscoveryError, discover

    try:
        inst = discover(Path.cwd())
    except DiscoveryError:
        return {"discovered": False}
    return {"discovered": True, "pid": inst.pid, "scan_roots": inst.scan_roots}


def _fmt_ago(ts: Optional[float]) -> str:
    if not ts:
        return "never"
    s = max(0, int(time.time() - ts))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


@app.command("sessions")
def cmd_sessions(
    json_out: bool = typer.Option(False, "--json", help="the raw /api/sessions list"),
) -> None:
    """List the SESSIONS a `--multi-user` server holds — one sidebar (panel
    selection, open workspace, params, running) per browser / `--session` — so
    you can pick which one to drive: `live` = a browser is attached; the human's
    id is on their topbar chip. Workspaces are shared across sessions, so
    `tinkpg ws` / `grep` / `samples` need no session; `state`, `send`, `params`,
    `open`, `continue`, `wait` act on ONE. A single-user server lists just
    `default` (every client drives it)."""
    sessions = _get("/api/sessions")
    if json_out:
        _print_json(sessions)
        return
    health = _get("/api/health")
    if not health.get("multi_user"):
        print("single-user server (started without --multi-user): every client drives the one `default` session")
    names = {c.get("id"): c.get("name") for c in _get("/api/workspaces")}
    for s in sessions:
        wid = s.get("workspace_id")
        where = f"{names.get(wid, '(unsaved draft)')} ({wid[:8]})" if wid else "(no workspace open)"
        live = f"live ×{s['subscribers']}" if s.get("subscribers") else "idle"
        flags = "running" if s.get("running") else ""
        print(f"{s['id']:<20} {live:<9} {flags:<8} {where}   last seen {_fmt_ago(s.get('last_seen'))}")
    if health.get("multi_user"):
        print("\n(drive one: `tinkpg --session <id> …`, or export TINKERSCOPE_SESSION=<id>)")


@app.command("state")
def cmd_state(
    full: bool = typer.Option(False, "--full", help="show every message per panel, not just first/last-2"),
    width: int = typer.Option(160, "--width", help="per-message truncation width"),
    link: bool = typer.Option(True, "--link/--no-link", help="resolve the open workspace from the saved store — its name, folds and the panel transcripts all come from there (`--no-link` skips that fetch: model bindings only, transcripts marked as skipped)"),
    json_out: bool = typer.Option(False, "--json", help="raw state JSON (untruncated escape hatch)"),
    include_folded: bool = typer.Option(
        False, "--include-folded", help="also show panels folded in the browser UI (skipped by default)"
    ),
) -> None:
    """Digest of what's on screen now: one block per panel, first/last-2 of the
    open workspace's saved tree ACTIVE path for that panel (the bus carries
    model bindings only — the tree is the single transcript source). Panels
    folded in the browser UI are skipped (one-line stub) — --include-folded
    expands them. Branches themselves: see `ws`."""
    st = _get("/api/state")
    if json_out:
        print(json.dumps(st, indent=2, default=str, ensure_ascii=False))
        return
    base = _base_url()
    print(
        f"live playground   {base}   running={'yes' if st.get('running') else 'no'}   "
        f"temp={st.get('temperature')} max_tokens={st.get('max_tokens')} "
        f"n={st.get('n_samples')} thinking={'both' if st.get('thinking') == 'both' else 'yes' if st.get('thinking') else 'no'}"
    )
    if st.get("system_prompt"):
        muted = " (muted)" if st.get("system_enabled") is False else ""
        print(f"system: {_oneline(st['system_prompt'], 200)}{muted}")
    panels = st.get("panels", [])
    conv_id = st.get("workspace_id")
    # The workspaces fetch is what makes transcripts possible AT ALL (P3: the
    # bus carries model bindings only; the saved tree is the single transcript
    # source). Nothing to fetch when no workspace is open.
    convs = _workspaces() if (link and conv_id) else []
    # Fold info lives only in the saved workspace (the state bus has no
    # reduced_panels), so folded-panel skipping needs the browser-pushed
    # workspace_id + the (default) --link fetch; without either, all panels show.
    reduced: set[str] = set()
    open_conv: Optional[dict] = None
    if conv_id:
        open_conv = next((c for c in convs if c.get("id") == conv_id), None)
        if open_conv:
            print(f"open workspace: {open_conv.get('name')} ({conv_id[:8]})   → `tinkpg ws {conv_id[:8]}`")
            reduced = set(open_conv.get("reduced_panels") or [])
        elif link:
            print(f"open workspace: {conv_id[:8]} (unsaved draft / not in saved set)")
        else:
            print(f"open workspace: {conv_id[:8]}   (--no-link: name + folds not resolved)")
        print(f"   link: {base}/?w={conv_id}")
    print(f"{len(panels)} panel(s):\n")
    skipped: list[str] = []
    for p in panels:
        # The open workspace's saved tree is the ONLY transcript source (the
        # bus panel is bindings + thread-system mirror; the echo retired with
        # P3). msgs=None means "no source", distinct from an empty thread —
        # printing that as "(0 msgs)" was the review's --no-link lie.
        msgs: Optional[list[dict]] = None
        if open_conv is not None:
            t = (open_conv.get("trees") or {}).get(p["id"])
            msgs = [
                {"role": nd.get("role"), "content": nd.get("content", ""),
                 **({"reasoning": nd["reasoning"]} if nd.get("reasoning") else {})}
                for nd in _active_path(t)
            ] if t and t.get("nodes") else []
        bind = _short_run(p.get("run_id")) + (f"@{p['checkpoint']}" if p.get("checkpoint") else "")
        if p["id"] in reduced and not include_folded:
            skipped.append(p["id"])
            print(f"▸ {p['id']}  {bind}   (folded — --include-folded to expand)")
            print()
            continue
        if msgs is None:
            why = ("transcript skipped: --no-link" if conv_id and not link
                   else "no transcript: workspace not in saved set" if conv_id
                   else "no transcript: no workspace open")
            print(f"▸ {p['id']}  {bind}   ({why})")
        else:
            print(f"▸ {p['id']}  {bind}   ({len(msgs)} msgs)")
        if p.get("thread_system_prompt"):
            print(f"   thread system: {_oneline(p['thread_system_prompt'], 200)}")
        for line in _digest(msgs or [], _fmt_msg, full, width):
            print(line)
        print()
    if skipped:
        print(f"{len(skipped)} folded panel(s) skipped: {', '.join(skipped)}   (--include-folded to expand)")
    print("(branch trees: `tinkpg ws <id|name>`   ·   raw: `tinkpg state --json`)")


def _workspaces() -> list[dict]:
    """Fetch all saved workspace trees for this scan-root set.

    `?bodies=1` because every CLI consumer (link-by-active-path, browse, resolve)
    reads the trees; the bare endpoint returns blob-less summaries (storage v2)."""
    return _get("/api/workspaces?bodies=1")


def _resolve_workspace(sel: str, convs: Optional[list[dict]] = None) -> dict:
    """Resolve a workspace by exact id, else id-prefix, else unique
    case-insensitive name substring. Ambiguity / no-match errors out."""
    convs = convs if convs is not None else _workspaces()
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


def _split_node_handle(handle: str) -> tuple[Optional[str], Optional[str], str]:
    """Parse a node handle into (workspace, panel, node).

    The browser's Copy-node-id button hands out `<panel>:<node>` — a bare node id
    is ambiguous whenever a tree was cloned across panels, and the panel is what
    `continue`/`samples` need in order to loom into the right column. Colon is a
    safe separator: no panel id (`p-1`, and the older `primary`/`compare`) and no
    node id contains one.

        n4f1                  → (None, None,      'n4f1')   # still accepted
        p-4:n4f1              → (None, 'p-4',     'n4f1')
        ws8chars:p-4:n4f1     → ('ws8chars', 'p-4', 'n4f1')

    Anything with more parts is a typo, not a deeper address — say so rather than
    guessing which piece is the id."""
    parts = handle.split(":")
    if len(parts) > 3:
        _die(f"can't read {handle!r} as a node handle — expected <node>, <panel>:<node>, or <ws>:<panel>:<node>")
    # An EMPTY part is a truncated paste, and each one fails confusingly on its own:
    # a blank node id turns into "ambiguous — N matches" + a 20-row dump, a blank
    # panel into zero hits that never mention a filter was applied.
    if any(not x for x in parts):
        _die(f"{handle!r} has an empty part — truncated paste? expected <node>, <panel>:<node>, or <ws>:<panel>:<node>")
    if len(parts) == 1:
        return None, None, parts[0]
    if len(parts) == 2:
        return None, parts[0], parts[1]
    return parts[0], parts[1], parts[2]


def _aim_at_node(
    handle: Optional[str], panel: Optional[str], selector: Optional[str]
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Apply a `panel:node` handle's parts as defaults for --panel / the workspace.
    An explicit flag always wins (the handle is a convenience, not an override), so
    `--node p-4:n1 --panel p-2` reads as a deliberate cross-panel aim."""
    if handle is None:
        return None, panel, selector
    ws_part, panel_part, node = _split_node_handle(handle)
    # Panel: a flag that disagrees is a coherent cross-panel aim, so it just wins.
    # Workspace: a disagreement is almost certainly a stale paste, so it errors —
    # same rule as `_one_selector`, and for the same reason.
    if ws_part is not None and selector is not None and ws_part != selector:
        _die(f"handle names workspace {ws_part!r} but --ws says {selector!r} — pass one")
    return node, panel or panel_part, selector or ws_part


def _one_selector(positional: Optional[str], ws_opt: Optional[str]) -> Optional[str]:
    """Accept a workspace either positionally or as --ws, so muscle memory from
    `grep`/`node`/`threads` (option-only, their positional is taken) carries over
    to `ws`/`samples` (positional). Both given and disagreeing is a typo, not a
    preference — say so instead of silently picking one."""
    if positional is not None and ws_opt is not None and positional != ws_opt:
        _die(f"got two different workspaces: {positional!r} (positional) and {ws_opt!r} (--ws) — pass one")
    return positional if positional is not None else ws_opt


def _list_workspaces(convs: list[dict]) -> None:
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
    print(f"\n{len(rows)} workspace(s)   (expand: `tinkpg ws <id|name>`)")


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
        if nd.get("system_prompt"):
            out.append(f"      sys: {_oneline(nd['system_prompt'], max(20, width - 34))}")
    return out


def _deepest_path(tree: dict, root_id: str) -> list[dict]:
    """The LONGEST root→leaf path in `root_id`'s subtree, ignoring the selection.
    A thread's deepest branch is usually the real conversation — the human keeps
    chatting down one branch and re-rolls elsewhere, and the selected child
    (default: newest) often points at a fresh 1-turn re-roll instead."""
    nodes = tree.get("nodes", {})
    best: list[dict] = []

    def walk(nid: str, path: list[dict], seen: set) -> None:
        nonlocal best
        node = nodes.get(nid)
        if node is None or nid in seen:
            return
        path = path + [node]
        if len(path) > len(best):
            best = path
        for cid in node.get("children", []):
            walk(cid, path, seen | {nid})

    walk(root_id, [], set())
    return best


def _uturns(path: list[dict]) -> int:
    return sum(1 for n in path if n.get("role") == "user")


def _panel_threads(c: dict, pid: str, tree: dict) -> list[dict]:
    """One row per root thread of a panel: turn counts on the selected path AND
    on the deepest branch, plus locators to read/aim at it."""
    layout = {p["id"]: p for p in (c.get("panels") or [])}
    lay = layout.get(pid, {})
    model = _short_run(lay.get("run_id")) + (f"@{lay['checkpoint']}" if lay.get("checkpoint") else "")
    sel_root = _selected_child(tree, ROOT)
    nodes = tree.get("nodes", {})
    rows = []
    for k, rid in enumerate(tree.get("rootChildren", []), 1):
        nd = nodes.get(rid)
        if nd is None:
            continue
        deep = _deepest_path(tree, rid)
        rows.append({
            "ws": c.get("name") or "?",
            "ws_id": (c.get("id") or "")[:8],
            "panel": pid,
            "model": model,
            "k": k,
            "turns": _uturns(_thread_path(tree, rid)),
            "deep": _uturns(deep),
            "samples": len(nd.get("children", [])),
            "active": rid == sel_root,
            "first": nd.get("content", ""),
            "leaf": (deep[-1].get("id") if deep else rid),
        })
    return rows


@app.command("threads")
def cmd_threads(
    min_turns: int = typer.Option(1, "--min-turns", help="only threads whose DEEPEST branch has ≥N user turns (2+ = multi-turn)"),
    ws: Optional[str] = typer.Option(None, "--ws", help="restrict to one workspace (id-prefix or name substring)"),
    model: Optional[str] = typer.Option(None, "--model", help="only panels whose model/checkpoint contains this substring"),
    grep: Optional[str] = typer.Option(None, "--grep", help="only threads whose FIRST message contains this text (case-insensitive)"),
    width: int = typer.Option(72, "--width", help="first-message truncation width"),
    include_folded: bool = typer.Option(True, "--include-folded/--no-folded", help="include panels folded in the browser (default: yes — folding is a view choice, not a filter)"),
    json_out: bool = typer.Option(False, "--json", help="emit rows as JSON (untruncated first messages)"),
) -> None:
    """Cross-workspace index of every root THREAD — the find primitive for
    "where are my multi-turn conversations?".

    One row per thread with its turn count on the DEEPEST branch (`deep`) as well
    as on the selected one (`turns`) — a long conversation often sits on a branch
    the panel no longer points at, so `ws`/`state` alone never show it. Filter with
    --min-turns / --model / --grep, then read one in full with
    `tinkpg ws <ws_id> --panel <panel> --thread <k> --full --deepest`."""
    rows: list[dict] = []
    convs = _workspaces()
    if ws:
        convs = [_resolve_workspace(ws, convs)]
    for c in convs:
        reduced = set(c.get("reduced_panels") or [])
        for pid, tree in (c.get("trees") or {}).items():
            if pid in reduced and not include_folded:
                continue
            rows.extend(_panel_threads(c, pid, tree))
    if model:
        rows = [r for r in rows if model.lower() in r["model"].lower()]
    if grep:
        rows = [r for r in rows if grep.lower() in (r["first"] or "").lower()]
    rows = [r for r in rows if r["deep"] >= min_turns]
    rows.sort(key=lambda r: r["deep"], reverse=True)
    if json_out:
        _print_json(rows)
        return
    if not rows:
        print("no threads matched  (loosen --min-turns / --model / --grep)")
        return
    table = [
        {
            "ws": _oneline(r["ws"], 24),
            "ws_id": r["ws_id"],
            "panel": r["panel"],
            "model": _oneline(r["model"], 34),
            "k": ("*" if r["active"] else "") + str(r["k"]),
            "deep": r["deep"],
            "turns": r["turns"],
            "n": r["samples"],
            "first message": _oneline(r["first"], width),
        }
        for r in rows
    ]
    _print_table(table, ["ws", "ws_id", "panel", "model", "k", "deep", "turns", "n", "first message"])
    print(
        f"\n{len(rows)} thread(s)   deep = user turns on the DEEPEST branch, turns = on the selected one, "
        "* = active\nread one: `tinkpg ws <ws_id> --panel <panel> --thread <k> --deepest --full`"
    )


def _resolve_walk(tree: dict, thread: Optional[int], deepest: bool, pid: str) -> tuple[list[dict], str]:
    """The transcript a `ws` invocation asks for: which root thread (--thread K,
    else the selected one) walked which way (--deepest, else the selection).
    Returns (path, label)."""
    roots = tree.get("rootChildren", [])
    if thread is not None:
        if not 1 <= thread <= len(roots):
            _die(f"panel {pid} has {len(roots)} thread(s); --thread {thread} out of range")
        root_id = roots[thread - 1]
    else:
        root_id = _selected_child(tree, ROOT)
    path = (_deepest_path(tree, root_id) if deepest else _thread_path(tree, root_id)) if root_id else []
    label = "active" if thread is None else f"thread {thread}"
    return path, label + (", deepest branch" if deepest else "")


def _workspace_json(
    c: dict, panel: Optional[str], include_folded: bool, thread: Optional[int], deepest: bool
) -> dict:
    """The selected transcript(s) as STRUCTURED data — untruncated content, CoT,
    node ids and fork position. This is the export path for rendering a
    conversation elsewhere (a report, an artifact, a diff); the text views are
    for reading in a terminal and are lossy by design."""
    trees = c.get("trees") or {}
    layout = {p["id"]: p for p in (c.get("panels") or [])}
    reduced = set(c.get("reduced_panels") or [])
    panels_out = []
    for pid, t in trees.items():
        if panel and pid != panel:
            continue
        if pid in reduced and not include_folded and not panel:
            continue
        lay = layout.get(pid, {})
        path, which = _resolve_walk(t, thread, deepest, pid)
        roots = t.get("rootChildren", [])
        root_id = _root_of(t, path[0]["id"]) if path else None
        msgs = []
        for nd in path:
            sibs = _siblings(t, nd)
            msgs.append({
                "id": nd.get("id"),
                "role": nd.get("role"),
                "content": nd.get("content"),
                "reasoning": nd.get("reasoning") or None,
                "sibling_index": (sibs.index(nd["id"]) + 1) if nd.get("id") in sibs else None,
                "n_siblings": len(sibs),
                "system_prompt": nd.get("system_prompt"),
            })
        panels_out.append({
            "panel": pid,
            "model": _short_run(lay.get("run_id")) + (f"@{lay['checkpoint']}" if lay.get("checkpoint") else ""),
            "run_id": lay.get("run_id"),
            "checkpoint": lay.get("checkpoint"),
            "folded": pid in reduced,
            "walk": which,
            "thread_k": (thread if thread is not None else (roots.index(root_id) + 1 if root_id in roots else None)),
            "n_threads": len(roots),
            "user_turns": _uturns(path),
            "messages": msgs,
        })
    if panel and not panels_out:
        _die(f"workspace has no panel {panel!r}; panels: {', '.join(trees) or '(none)'}")
    return {
        "id": c.get("id"),
        "name": c.get("name"),
        "updated_at": c.get("updated_at"),
        "system_prompt": c.get("system_prompt"),
        "system_enabled": c.get("system_enabled"),
        "panels": panels_out,
    }


def _show_workspace(
    c: dict, panel: Optional[str], full: bool, show_tree: bool, width: int, include_folded: bool = False,
    thread: Optional[int] = None, deepest: bool = False,
) -> None:
    trees = c.get("trees") or {}
    layout = {p["id"]: p for p in (c.get("panels") or [])}
    reduced = set(c.get("reduced_panels") or [])
    upd = (c.get("updated_at") or "")[:19].replace("T", " ")
    print(f"workspace: {c.get('name')}  ({c.get('id')})   updated {upd}")
    if c.get("system_prompt"):
        muted = " (muted)" if c.get("system_enabled") is False else ""
        print(f"system: {_oneline(c['system_prompt'], 200)}{muted}")
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
        ap, which = _resolve_walk(t, thread, deepest, pid)
        nf = sum(1 for nd in ap if len(_siblings(t, nd)) > 1)
        print(f"▸ {pid}  ← {bind}   ({which}: {len(ap)} msgs, {nf} fork{'' if nf == 1 else 's'} on path)")
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


@app.command("ws")
def cmd_ws(
    selector: Optional[str] = typer.Argument(None, help="workspace id-prefix or name substring; omit to list all"),
    ws_opt: Optional[str] = typer.Option(None, "--ws", "--conv", help="same as the positional selector, for symmetry with `grep`/`node`/`threads` (which can only take it as an option)"),
    panel: Optional[str] = typer.Option(None, "--panel", help="restrict to one panel id (p-1/p-2/… — older workspaces also have primary/compare); overrides folding"),
    full: bool = typer.Option(False, "--full", help="show the whole active path, not just first/last-2"),
    tree: bool = typer.Option(False, "--tree", help="show the full branch tree (all branches), `*` = active"),
    width: int = typer.Option(160, "--width", help="per-message truncation width"),
    include_folded: bool = typer.Option(
        False, "--include-folded", help="also expand panels folded in the browser UI (skipped by default)"
    ),
    thread: Optional[int] = typer.Option(
        None, "--thread", help="walk root thread K (the `threads:` index / `tinkpg threads`) instead of the active one"
    ),
    deepest: bool = typer.Option(
        False, "--deepest", help="walk the thread's LONGEST branch instead of its selected one"
    ),
    json_out: bool = typer.Option(
        False, "--json", help="emit the selected transcript(s) as structured JSON (untruncated content + CoT + node ids)"
    ),
) -> None:
    """Browse saved WORKSPACES (multi-panel, branchable; `conv` is a back-compat alias). No
    selector → list them with branch metadata; a selector → expand its panels'
    active branch + forks, plus a `threads:` index when the panel has multiple
    root threads (branch-from-start first messages). Panels folded in the browser
    UI are skipped by default (shown as a one-line stub) — pass --include-folded
    to expand them too, or --panel to target one.

    --thread K reads a NON-active thread's transcript, and --deepest follows its
    longest branch rather than the selected (newest-child) one — together the way
    to read a conversation that the panel no longer points at. `tinkpg threads`
    finds them. --json exports the same transcript as structured data (for
    rendering a conversation in a report / artifact instead of reading it here)."""
    selector = _one_selector(selector, ws_opt)
    convs = _workspaces()
    if selector is None:
        if json_out:
            _print_json([
                {"id": c.get("id"), "name": c.get("name"), "updated_at": c.get("updated_at"),
                 "panels": list((c.get("trees") or {}))}
                for c in convs
            ])
            return
        _list_workspaces(convs)
        return
    c = _resolve_workspace(selector, convs)
    if json_out:
        _print_json(_workspace_json(c, panel, include_folded, thread, deepest))
        return
    _show_workspace(c, panel, full, tree, width, include_folded, thread, deepest)


# Back-compat alias: `conv` was the primary name before the v1.0.0 workspaces
# rename. Hidden to keep --help tidy; kept because it costs one line and it is in
# muscle memory + old transcripts.
app.command("conv", hidden=True)(cmd_ws)


def _panels_in_display_order(c: dict, trees: dict) -> list[str]:
    """Panel ids in the order their COLUMNS appear on screen.

    `panels` is the layout (display order); `trees` is a dict whose key order is
    just whatever the file was last written with — a restored column, a partial
    upsert or a hand edit puts a key wherever it lands. They used to agree closely
    enough not to matter because the default panel was the reserved name 'primary';
    with monotonic ids there is no privileged name left, so "the first panel" has to
    be asked of the layout or it silently means "the first key in the JSON".

    Trees with no layout row (legacy workspaces store no `panels` at all) keep their
    key order, after the laid-out ones."""
    order = [p["id"] for p in (c.get("panels") or []) if isinstance(p, dict) and p.get("id") in trees]
    return order + [p for p in trees if p not in order]


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
    first_token: bool = False,
    deepest: bool = False,
    this: bool = False,
    export_ancestry: Optional[Path] = None,
) -> None:
    trees = c.get("trees") or {}
    if not trees:
        _die("workspace has no panels")
    reduced = set(c.get("reduced_panels") or [])
    if node is not None:
        # --node pinpoints a fork ANYWHERE in a tree (grep prints the ids) —
        # including forks on non-selected branches that --thread/--turn (which
        # walk selected paths) can never reach.
        if thread is not None or turn is not None or deepest:
            _die("--node is mutually exclusive with --thread/--turn/--deepest (it pinpoints the fork directly)")
        search = [panel] if panel else list(trees)
        found: list[tuple[str, dict]] = []
        for p in search:
            t0 = trees.get(p)
            if t0 is None:
                _die(f"no panel {panel!r}; panels: {', '.join(trees) or '(none)'}")
            found.extend((p, nd_) for nd_ in _node_prefix_hits(t0.get("nodes") or {}, node))
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
                _die(f"node {nd.get('id')} is a root-level assistant — its siblings are whole threads; see `tinkpg ws --tree`")
        else:
            unode = nd
        roots = t.get("rootChildren", [])
        thread_k = _thread_of(t, unode.get("id", ""))
        pos_part = f"node {unode.get('id')}"
    else:
        if panel:
            pid = panel  # explicit --panel always overrides the fold
        else:
            ordered = _panels_in_display_order(c, trees)
            candidates = [p for p in ordered if p not in reduced] or ordered
            pid = candidates[0]  # the LEFTMOST unfolded column
        t = trees.get(pid)
        if t is None:
            _die(f"no panel {panel!r}; panels: {', '.join(trees) or '(none)'}")
        roots = t.get("rootChildren", [])
        path, _ = _resolve_walk(t, thread, deepest, pid)
        if thread is not None:
            thread_k = thread
        else:
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

    if this:
        # --this: the --node id names one SAMPLE — isolate exactly it.
        if node is None or nd.get("role") != "assistant":
            _die("--this needs --node pointing at an ASSISTANT sample id (a user id names the whole fork)")
        sample_k = next((i for i, s in enumerate(samples, 1) if s.get("id") == nd.get("id")), None)
        if sample_k is None:
            _die(f"sample {nd.get('id')} is not among its parent's children — tree inconsistency?")

    if export_ancestry is not None:
        # The shown sample's full transcript, in --ancestry-file's shape. Picks
        # --sample K / --this, else the fork's ACTIVE sibling.
        if sample_k is not None:
            if not (1 <= sample_k <= len(samples)):
                _die(f"--sample {sample_k} out of range (this fork has {len(samples)} sample(s))")
            target_id = samples[sample_k - 1].get("id")
        else:
            target_id = active_id
        if not target_id:
            _die("nothing to export — this fork has no samples")
        chain = _ancestry(t, target_id)
        payload = [{"role": m.get("role"), "content": m.get("content", "")} for m in chain]
        export_ancestry.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {len(payload)}-turn ancestry ({_qualified_handle(c.get('id'), pid, target_id)}) to {export_ancestry}")
        print(f'  loom from it: tinkpg continue "<follow-up>" --ancestry-file {export_ancestry}')
        return

    # --first-token: the per-sample position-0 records live server-side as heavy
    # node blobs (storage v2), not in the light tree — fetch them in one batch.
    firsts: list[Optional[dict]] = []
    if first_token:
        ids = [s.get("id") for s in samples]
        blobs = _post(f"/api/workspaces/{c.get('id')}/node-blobs",
                      {"nodes": [i for i in ids if i]}) or {}
        for nid in ids:
            tlp = (blobs.get(nid) or {}).get("token_logprobs")
            firsts.append(tlp[0] if tlp else None)

    layout = {p["id"]: p for p in (c.get("panels") or [])}
    lay = layout.get(pid, {})
    bind = _short_run(lay.get("run_id")) + (f"@{lay['checkpoint']}" if lay.get("checkpoint") else "")
    # The fan-out's thread system prompt (its root node's) — provenance for the
    # samples being read.
    thread_sys = (nodes.get(_root_of(t, unode.get("id", ""))) or {}).get("system_prompt")

    if json_out:
        shown = [(i, s) for i, s in enumerate(samples, 1) if sample_k is None or i == sample_k]
        if sample_k is not None and not shown:
            _die(f"--sample {sample_k} out of range (this fork has {len(samples)} sample(s))")
        counts, doubled, untagged = _tag_tally([s.get("content", "") for s in samples])
        print(json.dumps({
            "workspace_id": c.get("id"), "workspace_name": c.get("name"),
            "panel": pid, "run_id": lay.get("run_id"), "checkpoint": lay.get("checkpoint"),
            "thread": thread_k, "thread_count": len(roots), "position": pos_part,
            "thread_system": thread_sys,
            "prompt": unode.get("content", ""),
            "tally": {"counts": counts, "doubled_draft": doubled, "untagged": untagged},
            "samples": [
                {"index": i, "id": s.get("id"), "role": s.get("role"), "content": s.get("content", ""),
                 "reasoning": s.get("reasoning"), "active": s.get("id") == active_id,
                 **({"first": firsts[i - 1]} if first_token else {})}
                for i, s in shown
            ],
            **({"first_token": _first_token_dist(firsts)} if first_token else {}),
        }, default=str, ensure_ascii=False))
        return

    print(f"workspace: {c.get('name')}  ({(c.get('id') or '')[:8]})")
    thread_part = f"thread {thread_k or '?'}/{len(roots)}   ·   " if len(roots) > 1 else ""
    print(f"panel {pid}  ← {bind}   ·   {thread_part}{pos_part}   ·   {len(samples)} sample(s)")
    if thread_sys:
        print(f"thread system: {_oneline(thread_sys, 200)}")
    if len(trees) > 1 and panel is None:
        unfolded = [p for p in trees if p not in reduced]
        plist = (", ".join(unfolded) or "none unfolded") + (f" (+{len(trees) - len(unfolded)} folded)" if reduced else "")
        print(f"(panels: {plist} — showing {pid}; --panel to switch)")
    print(f"\n▸ prompt · {_qualified_handle(c.get('id'), pid, unode.get('id', '?'))}:")
    print(_indent(unode.get("content", ""), "   "))

    counts, doubled, untagged = _tag_tally([s.get("content", "") for s in samples])
    if counts or untagged:
        parts = [f"{k} ×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
        if untagged:
            parts.append(f"untagged ×{untagged}")
        tail = f"   ({doubled} doubled-draft)" if doubled else ""
        print(f"\ntally: {' · '.join(parts)}{tail}")
    if first_token:
        dist = _first_token_dist(firsts)
        print()
        print(_fmt_first_token(dist, len(samples)) if dist else
              "first-token distribution: no sample here carries token_logprobs "
              "(OpenRouter/streamed turn, or logprob capture off)")
    print()
    shown = [(i, s) for i, s in enumerate(samples, 1) if sample_k is None or i == sample_k]
    if sample_k is not None and not shown:
        _die(f"--sample {sample_k} out of range (this fork has {len(samples)} sample(s))")
    for i, s in shown:
        active = "*" if s.get("id") == active_id else " "
        print(f"{active}--- sample {i}/{len(samples)} · {_qualified_handle(c.get('id'), pid, s.get('id', '?'))} ---")
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
    ws_opt: Optional[str] = typer.Option(None, "--ws", "--conv", help="same as the positional selector, for symmetry with `grep`/`node`/`threads` (which can only take it as an option)"),
    panel: Optional[str] = typer.Option(None, "--panel", help="panel id (p-1/p-2/… — older workspaces also have primary/compare); default = the LEFTMOST non-folded panel (layout order, i.e. the column order on screen). Explicit --panel overrides folding"),
    thread: Optional[int] = typer.Option(None, "--thread", help="1-indexed root thread (branch-from-start sibling) to walk; default = the active one. Thread numbers: the `threads:` index in `tinkpg ws <id>`"),
    turn: Optional[int] = typer.Option(None, "--turn", help="1-indexed user turn on the thread's path whose responses to show; default = the last one"),
    node: Optional[str] = typer.Option(None, "--node", help="node handle — `<node>`, `<panel>:<node>` or `<ws>:<panel>:<node>` (the browser's Copy-node-id button gives the middle form; `tinkpg grep` prints ids). Pinpoints the fork directly, reaching NON-selected branches --thread/--turn can't. An assistant id shows the fan-out it belongs to"),
    full: bool = typer.Option(False, "--full", help="each sample's COMPLETE answer + full CoT (default: answer + one-line CoT preview)"),
    width: int = typer.Option(240, "--width", help="per-sample truncation width in the default (non --full) view"),
    sample: Optional[int] = typer.Option(None, "--sample", help="show ONLY sibling K (1-indexed) — read one sample at a time"),
    this: bool = typer.Option(False, "--this", help="with an ASSISTANT --node id: show only THAT sample (the Copy-node-id → terminal round-trip in one paste, no counting siblings)"),
    export_ancestry: Optional[Path] = typer.Option(None, "--export-ancestry", metavar="OUT.json", help="write the shown sample's root→sample transcript as a JSON `{role, content}` list — ready for `tinkpg continue --ancestry-file` (picks --sample K / --this, else the active sibling)"),
    slice_spec: Optional[str] = typer.Option(None, "--slice", help="START[:LEN] character window of each shown sample (default LEN 2000) — read long samples in pieces instead of truncating; with --full the same window applies to the CoT"),
    json_out: bool = typer.Option(False, "--json", help="the fork as one JSON object (workspace/panel/thread/prompt/tally/samples) instead of human text — for scripts (--slice is ignored; content is never truncated)"),
    first_token: bool = typer.Option(False, "--first-token", help="the model's probability distribution over the FIRST generated token at this fork (stored top-K + each sample's sampled token — the CLI twin of the chart's first-token mode); with --json, adds per-sample `first` records + the aggregate"),
    deepest: bool = typer.Option(False, "--deepest", help="resolve --turn against the thread's LONGEST branch instead of its selected one — reaches forks deeper than the selection goes"),
) -> None:
    """Show every sibling response (the n-sample fan-out) at ONE fork, each with its
    CoT, plus a `<tag>` verdict tally — the 'what did the model say across all draws
    here' view that `state`/`conv` (active path only) can't give you. With no selector
    it targets the workspace the browser has open (via its pushed workspace_id);
    with no --panel, the leftmost non-folded one. --thread k aims it at a non-active
    root thread (numbers from `tinkpg ws <id>`'s thread index); --node <id> (ids
    from `tinkpg grep`) aims it at ANY fork, even on non-selected branches. Reading
    ergonomics: --sample K isolates one sibling, --slice START[:LEN] pages through it,
    --json for scripts (untruncated content, no need to regex human-formatted text).
    --deepest resolves --turn against the thread's longest branch, so a fork below
    where the selection stops is reachable without hunting for its node id."""
    selector = _one_selector(selector, ws_opt)
    node, panel, selector = _aim_at_node(node, panel, selector)
    convs = _workspaces()
    if selector is not None:
        c = _resolve_workspace(selector, convs)
    else:
        cid = _get("/api/state").get("workspace_id")
        if cid:
            c = next((x for x in convs if x.get("id") == cid), None)
            if c is None:
                _die(f"open workspace {cid[:8]} isn't in the saved set yet (unsaved draft?). save it, or pass a saved id — see `tinkpg ws`.")
        else:
            # Browserless bare --node: search every workspace (self-contained ids).
            c = _find_workspace_holding_node(node) if node is not None else None
            if c is None:
                _die("no workspace open in the browser (state has no workspace_id). pass a workspace id/name — see `tinkpg ws`.")
    slice_rng: Optional[tuple[int, int]] = None
    if slice_spec is not None:
        m = re.fullmatch(r"(\d+)(?::(\d+))?", slice_spec)
        if not m:
            _die("--slice takes START[:LEN] character offsets, e.g. `--slice 2000:1500`")
        slice_rng = (int(m.group(1)), int(m.group(2) or 2000))
    _show_samples(c, panel, turn, full, width, thread, node, sample, slice_rng, json_out,
                  first_token, deepest, this=this, export_ancestry=export_ancestry)


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


@app.command("grep")
def cmd_grep(
    pattern: str = typer.Argument(..., help="text to find (fixed string; --regex for a regex)"),
    conv: Optional[str] = typer.Option(None, "--ws", "--conv", help="restrict to one workspace (id-prefix or name substring)"),
    regex: bool = typer.Option(False, "--regex", help="treat PATTERN as a Python regex"),
    ignore_case: bool = typer.Option(False, "-i", "--ignore-case"),
    width: int = typer.Option(160, "--width", help="snippet width around each match"),
    max_hits: int = typer.Option(200, "--max-hits", help="stop printing after this many hits (count continues)"),
    json_out: bool = typer.Option(False, "--json", help="hits as a JSON array (full match text, not a snippet) instead of human text — for scripts"),
    link: bool = typer.Option(False, "--link", help="append a clickable deep link per hit (?w=…&node=… opens the browser AT the match)"),
) -> None:
    """Search EVERY branch of saved workspaces — message content, thinking
    (`reasoning`) and thread system prompts of all nodes, active or not; the view
    `conv`/`samples` can't give you (they walk selected paths). One line per hit:
    workspace · panel · thread k · role · node id (tagged when the hit is in CoT
    or a system prompt) + snippet. Workspace-LEVEL matches (name / panel model id /
    global system prompt) print first. Drill into a hit with `tinkpg samples <ws>
    --node <id>` (works on non-selected branches too), `samples --panel P
    --thread k`, or `conv --tree`.

    Runs server-side (GET /api/search — the same engine as the browser's Ctrl+K
    palette) instead of pulling every body over ?bodies=1 and scanning here."""
    ws_id: Optional[str] = None
    if conv is not None:
        ws_id = _resolve_workspace(conv, _get("/api/workspaces"))["id"]
    params: dict = {"q": pattern, "regex": int(regex), "case": int(not ignore_case),
                    "max_hits": max_hits, "width": width}
    if ws_id is not None:
        params["ws"] = ws_id
    out = _get("/api/search", params=params)
    if link:
        base = _base_url()
        for h in out["hits"]:
            h["link"] = f"{base}/?w={h['workspace_id']}&panel={h['panel']}&node={h['node_id']}"
        for wh in out["workspace_hits"]:
            wh["link"] = f"{base}/?w={wh['workspace_id']}"
    if json_out:
        print(json.dumps(out, default=str, ensure_ascii=False))
        return
    for wh in out["workspace_hits"]:
        where = {"name": "workspace name", "system": "workspace system prompt",
                 "model": f"panel {wh.get('panel')} model"}.get(wh["field"], wh["field"])
        print(f"{wh['workspace_name']} ({(wh['workspace_id'] or '')[:8]}) · {where}")
        print(f"   {wh['before']}{wh['match_display']}{wh['after']}")
        if link:
            print(f"   ↳ {wh['link']}")
    for h in out["hits"]:
        loc = (f"{h['workspace_name']} · thread {h['thread'] or '?'} · {h['role']} · "
               f"{_qualified_handle(h['workspace_id'], h['panel'], h['node_id'])}")
        tag = {"reasoning": " [thinking]", "system_prompt": " [system]"}.get(h["field"], "")
        print(f"{loc}{tag}")
        print(f"   {h['before']}{h['match_display']}{h['after']}")
        if link:
            print(f"   ↳ {h['link']}")
    total = out["total"]
    if out["truncated"]:
        print(f"\n…{total - len(out['hits'])} more hit(s) not shown (--max-hits to raise)")
    if not total and not out["workspace_hits"]:
        print(f"no matches for {pattern!r} across {out['workspaces_searched']} workspace(s)")
    elif total:
        per_ws = " · ".join(
            f"{t['total']}× {t['workspace_name']}"
            for t in sorted(out["workspace_totals"], key=lambda t: -t["total"]))
        print(f"\n{total} hit(s) in {out['workspaces_matched']} workspace(s): {per_ws}")


@app.command("node")
def cmd_node(
    node_id: str = typer.Argument(..., help="node handle: `<node>`, `<panel>:<node>` or `<ws>:<panel>:<node>` — the browser's Copy-node-id button gives the middle form; `grep`/`samples --json` print bare ids"),
    conv: Optional[str] = typer.Option(None, "--ws", "--conv", help="restrict the search to one workspace (id-prefix or name substring)"),
    logprobs: bool = typer.Option(False, "--logprobs", help="fetch + print the stored per-token logprob blob (index, token, lp, top-K alternatives)"),
    meta: bool = typer.Option(False, "--meta", help="fetch + print the stored raw_meta blob (the request & response record)"),
    raw: bool = typer.Option(False, "--raw", help="print the node's raw_text (tags preserved)"),
    full: bool = typer.Option(False, "--full", help="full content / thinking / prefill instead of one-line previews"),
    json_out: bool = typer.Option(False, "--json", help="the matches as one JSON object (blobs included when --logprobs/--meta; content never truncated) — for scripts"),
    link: bool = typer.Option(False, "--link", help="append a clickable deep link (?w=…&node=… opens the browser AT this node)"),
) -> None:
    """Locate a NODE ID anywhere in the saved workspaces and dump its record —
    the reverse index `grep` (text → ids) can't give you. Takes the id with no
    workspace/panel context needed (the browser's Copy-node-id button hands out
    exactly that), finds every tree holding it, and prints where it lives
    (workspace · panel · thread · sibling k/N) plus the fields the transcript
    views drop: `prefill` (an authored/Continue prefix — the token stream only
    covers what came AFTER it), finish_reason, parent/children, and which heavy
    blobs exist. --logprobs / --meta fetch those blobs (storage v2 keeps them out
    of the tree), --raw prints the raw stream text. Same id in several trees
    (a branch copied across panels/workspaces) prints one block per copy."""
    node_id, want_panel, conv = _aim_at_node(node_id, None, conv)
    if conv is not None:
        target = _resolve_workspace(conv, _get("/api/workspaces"))
        convs = [_get(f"/api/workspaces/{target['id']}")]
    else:
        convs = _workspaces()
    hits: list[tuple[dict, str, dict]] = []  # (workspace, panel, node)
    for c in convs:
        for pid, t in (c.get("trees") or {}).items():
            if want_panel is not None and pid != want_panel:
                continue
            for nid, nd in (t.get("nodes") or {}).items():
                if nid == node_id or nid.startswith(node_id):
                    hits.append((c, pid, nd))
    exact = [h for h in hits if h[2].get("id") == node_id]
    if exact:
        hits = exact
    if not hits:
        # Name the panel filter when one is active: a handle whose panel part is stale
        # reads as "no such node" while the node is sitting in a different column.
        _die(f"no node matching {node_id!r} in {len(convs)} workspace(s)"
             + (f" (restricted to panel {want_panel} by the handle — drop that part to search every panel)"
                if want_panel else "")
             + ("" if conv else " — `tinkpg ws` lists them; --ws to scope"))
    if len({h[2].get("id") for h in hits}) > 1:
        listing = "\n".join(
            f"  - {nd.get('id')}  ·  {c.get('name')} ({(c.get('id') or '')[:8]}) · {pid} · {nd.get('role', '?')}"
            for c, pid, nd in hits[:20]
        )
        _die(f"ambiguous node prefix {node_id!r} — {len(hits)} matches:\n{listing}")

    want_blobs = logprobs or meta
    json_matches: list[dict] = []
    for k, (c, pid, nd) in enumerate(hits):
        t = c["trees"][pid]
        nid = nd.get("id", "")
        blobs: dict = {}
        if want_blobs:
            blobs = (_post(f"/api/workspaces/{c.get('id')}/node-blobs", {"nodes": [nid]}) or {}).get(nid) or {}
        sibs = _siblings(t, nd)
        sib_k = sibs.index(nid) + 1 if nid in sibs else None
        thread_k = _thread_of(t, nid)
        lay = {p["id"]: p for p in (c.get("panels") or [])}.get(pid, {})
        if json_out:
            json_matches.append({
                "workspace_id": c.get("id"), "workspace_name": c.get("name"),
                "panel": pid, "run_id": lay.get("run_id"), "checkpoint": lay.get("checkpoint"),
                "thread": thread_k, "sibling_index": sib_k, "n_siblings": len(sibs),
                **({"link": f"{_base_url()}/?w={c.get('id')}&panel={pid}&node={nid}"} if link else {}),
                "node": nd,
                **({"token_logprobs": blobs.get("token_logprobs")} if logprobs else {}),
                **({"raw_meta": blobs.get("raw_meta")} if meta else {}),
            })
            continue

        if k:
            print()
        bind = _short_run(lay.get("run_id")) + (f"@{lay['checkpoint']}" if lay.get("checkpoint") else "")
        roots = t.get("rootChildren", [])
        print(f"workspace: {c.get('name')}  ({(c.get('id') or '')[:8]})  ·  panel {pid}  ← {bind}")
        loc = (f"node {_qualified_handle(c.get('id'), pid, nid)}  ·  {nd.get('role', '?')}"
               f"  ·  thread {thread_k or '?'}/{len(roots)}")
        if sib_k is not None and len(sibs) > 1:
            loc += f"  ·  sibling {sib_k}/{len(sibs)}"
        loc += f"  ·  parent {nd.get('parent') or '(root)'}  ·  {len(nd.get('children') or [])} child(ren)"
        print(loc)
        if link:
            print(f"↳ {_base_url()}/?w={c.get('id')}&panel={pid}&node={nid}")
        facts = []
        if nd.get("finish_reason"):
            facts.append(f"finish: {nd['finish_reason']}")
        if nd.get("thinking") is not None:
            facts.append(f"thinking: {'on' if nd['thinking'] else 'off'}")
        have = [name for name, flag in (("token_logprobs", "has_token_logprobs"), ("raw_meta", "has_raw_meta"))
                if nd.get(flag) or blobs.get(name)]
        facts.append("blobs: " + (", ".join(have) or "none"))
        print("  ".join(facts))

        def field(label: str, text: Optional[str]) -> None:
            if not text:
                return
            print(f"▸ {label} ({len(text)} chars):")
            print(_indent(text, "   ") if full else _indent(_oneline(text, 300), "   "))

        prefill = nd.get("prefill")
        if prefill:
            note = " — the token stream starts AFTER it" if "token_logprobs" in have else ""
            field(f"prefill{note}", prefill)
        field("thinking", nd.get("reasoning"))
        field("content", nd.get("content"))
        if raw:
            field("raw_text", nd.get("raw_text"))
        if logprobs:
            tlp = blobs.get("token_logprobs")
            if tlp:
                print(f"▸ token_logprobs ({len(tlp)} tokens):")
                print(_fmt_token_logprobs(tlp))
            else:
                print("▸ token_logprobs: none stored for this node")
        if meta:
            if blobs.get("raw_meta"):
                print(f"▸ raw_meta ({len(blobs['raw_meta'])} chars):")
                print(_indent(blobs["raw_meta"], "   "))
            else:
                print("▸ raw_meta: none stored for this node")
    if json_out:
        print(json.dumps({"matches": json_matches, "total": len(json_matches)},
                         default=str, ensure_ascii=False))


@app.command("trash")
def cmd_trash(
    action: str = typer.Argument("list", help="list | restore | purge"),
    handle: Optional[str] = typer.Argument(None, help="restore: an entry id, a deleted branch's root node id, or any node id inside it"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="workspace id-prefix or name substring (default: every workspace, for list)"),
    json_out: bool = typer.Option(False, "--json", help="emit structured JSON"),
) -> None:
    """Recover deleted branches. Every node a save makes disappear is journaled
    server-side, so a delete survives the browser that made it.

    `list` shows what's recoverable (newest first) — you rarely know the id of the
    thing you deleted, so start here. `restore <handle>` splices a branch back at
    its original sibling position, logprobs included (node blobs are write-once and
    were never removed). `purge` forgets a workspace's journal.

    Reload any open browser tab after a restore: the tab still holds the
    post-delete tree and its next save would re-delete the branch."""
    if action not in ("list", "restore", "purge"):
        _die(f"unknown action {action!r} — use list, restore or purge")
    convs = _workspaces()
    targets = [_resolve_workspace(workspace, convs)] if workspace else convs
    if action == "list":
        rows = []
        for c in targets:
            for e in _get(f"/api/workspaces/{c['id']}/trash"):
                roots = e.get("roots") or []
                rows.append({
                    "ws": _oneline(c.get("name") or "?", 22),
                    "entry": e.get("id"),
                    "when": str(e.get("ts") or "")[:19].replace("T", " "),
                    "panel": e.get("panel"),
                    "nodes": e.get("count"),
                    "node_id": roots[0].get("id") if roots else "",
                    "what": _oneline(roots[0].get("preview") or "", 60) if roots else
                            ("whole panel" if e.get("kind") == "panel" else ""),
                    "_ws_id": c["id"],
                })
        rows.sort(key=lambda r: r["when"], reverse=True)
        if json_out:
            _print_json(rows)
            return
        if not rows:
            print("nothing in the trash" + (f" for {targets[0].get('name')!r}" if workspace else ""))
            return
        _print_table(rows, ["ws", "entry", "when", "panel", "nodes", "node_id", "what"])
        print("\nrestore with:  tinkpg trash restore <entry|node_id> -w <workspace>")
        return

    if not workspace:
        _die("--workspace is required for restore/purge (a handle is only unique within one)")
    cid = targets[0]["id"]
    if action == "purge":
        out = _delete(f"/api/workspaces/{cid}/trash")
        print(f"purged the trash journal for {targets[0].get('name')!r}" if out.get("ok") else "nothing to purge")
        return

    if not handle:
        _die("restore needs a handle — run `tinkpg trash list` to find one")
    out = _post(f"/api/workspaces/{cid}/trash/restore", {"handle": handle})
    if json_out:
        _print_json(out)
        return
    if not out.get("ok"):
        _die(out.get("error") or "restore failed")
    n = len(out.get("restored") or [])
    rebuilt = bool(out.get("recreated_panel"))
    where = f"panel {out.get('panel')}"
    if rebuilt:
        where += " (re-added: the whole column had been closed)"
    print(f"restored {n} node(s) into {where} of {targets[0].get('name')!r}")
    if n == 0 and not rebuilt:
        print("(already present — nothing to do)")
    else:
        # Warn on a rebuilt column even at n==0: the nodes can already be back while
        # the LAYOUT ROW was wiped by a stale tab's save, and that tab will wipe it
        # again on its next write.
        print("⚠ reload any open browser tab on this workspace before editing it,")
        print("  or its next save will re-delete what was just restored.")
    if out.get("unbound_panel"):
        print("⚠ this entry predates layout journaling, so the column came back with NO")
        print("  model bound — and the browser drops unbound panels on load. Bind a model")
        print("  to it in the same session, or restore again after adding the panel.")


@app.command("refresh")
def cmd_refresh() -> None:
    """Rescan the filesystem + re-probe sampling capabilities."""
    _print_json(_post("/api/models/refresh"))


@app.command("wait")
def cmd_wait(
    timeout: Optional[float] = typer.Option(None, "--timeout", help="max seconds to wait (default: forever); exit 1 on expiry"),
    poll: float = typer.Option(1.0, "--poll", help="seconds between checks"),
) -> None:
    """Block until no generation is running — the sequential-wave primitive:
    fire, `tinkpg wait`, read, fire again (replaces the sleep/check dance)."""
    start = time.time()
    while True:
        if not _get("/api/state").get("running"):
            return
        if timeout is not None and time.time() - start > timeout:
            _die(f"still running after {timeout:.0f}s")
        time.sleep(poll)


# ---------- serve / pack / site: the merged `tinkerscope` surface ----------
# One binary since 2026-08-12 (Clément's call; samplescope's b2e1818 pattern).
# Everything below LAZY-IMPORTS its machinery — serve pulls uvicorn, pack/site
# pull the state readers — so the driver verbs above stay fast.


@app.command("serve")
def cmd_serve(
    dirs: Optional[list[Path]] = typer.Argument(
        None, help="directories to scan for Tinker runs (default: cwd, or $TINKERSCOPE_SCAN_ROOTS)"
    ),
    port: Optional[int] = typer.Option(None, "--port", help="port to bind (default: first free port from 8765)"),
    host: str = typer.Option(
        os.environ.get("TINKERSCOPE_HOST", "127.0.0.1"), "--host", help="host to bind"
    ),
    reload: bool = typer.Option(False, "--reload", help="dev mode: auto-reload on source change"),
    pack: Optional[str] = typer.Option(
        None, "--pack", metavar="FILE_OR_URL",
        help="apply a share pack (local path or http(s) URL) to this folder's state before serving",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="with --pack: also overwrite existing default params/layout (default: keep them if the folder was already used)",
    ),
    reseed: bool = typer.Option(
        False, "--reseed",
        help="with --pack: fully rebuild the pack's workspaces (delete + re-import, so re-exported raw_meta/logprob blobs refresh and dropped nodes are removed) and overwrite default params — for iterating on a pack you keep re-exporting (implies --force)",
    ),
    vllm_url: Optional[str] = typer.Option(
        None, "--vllm-url", metavar="URL",
        help="a vLLM (OpenAI-compatible) server whose served models join the picker as `vllm:<name>` — sampled natively (token ids, logprobs, loom); same as $TINKERSCOPE_VLLM_URL",
    ),
    multi_user: bool = typer.Option(
        False, "--multi-user",
        help="several people on this one instance: each browser (and each `tinkpg --session <id>`) gets its OWN sidebar — panel selection, open workspace, sampling params, running — while workspaces, highlights and pins stay shared; same as TINKERSCOPE_MULTI_USER=1",
    ),
) -> None:
    """Serve the API + web UI for DIRS (bare `tinkerscope <dir>` is shorthand for this)."""
    from .serve import run_server  # lazy: keeps uvicorn off the driver-verb hot path

    run_server(dirs or None, host=host, port=port, reload=reload,
               pack=pack, force=force, reseed=reseed, vllm_url=vllm_url,
               multi_user=multi_user)


pack_app = typer.Typer(
    add_completion=False, no_args_is_help=True,
    help="Author share packs (portable YAML bundles of checkpoints + params + workspaces).",
)
app.add_typer(pack_app, name="pack")


@pack_app.command("export")
def cmd_pack_export(
    out: Path = typer.Argument(..., help="output pack file (.yaml); if it exists, merges into it unless --overwrite"),
    dir: Optional[list[Path]] = typer.Option(
        None, "--dir",
        help="scan root(s) whose state to export (default: cwd) — must match how the instance was launched",
    ),
    name: Optional[str] = typer.Option(None, "--name", help="pack name (default: kept from an existing file, else the dir name)"),
    description: Optional[str] = typer.Option(None, "--description"),
    models_from: str = typer.Option(
        "all", "--models-from", metavar="panels|workspaces|all|runs",
        help="where to gather models (default: all = current panels + workspaces + already-registered pack models)",
    ),
    include_model: Optional[list[str]] = typer.Option(
        None, "--include-model", metavar="SUBSTR", help="keep only models whose label/ref matches (repeatable)"
    ),
    exclude_model: Optional[list[str]] = typer.Option(
        None, "--exclude-model", metavar="SUBSTR", help="drop models whose label/ref matches (repeatable)"
    ),
    no_workspaces: bool = typer.Option(False, "--no-workspaces", help="exclude saved workspaces"),
    no_defaults: bool = typer.Option(
        False, "--no-defaults", help="omit the defaults block (sampling params + default panel layout) from the pack"
    ),
    workspace: Optional[list[str]] = typer.Option(
        None, "--workspace", metavar="NAME", help="include only these workspaces by name (repeatable)"
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="regenerate from scratch instead of merging into an existing file"
    ),
    logprobs: bool = typer.Option(
        False, "--logprobs",
        help="include per-token logprobs (the token inspector + first-token chart). "
             "Large: give `out` a .gz suffix to compress (107 MB -> 30 MB on a real workspace)",
    ),
) -> None:
    """Export the current setup to a pack YAML file."""
    if models_from not in ("panels", "workspaces", "all", "runs"):
        _die(f"--models-from must be one of panels|workspaces|all|runs, got {models_from!r}")
    from .publish_cli import run_pack_export  # lazy: keeps state readers off the hot path

    run_pack_export(
        out, dirs=dir, name=name, description=description, models_from=models_from,
        include=include_model, exclude=exclude_model, workspaces=not no_workspaces,
        workspace_names=workspace, include_defaults=not no_defaults,
        include_logprobs=logprobs, overwrite=overwrite,
    )


site_app = typer.Typer(
    add_completion=False, no_args_is_help=True,
    help="Export a read-only static site (built SPA + baked JSON) for GitHub Pages or any file host.",
)
app.add_typer(site_app, name="site")


@site_app.command("export")
def cmd_site_export(
    out: Path = typer.Argument(..., help="output directory (created; its data/ and _app/ are replaced)"),
    dir: Optional[list[Path]] = typer.Option(
        None, "--dir",
        help="scan root(s) whose state to export (default: cwd) — must match how the instance was launched",
    ),
    title: Optional[str] = typer.Option(None, "--title", help="site title, shown in the read-only badge (default: the dir name)"),
    description: Optional[str] = typer.Option(None, "--description"),
    workspace: Optional[list[str]] = typer.Option(
        None, "--workspace", metavar="NAME", help="include only these workspaces by name (repeatable)"
    ),
    open_ws: Optional[str] = typer.Option(
        None, "--open", metavar="WS_ID", help="workspace id to open by default (default: the first exported one)"
    ),
    pack_url: Optional[str] = typer.Option(
        None, "--pack-url", metavar="URL",
        help='where the same content is published as a share pack — the site\'s "open this locally" panel turns it into a runnable command',
    ),
    pack_link: Optional[list[str]] = typer.Option(
        None, "--pack-link", metavar="URL|PATH=URL",
        help="a pack this site should be able to INSTALL on demand, so a ?w=<id> link is shareable: "
             "a visitor who lacks that workspace fetches the pack instead of falling back to the newest "
             "one. Repeatable. Use PATH=URL when the file is local and not yet uploaded (path read for "
             "the ids, URL fetched by visitors). Implies --pack-url when given exactly once",
    ),
    logprobs: Optional[str] = typer.Option(
        None, "--logprobs", metavar="WHICH",
        help="which turns keep per-token logprobs: all (default) | chart (only the turn each "
             "workspace's saved chart view points at) | last:N (newest N turns per thread) | none. "
             "They are ~97% of a site's bytes, and the token inspector + first-token chart are what they buy",
    ),
    no_logprobs: bool = typer.Option(False, "--no-logprobs", help="alias for --logprobs none"),
    # Tri-state. Pins have no workspace id, so a --workspace filter can't scope them:
    # defaulting them ON would make a curated export ship saved samples (and their
    # local dataset paths) from the workspaces you filtered OUT.
    pins: Optional[bool] = typer.Option(
        None, "--pins/--no-pins",
        help="--no-pins excludes saved pins (default: included, EXCEPT when --workspace filters the "
             "export); --pins includes them even then (they can't be filtered per-workspace)",
    ),
    web_dist: Optional[Path] = typer.Option(
        None, "--web-dist",
        help="built frontend to publish (default: this install's web/dist, else the packaged copy)",
    ),
) -> None:
    """Write a self-contained static site into a directory."""
    from .publish_cli import run_site_export  # lazy: keeps state readers off the hot path

    run_site_export(
        out, dirs=dir, title=title, description=description, workspace_names=workspace,
        open_ws=open_ws, pack_url=pack_url, pack_link=pack_link, logprobs=logprobs,
        no_logprobs=no_logprobs, pins=pins, web_dist=web_dist,
    )


def main() -> None:
    """Entry point shim used when invoked as a module."""
    app()


if __name__ == "__main__":
    main()
