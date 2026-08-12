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
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    # Tab B (workspace ws-b) echoes ITS transcript for a panel id that collides.
    st = _apply(workspace_id="ws-b", panel_messages={"primary": [{"role": "user", "content": "from B"}]})
    assert st.workspace_id == "ws-a", "the stamp must not move without a claim"
    assert st.panels[0].messages == [], "ws-b's echo must not land on ws-a's panel"


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
                panel_messages={"primary": [{"role": "user", "content": "x"}]})
    assert st.temperature == 0.25
    assert st.n_samples == 8
    assert st.panels[0].messages == []


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
    st = _apply(workspace_id=None, panel_messages={"primary": [{"role": "user", "content": "x"}]})
    assert st.workspace_id == "ws-a"
    assert st.panels[0].messages == []


def test_own_incremental_write_applies():
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    st = _apply(workspace_id="ws-a", panel_messages={"primary": [{"role": "user", "content": "mine"}]})
    assert st.panels[0].messages == [{"role": "user", "content": "mine"}]


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
async def test_chat_end_commit_dropped_when_the_bus_moved_on():
    """The chimera hole (P1 certification observation): a chat fired under ws-a
    whose terminal lands after ws-b claimed the bus must NOT write ws-a's
    transcript into ws-b's panel echo — chat_end's commit was unstamped (=
    same-owner) before the origin_workspace gate. Stamped-b/echoing-a is one
    reconnect reconcile away from grafting a's turns into b's TREE."""
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    await BUS.chat_begin(workspace_id="ws-a", panel="primary")
    # ws-b claims while the chat streams…
    _apply(workspace_id="ws-b", panels=_panels(("primary", "run-b")))
    await BUS.chat_end(
        "chat_done", origin_workspace="ws-a",
        panel="primary", messages=[{"role": "user", "content": "a's turn"}])
    assert BUS.state.workspace_id == "ws-b"
    assert BUS.state.panels[0].messages == [], "a's transcript reached b's echo (chimera)"


@pytest.mark.asyncio
async def test_chat_end_commit_applies_for_the_still_owning_workspace():
    _apply(workspace_id="ws-a", panels=_panels(("primary", "run-a")))
    await BUS.chat_begin(workspace_id="ws-a", panel="primary")
    await BUS.chat_end(
        "chat_done", origin_workspace="ws-a",
        panel="primary", messages=[{"role": "user", "content": "mine"}])
    assert BUS.state.panels[0].messages == [{"role": "user", "content": "mine"}]


@pytest.mark.asyncio
async def test_chat_end_commit_with_no_origin_keeps_lockstep_behavior():
    """A chat fired with NO workspace open (pure lockstep) commits as today —
    even if a workspace claimed meanwhile. Retires with the null-stamp hole (P2)."""
    _apply(panels=_panels(("primary", "run-a")))
    await BUS.chat_begin(panel="primary")
    _apply(workspace_id="ws-b", panels=_panels(("primary", "run-b")))
    await BUS.chat_end(
        "chat_done", origin_workspace=None,
        panel="primary", messages=[{"role": "user", "content": "lockstep turn"}])
    assert BUS.state.panels[0].messages == [{"role": "user", "content": "lockstep turn"}]
