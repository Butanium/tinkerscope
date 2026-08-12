#!/usr/bin/python3
"""Escape raw NUL bytes in staged text sources (pre-commit step 1).

A raw NUL in a source file is behaviorally fine — it is always someone writing a
separator character straight into a string literal — but it makes the file BINARY
to grep, which then reports ZERO matches and exit 1 for every later query against
it. That failure is silent and confidently wrong: two sessions have now been told
"not in this file" about code that was plainly there (`+page.svelte` fd39382, then
`SearchPalette.svelte`). An escape sequence is the same character to the runtime,
so the fix costs nothing and the check is worth running on every commit.

Called with the staged paths. For each file whose STAGED blob contains a NUL:

  * a language we can escape unambiguously (JS family / Python) -> rewrite the
    worktree file, `git add` it, report, and let the commit proceed;
  * anything else -> refuse the commit and name the file (there is no escape we
    can apply blind to JSON/YAML/CSS/markdown).

Refuses rather than fixes when the worktree copy differs from the staged one
(partial `git add -p` staging, or a file deleted after staging): fixing the
worktree there would either miss what is being committed or sweep unstaged edits
into the commit.

Known limit, accepted: the replacement is textual, so a NUL sitting somewhere the
escape does NOT mean the same thing — Svelte markup text, a Python r'' string —
would change meaning. Neither is a thing anyone writes; the byte only ever shows
up as a `.join()` separator inside an ordinary string literal.

NB: this file never spells either escape sequence literally, and neither should
its callers' tests — an assistant writing "backslash u 0 0 0 0" into a file emits
the NUL itself (that is how this very script acquired three of them on its first
draft). Both are built from `chr(92)` below.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

NUL = bytes([0])
_BS = chr(92)  # a literal backslash, spelled indirectly — see the module docstring
JS_ESC = (_BS + "u0000").encode()  # the convention set by fd39382
PY_ESC = (_BS + "x00").encode()

# The escape to substitute, per suffix.
ESCAPES = {
    **{s: JS_ESC for s in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".svelte")},
    **{s: PY_ESC for s in (".py", ".pyi")},
}
# Text formats with no blind-safe escape — a NUL here is still a bug, but the fix
# needs a human (JSON wants a unicode escape inside a string and has no spelling
# for one outside; YAML, CSS, shell and markdown each differ again).
REFUSE_ONLY = {
    ".json", ".yaml", ".yml", ".toml", ".css", ".html", ".md", ".sh", ".bash",
    ".txt", ".sql", ".cfg", ".ini", ".svg", ".xml",
}


def staged_blob(path: str) -> bytes | None:
    """The bytes git would commit for `path`, or None if it can't be read."""
    r = subprocess.run(["git", "show", f":{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def nul_lines(blob: bytes) -> list[int]:
    return [i for i, line in enumerate(blob.split(b"\n"), 1) if NUL in line]


def _where(lines: list[int]) -> str:
    return ", ".join(f"line {n}" for n in lines[:5]) + ("…" if len(lines) > 5 else "")


def main(argv: list[str]) -> int:
    fixed: list[tuple[str, int, list[int]]] = []
    refused: list[tuple[str, str]] = []

    for path in [p for p in argv if p]:
        suffix = Path(path).suffix
        if suffix not in ESCAPES and suffix not in REFUSE_ONLY:
            continue  # not a text source we claim to understand (or a real binary)
        blob = staged_blob(path)
        if blob is None or NUL not in blob:
            continue

        where = _where(nul_lines(blob))
        if suffix in REFUSE_ONLY:
            refused.append((path, f"{where} — no blind-safe escape for {suffix}, fix it by hand"))
            continue

        wt = Path(path)
        if not wt.is_file() or wt.read_bytes() != blob:
            refused.append((path, f"{where} — worktree copy differs from the staged one "
                                  "(partial staging?); fix the file and re-stage"))
            continue

        wt.write_bytes(blob.replace(NUL, ESCAPES[suffix]))
        add = subprocess.run(["git", "add", "--", path], capture_output=True, text=True)
        if add.returncode != 0:
            refused.append((path, f"{where} — rewrote it, but `git add` failed: {add.stderr.strip()}"))
            continue
        fixed.append((path, blob.count(NUL), nul_lines(blob)))

    for path, count, lines in fixed:
        esc = ESCAPES[Path(path).suffix].decode()
        print(f"✓ pre-commit: escaped {count} raw NUL byte(s) as {esc} in {path} ({_where(lines)}) — re-staged")
    if fixed:
        print("  (same character to the runtime; a raw NUL makes the file binary to grep)")

    if refused:
        print("", file=sys.stderr)
        print("✗ pre-commit: raw NUL byte in a staged file — commit aborted.", file=sys.stderr)
        for path, why in refused:
            print(f"  {path}: {why}", file=sys.stderr)
        print("  A NUL makes the whole file binary to grep — every later search on it", file=sys.stderr)
        print(f"  silently returns nothing. Replace it with an escape ({JS_ESC.decode()} / {PY_ESC.decode()}).",
              file=sys.stderr)
        print("  Bypass with: git commit --no-verify", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
