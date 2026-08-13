"""Generate the CLI command reference from the Typer app itself.

Two generated blocks, spliced between BEGIN/END markers (samplescope's
`_gen_cli_ref` pattern, ported 2026-08-12 with the unification):

- **README.md** gets the COMPACT table — one line per command, no options.
  The README is a human pitch; per-flag notes belong in the skill (Clément
  2026-08-05), and a hand-written table is exactly what drifted before.
- **plugin/skills/cli/SKILL.md** gets the FULL reference — command line,
  one-line help, per-option help — appended as its "Command reference"
  section. The skill's workflow prose stays hand-written; the flag surface
  is derived from the same `typer.Option(help=...)` strings `--help` shows,
  so it cannot drift. `tests/test_cli_docs.py` fails when a CLI change lands
  without rerunning this.

Display names: driver verbs print as `tinkpg <verb>` (the alias every doc and
muscle memory uses); `serve`/`pack`/`site` print as `tinkerscope …` (the
server-side surface — same binary, two names).

Usage:
    python -m tinkerscope._gen_cli_ref            # rewrite marked blocks in place
    python -m tinkerscope._gen_cli_ref --check    # exit 1 if any block is stale
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import typer
# typer 0.26+ vendors click as `typer._click`; the command tree from
# `typer.main.get_command` is built from typer.core's Typer* subclasses, so
# isinstance checks against the standalone `click` package silently fail.
from typer._click import Context
from typer.core import TyperArgument, TyperGroup, TyperOption

BEGIN_FULL = "<!-- BEGIN GENERATED: tinkerscope-cli-reference (python -m tinkerscope._gen_cli_ref) -->"
END_FULL = "<!-- END GENERATED: tinkerscope-cli-reference -->"
BEGIN_COMPACT = "<!-- BEGIN GENERATED: tinkerscope-cli-table (python -m tinkerscope._gen_cli_ref) -->"
END_COMPACT = "<!-- END GENERATED: tinkerscope-cli-table -->"

_REPO = Path(__file__).resolve().parents[2]
FULL_TARGET = _REPO / "plugin" / "skills" / "cli" / "SKILL.md"
COMPACT_TARGET = _REPO / "README.md"

# Sub-trees displayed under the `tinkerscope` name (the server-side surface).
_SERVER_SIDE = {"serve", "pack", "site"}


def _prefix_for(top_name: str) -> str:
    return "tinkerscope" if top_name in _SERVER_SIDE else "tinkpg"


def _metavar(p) -> str:
    if p.metavar:
        return p.metavar
    return getattr(p.type, "name", "value").upper()


def _fmt_option(p: TyperOption) -> str | None:
    """One reference line for an option, or None for --help."""
    if "--help" in p.opts:
        return None
    # First-declared long spelling: p.opts is declaration order, and the canonical
    # name is declared first (`--ws, --conv`) — max-by-length taught the alias.
    decl = next((o for o in p.opts if o.startswith("--")), p.opts[0])
    # A --x/--no-x pair reads best spelled out.
    if p.secondary_opts:
        sec = next((o for o in p.secondary_opts if o.startswith("--")), p.secondary_opts[0])
        decl = f"{decl}/{sec}"
    head = decl if p.is_flag else f"{decl} {_metavar(p)}"
    if p.multiple:
        head += " (repeatable)"
    bits = []
    if p.help:
        bits.append(p.help)
    if p.default not in (None, False, (), []) and not p.is_flag:
        bits.append(f"[default: {p.default}]")
    tail = "  ".join(bits)
    return f"  {head:<34}{tail}".rstrip()


def _arg_bits(cmd) -> list[str]:
    args = []
    for p in cmd.params:
        if isinstance(p, TyperArgument):
            mv = (p.metavar or p.name or "arg").lower()
            if p.nargs == -1 or p.multiple:
                mv += "..."
            args.append(f"<{mv}>" if p.required else f"[{mv}]")
    return args


def _help_line(cmd) -> str:
    # click's short-help: first sentence-ish of the docstring, ellipsized —
    # cleaner than a raw first LINE, which cuts mid-clause.
    return cmd.get_short_help_str(limit=96)


def _fmt_command_full(prefix: str, name: str, cmd) -> list[str]:
    has_opts = any(isinstance(p, TyperOption) and "--help" not in p.opts for p in cmd.params)
    line = " ".join(filter(None, [prefix, name, *_arg_bits(cmd), "[options]" if has_opts else ""]))
    out = [line]
    if _help_line(cmd):
        out.append(f"  # {_help_line(cmd)}")
    for p in cmd.params:
        if isinstance(p, TyperOption):
            fmt = _fmt_option(p)
            if fmt:
                out.append(fmt)
    return out


def _walk(visit) -> None:
    """Call visit(prefix, name, leaf_command) for every leaf, tree order."""
    from .cli import app

    group = typer.main.get_command(app)
    ctx = Context(group)
    for name in group.list_commands(ctx):
        cmd = group.get_command(ctx, name)
        assert cmd is not None
        if cmd.hidden:  # back-compat aliases (`conv`) stay out of the reference
            continue
        prefix = _prefix_for(name)
        if isinstance(cmd, TyperGroup):
            sub_ctx = Context(cmd, parent=ctx)
            for sub_name in cmd.list_commands(sub_ctx):
                sub = cmd.get_command(sub_ctx, sub_name)
                assert sub is not None
                visit(f"{prefix} {name}", sub_name, sub)
        else:
            visit(prefix, name, cmd)


def generate_full() -> str:
    lines: list[str] = ["```"]
    _walk(lambda prefix, name, cmd: lines.extend(_fmt_command_full(prefix, name, cmd)))
    lines.append("```")
    return "\n".join(lines)


def generate_compact() -> str:
    lines: list[str] = ["```bash"]
    rows: list[tuple[str, str]] = []

    def visit(prefix: str, name: str, cmd) -> None:
        rows.append((" ".join([prefix, name, *_arg_bits(cmd)]), _help_line(cmd)))

    _walk(visit)
    width = max(len(r[0]) for r in rows)
    for invocation, help_line in rows:
        lines.append(f"{invocation:<{width}}  # {help_line}".rstrip())
    lines.append("```")
    return "\n".join(lines)


def _splice(text: str, begin: str, end: str, block: str, path: Path) -> str:
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(text):
        sys.exit(f"{path}: missing generated-block markers ({begin} … {end})")
    return pattern.sub(f"{begin}\n{block}\n{end}", text)


def main() -> None:
    check = "--check" in sys.argv[1:]
    jobs = [
        (FULL_TARGET, BEGIN_FULL, END_FULL, generate_full()),
        (COMPACT_TARGET, BEGIN_COMPACT, END_COMPACT, generate_compact()),
    ]
    stale = []
    for path, begin, end, block in jobs:
        old = path.read_text()
        new = _splice(old, begin, end, block, path)
        if new != old:
            if check:
                stale.append(path)
            else:
                path.write_text(new)
                print(f"updated {path}")
    if check and stale:
        sys.exit(
            "stale generated CLI reference in: "
            + ", ".join(str(p) for p in stale)
            + "\nrun: python -m tinkerscope._gen_cli_ref"
        )
    if check:
        print("CLI reference up to date")


if __name__ == "__main__":
    main()
