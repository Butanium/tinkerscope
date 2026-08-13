"""The bus's workspace-scoping rule (src/tinkerscope/api/state.py).

The bug these lock down: `panels` (per-panel models) is workspace-scoped data in a
process-global slot. Two browser tabs on two workspaces clobbered each other — the
losing tab mirrored the winner's models and persisted them onto its own workspace.
The browser half of the fix is web/src/lib/bus-scope.ts (+ bus-scope.test.ts); this
is the server half: a non-owner may not graft workspace-scoped keys onto the bus.
"""
from __future__ import annotations

import pytest

from tinkerscope.api.state import BUS, PlaygroundState


@pytest.fixture(autouse=True)
def fresh_bus():
    """Each test gets a pristine bus (BUS is a process-wide singleton)."""
    saved = BUS.state
    BUS.state = PlaygroundState()
    BUS._inflight = 0  # a prior test's unterminated chat_begin must not leak `running`
    yield
    BUS.state = saved


def _panels(*specs: tuple[str, str]) -> list[dict]:
    return [{"id": pid, "run_id": run, "checkpoint": "final", "messages": []} for pid, run in specs]


def _apply(**patch) -> PlaygroundState:
    BUS._apply_patch(patch)
    return BUS.state


def test_claim_sets_workspace_and_layout_together():
    st = _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    assert st.workspace_id == "ws-a"
    assert [p.run_id for p in st.panels] == ["run-a"]


def test_foreign_incremental_write_cannot_graft_onto_the_bus():
    """A patch stamped with a NON-owning workspace and no claim keeps only its
    global fields — its per-panel writes (thread-system mirror, the surviving
    incremental channel post-P3) must not graft onto the owner's panels."""
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    st = _apply(workspace_id="ws-b", panel_thread_system={"primary": "foreign probe"},
                temperature=0.42)
    assert st.workspace_id == "ws-a"
    assert st.panels[0].thread_system_prompt is None, "foreign mirror grafted"
    assert st.temperature == 0.42  # global fields still apply


def test_foreign_single_panel_subpatch_is_dropped():
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    st = _apply(workspace_id="ws-b", panel="primary", run_id="run-b", checkpoint="step-3")
    assert [p.run_id for p in st.panels] == ["run-a"]
    assert st.panels[0].checkpoint == "final"


def test_foreign_system_prompt_is_dropped():
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")),
           system_prompt="A's prompt", system_enabled=True)
    st = _apply(workspace_id="ws-b", system_prompt="B's prompt", system_enabled=False)
    assert st.system_prompt == "A's prompt"
    assert st.system_enabled is True


def test_foreign_patch_still_applies_GLOBAL_params():
    """Sampling params are shared on purpose — that's the point of one bus."""
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    st = _apply(workspace_id="ws-b", temperature=0.25, n_samples=8,
                panel_thread_system={"primary": "foreign"})
    assert st.temperature == 0.25
    assert st.n_samples == 8
    assert st.panels[0].thread_system_prompt is None


def test_foreign_patch_WITH_panels_claims_the_bus():
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    st = _apply(workspace_id="ws-b", panels=_panels(("primary", "run-b"), ("p-2", "run-c")))
    assert st.workspace_id == "ws-b"
    assert [p.run_id for p in st.panels] == ["run-b", "run-c"]


def test_unstamped_patch_is_treated_as_same_owner():
    """`tinkpg open <run>` sends no workspace id — terminal-drives-browser must keep
    working, so an unstamped patch applies to whatever workspace is on the bus."""
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    st = _apply(panels=_panels(("primary", "cli-picked")))
    assert st.workspace_id == "ws-a"
    assert [p.run_id for p in st.panels] == ["cli-picked"]


def test_restamp_requires_a_claim_even_on_an_unclaimed_bus():
    """The anti-chimera rule. Until 2026-08-06 a stamped patch was accepted
    whenever the bus was unclaimed — so right after a server restart a booting
    tab could restamp whatever panels the bus happened to hold with ITS OWN
    workspace id, and every client scoped to that id then adopted (and
    persisted) another workspace's layout as its own. `workspace_id` may only
    move together with the `panels` that belong to it."""
    _apply(panels=_panels(("primary", "run-a")))  # unclaimed bus, non-trivial panels
    st = _apply(workspace_id="ws-b", system_prompt="hello")
    assert st.workspace_id is None, "no claim → the stamp must not move"
    assert st.system_prompt is None, "workspace keys must not land either"
    assert [p.run_id for p in st.panels] == ["run-a"]
    # The first browser to speak still owns the bus — by claiming.
    st = _apply(workspace_id="ws-b", panels=_panels(("primary", "run-b")), system_prompt="hello")
    assert st.workspace_id == "ws-b"
    assert st.system_prompt == "hello"


def test_explicit_null_stamp_cannot_unstamp_the_bus():
    """`{workspace_id: null}` without panels (a booting tab's id-push once sent
    this) must not strip the owner's stamp — that would re-open the restamp hole
    one patch later."""
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    st = _apply(workspace_id=None, panel_thread_system={"primary": "sneak"})
    assert st.workspace_id == "ws-a"
    assert st.panels[0].thread_system_prompt is None


def test_own_incremental_write_applies():
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    st = _apply(workspace_id="ws-a", panel_thread_system={"primary": "mine"})
    assert st.panels[0].thread_system_prompt == "mine"


@pytest.mark.asyncio
async def test_chat_begin_goes_through_the_same_guard():
    """chat.py fires its selection patch through chat_begin, not _apply_patch —
    a foreign chat must not repoint the bus workspace's panel either."""
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    await BUS.chat_begin(workspace_id="ws-b", panel="primary", run_id="run-b",
                         messages=[{"role": "user", "content": "hi"}])
    assert [p.run_id for p in BUS.state.panels] == ["run-a"]
    assert BUS.state.workspace_id == "ws-a"


@pytest.mark.asyncio
async def test_chat_end_carries_no_state_patch_at_all():
    """P3: chat_end is bookkeeping-only. The transcript commit it used to apply
    retired with the echo — which is what closed the cross-workspace chimera
    class for good (the 8342e08 origin gate was the interim fix; the three
    tests that pinned it retired with the mechanism). A signature regression
    that reintroduces a patch parameter should fail here."""
    import inspect
    sig = inspect.signature(BUS.chat_end)
    assert list(sig.parameters) == ["event"], f"chat_end grew parameters: {sig}"
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    await BUS.chat_begin(workspace_id="ws-a", panel="primary")
    _apply(workspace_id="ws-b", panels=_panels(("primary", "run-b")))
    await BUS.chat_end("chat_done")
    assert BUS.state.workspace_id == "ws-b"
    assert not hasattr(BUS.state.panels[0], "messages")
    assert BUS.state.running is False
