"""The CLI reference blocks in README.md / plugin/skills/cli/SKILL.md are
GENERATED from the Typer app, not hand-written — this test fails when a CLI
change lands without rerunning `python -m tinkerscope._gen_cli_ref` (the
doc-drift class the unification was half about; samplescope's pattern)."""
from __future__ import annotations

import re

from tinkerscope._gen_cli_ref import (
    BEGIN_COMPACT, BEGIN_FULL, COMPACT_TARGET, END_COMPACT, END_FULL, FULL_TARGET,
    generate_compact, generate_full,
)


def _assert_current(path, begin, end, block):
    pattern = re.compile(re.escape(begin) + r"\n(.*?)\n" + re.escape(end), re.DOTALL)
    m = pattern.search(path.read_text())
    assert m, f"{path}: missing generated-block markers"
    assert m.group(1) == block, (
        f"{path}: stale generated CLI reference — run: python -m tinkerscope._gen_cli_ref"
    )


def test_generated_cli_reference_is_current():
    _assert_current(FULL_TARGET, BEGIN_FULL, END_FULL, generate_full())
    _assert_current(COMPACT_TARGET, BEGIN_COMPACT, END_COMPACT, generate_compact())


def test_reference_covers_the_merged_surface():
    """Every command is documented as `tinkerscope <verb>` — and hidden aliases
    (the deprecated `tinkpg` name, `conv`) stay out."""
    full = generate_full()
    for needle in ("tinkerscope send", "tinkerscope grep", "tinkerscope serve",
                   "tinkerscope pack export", "tinkerscope site export"):
        assert needle in full, f"missing {needle!r}"
    assert "tinkerscope conv" not in full, "hidden back-compat alias leaked into the docs"
    assert "tinkpg" not in full, "the deprecated tinkpg name leaked into the docs"
