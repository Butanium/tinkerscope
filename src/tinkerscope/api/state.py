"""Process-wide playground state + a tiny pub/sub for SSE fan-out.

There is exactly one PlaygroundState per process. The browser treats it as the
source of truth: it subscribes once to `/api/state/events` and re-renders on
every push. Both the browser and the `tinkpg` CLI mutate this state through the
same endpoints, so "drive the model from the terminal" and "click in the
browser" stay consistent — this is what makes the collaborative
"let's-look-at-the-model-together" flow work.

Two kinds of message travel the bus:
  - state patches  (`snapshot` / `patch`): persistent selection + params +
    workspace. Carried as a full state snapshot so a late subscriber is
    immediately consistent.
  - ephemeral broadcasts (`chat_start` / `sample` / `chat_done` / `chat_error`):
    streaming sample results. NOT stored on the state object (50 long samples
    would bloat every snapshot); the browser accumulates them per chat_id.

WORKSPACE SCOPING (the one non-obvious rule). One PlaygroundState per PROCESS is
right for sampling params — one knob, every panel, every client. It is NOT right
for the panel layout, per-panel models and system prompt: those belong to the open
WORKSPACE, are persisted with it and restored on open. So the bus holds exactly one
workspace's worth of them at a time, stamped with `workspace_id`, and:
  - a patch stamped with a different workspace may only apply workspace-scoped keys
    if it CLAIMS the bus by carrying `panels` (_drop_foreign_workspace_keys);
  - a client renders/persists workspace-scoped fields only from messages stamped
    with its own workspace (web/src/lib/bus-scope.ts).
Without this, two browser tabs on two workspaces clobber each other — the losing
tab mirrors the winner's models and then SAVES them onto its own workspace on disk
(really happened, 4 workspaces, 2026-07-24).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any


# The id a layout's FIRST panel gets when something has to invent one (a fresh bus,
# an empty workspace create, a schema fallback). Panel ids are minted monotonically
# per workspace and never reused — see `ws.mintPanelId`.
DEFAULT_PANEL_ID = "p-1"


@dataclass
class PanelState:
    """One comparison panel: its model SELECTION + thread-system mirror. The
    per-panel transcript echo retired with P3 (HANDOFF_SERVER_AUTHORITY §4.5):
    the workspace TREE is the transcript now — the CLI reads it over
    /api/workspaces and folds arrive as ops events, so the bus no longer
    carries message text at all. `id` is a stable string ('p-1','p-2',… — and
    'primary'/'compare' on workspaces saved before ids became monotonic),
    never an array index."""

    id: str = DEFAULT_PANEL_ID
    run_id: str | None = None
    checkpoint: str | None = None         # checkpoint name, e.g. "final"
    # The ACTIVE thread's system prompt (composed OVER the global one at sample
    # time — see routes/chat.py compose_system). Mirrored by the browser so a
    # CLI send that doesn't say otherwise extends the thread under the prompt
    # it was started with. None/"" = no thread part.
    thread_system_prompt: str | None = None


@dataclass
class PlaygroundState:
    """What the user (and Claude, via the CLI) is currently looking at. Sampling
    params are GLOBAL (shared across all panels); only run/checkpoint/transcript are
    per-panel, in `panels` (slot 0 always present)."""

    panels: list[PanelState] = field(default_factory=lambda: [PanelState(id=DEFAULT_PANEL_ID)])
    # Id of the saved workspace the browser currently has open (its `?c=`), pushed
    # so the CLI can name "what's on screen" exactly instead of guessing by path-match.
    # None when no workspace is open (or an older browser that doesn't push it).
    workspace_id: str | None = None
    system_prompt: str | None = None
    # Whether the global system prompt APPLIES to sends (the browser's power
    # toggle — False = kept but muted). Tri-state for back-compat: None = unset
    # (legacy state/clients) → derive from text presence, i.e. behave enabled.
    # Consumers gate with `is not False`. A patch that sets a non-empty
    # system_prompt WITHOUT this flag auto-enables (routes/state.py), so old
    # clients keep "set text ⇒ applies".
    system_enabled: bool | None = None
    # sampling params (global — shared across panels)
    temperature: float = 1.0
    max_tokens: int = 1024
    n_samples: int = 1
    # False / True / "both" ("both" = n_samples without thinking + n_samples with,
    # 2n per chat — see routes/chat.py)
    thinking: bool | str = False
    top_p: float | None = None
    # chat lifecycle
    chat_id: int = 0                      # increments each chat run; scopes sample events
    running: bool = False
    last_event: str | None = None
    last_event_ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StateBus:
    """Single publisher → many subscribers. Each subscriber gets its own queue."""

    def __init__(self) -> None:
        self.state = PlaygroundState()
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self._inflight = 0  # chats currently streaming; running == (_inflight > 0)

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register a subscriber. First event delivered is a full snapshot."""
        # Sized above the largest single burst (chat_start + up to 200 samples +
        # chat_done) so one big n_samples run can't overflow a momentarily-slow
        # consumer and drop its chat_done (which would wedge the panel spinner).
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
        async with self._lock:
            self._subs.add(q)
        await q.put({"type": "snapshot", "state": self.state.to_dict()})
        return q

    async def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subs.discard(q)

    # Fields that live on a PanelState, not the top-level state. A patch carrying a
    # `panel` id routes these to that panel; without `panel` they're ignored.
    _PANEL_FIELDS = ("run_id", "checkpoint", "thread_system_prompt")

    # Everything that describes the OPEN WORKSPACE rather than the process. These are
    # persisted per-workspace by the browser and restored on open; the bus holds them
    # only so `tinkpg` can see (and drive) what's on screen. Mirror of
    # web/src/lib/bus-scope.ts WORKSPACE_FIELDS + the panel-routing keys.
    _WORKSPACE_KEYS = (
        "panels", "workspace_id", "system_prompt", "system_enabled",
        "panel_thread_system",
        "panel", *_PANEL_FIELDS,
    )

    @classmethod
    def _as_panel(cls, p: Any) -> PanelState:
        if isinstance(p, PanelState):
            return p
        return PanelState(
            id=p["id"],
            run_id=p.get("run_id"),
            checkpoint=p.get("checkpoint"),
            thread_system_prompt=p.get("thread_system_prompt"),
        )

    def _patch_panel(self, panel_id: str, key: str, value: Any) -> None:
        """Route a per-panel field (run_id/checkpoint/thread_system_prompt) to an
        EXISTING panel. Never creates one: the `panels` field (full replace) is the
        sole source of truth for which panels exist. Auto-creating once let a stale
        per-panel echo resurrect a removed panel with run_id=None — the phantom
        4th panel. Drop the update for an unknown id instead."""
        panel = next((p for p in self.state.panels if p.id == panel_id), None)
        if panel is None:
            return
        setattr(panel, key, value)

    def _drop_foreign_workspace_keys(self, patch: dict[str, Any]) -> dict[str, Any]:
        """The bus describes exactly ONE workspace at a time — `workspace_id` and
        `panels` must always belong together (no chimeras). Two rules enforce it:

        1. Anti-graft: a patch stamped with a DIFFERENT workspace than the one on
           the bus may only apply workspace-scoped keys if it carries the full
           picture (`panels`) — i.e. it CLAIMS the bus. An incremental write from
           a non-owner (a second browser tab's transcript echo, a stale client)
           would otherwise graft onto the current workspace's panel list. Such a
           patch keeps only its GLOBAL fields (sampling params).
        2. Anti-chimera: a patch may never CHANGE `workspace_id` without bringing
           the panels that belong to it. Before 2026-08-06 a stamped patch was
           accepted whenever the bus was unclaimed (`current is None`), which let
           a booting tab restamp another workspace's panels with its own id right
           after a server restart — the browser then adopted the chimera as its
           own and persisted it (the second cross-tab layout clobber; the first
           is in the module docstring). Restamping now REQUIRES a claim.

        A patch with NO `workspace_id` key at all is treated as same-owner:
        that's the CLI (`tinkpg open`) and any pre-scoping client, and it's what
        keeps terminal-drives-browser working. (`exclude_unset` in routes/state.py
        preserves the absent-vs-null distinction this relies on.)
        See web/src/lib/bus-scope.ts for the browser half."""
        if "panels" in patch:
            return patch
        if "workspace_id" not in patch:
            return patch
        if patch["workspace_id"] == self.state.workspace_id:
            return patch
        return {k: v for k, v in patch.items() if k not in self._WORKSPACE_KEYS}

    def _apply_patch(self, patch: dict[str, Any]) -> None:
        """Apply a patch: `panels` full-replaces the list (browser/CLI selection);
        a `panel` id routes run_id/checkpoint/thread_system_prompt to that panel;
        everything else is a global setattr. Workspace-scoped keys are dropped when
        the patch belongs to a workspace that isn't the one on the bus and doesn't
        claim it — see _drop_foreign_workspace_keys."""
        patch = self._drop_foreign_workspace_keys(patch)
        panel_id = patch.get("panel")
        for k, v in patch.items():
            if k == "panel":
                continue
            if k == "panels":
                self.state.panels = [self._as_panel(p) for p in v]
            elif k == "panel_thread_system":
                # {panel_id: str|None} — the store's active-thread system-prompt
                # mirror, bulk-updated in one patch.
                for pid, ts in (v or {}).items():
                    self._patch_panel(pid, "thread_system_prompt", ts)
            elif k in self._PANEL_FIELDS:
                if panel_id is not None:
                    self._patch_panel(panel_id, k, v)
            elif hasattr(self.state, k):
                setattr(self.state, k, v)

    async def publish_state(self, event: str, **patch: Any) -> dict:
        """Apply a patch to the state and broadcast the new snapshot."""
        async with self._lock:
            self._apply_patch(patch)
            self.state.last_event = event
            self.state.last_event_ts = time.time()
            snap = self.state.to_dict()
            self._fanout({"type": "patch", "event": event, "state": snap})
            return snap

    async def broadcast(self, event: str, payload: dict[str, Any]) -> None:
        """Broadcast an ephemeral event (no state mutation) — e.g. one sample."""
        async with self._lock:
            self._fanout({"type": event, "event": event, **payload})

    async def alloc_chat_id(self) -> int:
        """chat_id allocation WITHOUT the chat lifecycle: no patch, no fanout,
        no `running` flip, no _inflight bump. For bus-SILENT chats
        (commit=false + broadcast=false — the `tinkpg probe` shape): the id
        keeps the cancel endpoint working while subscribers never learn the
        chat existed. Pairing rule: a chat allocated here must NOT call
        chat_end — the _inflight decrement would release a CONCURRENT chat's
        `running` early."""
        async with self._lock:
            self.state.chat_id += 1
            return self.state.chat_id

    async def chat_begin(self, **patch: Any) -> int:
        """Atomically: allocate a fresh chat_id, mark running, apply the
        selection/workspace/params patch, and broadcast the chat_start state.
        Returns the new chat_id. Race-free across concurrent /api/chat calls
        (compare fires two; CLI + browser can overlap)."""
        async with self._lock:
            self.state.chat_id += 1
            cid = self.state.chat_id
            self._inflight += 1
            self._apply_patch(patch)
            self.state.running = True
            self.state.last_event = "chat_start"
            self.state.last_event_ts = time.time()
            self._fanout({"type": "patch", "event": "chat_start", "state": self.state.to_dict()})
        return cid

    async def chat_end(self, event: str = "chat_done") -> None:
        """Atomically: decrement the in-flight count, clear running when no chat
        is still streaming, and broadcast. The transcript commit that used to
        ride this call retired with the echo (P3): folds land in the workspace
        TREE via the ops path before this runs, so the bus has nothing left to
        remember about a finished chat — which also closes the whole class of
        cross-workspace commit bugs the origin_workspace gate existed for."""
        async with self._lock:
            self._inflight = max(0, self._inflight - 1)
            if self._inflight == 0:
                self.state.running = False
            self.state.last_event = event
            self.state.last_event_ts = time.time()
            self._fanout({"type": "patch", "event": event, "state": self.state.to_dict()})

    def _fanout(self, msg: dict[str, Any]) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass


BUS = StateBus()


# Global sampling params the CLI can read before any browser connects. On startup we
# prime the bus from prefs.json's `last_session` so a pure-CLI consumer of a pack
# (`tinkerscope --pack …` then `tinkpg params`, never opening a browser) sees the pack's
# defaults instead of the PlaygroundState() library defaults. ONLY these five — the
# shared-state params — are seeded; panels/workspace are deliberately left at defaults
# so the browser's own restore (which fires only when every panel run_id is null) still
# runs and restores the layout + the frontend-only params (top_k/presence/repetition).
_SEEDABLE_PARAMS = ("temperature", "max_tokens", "n_samples", "thinking", "top_p")


def seed_bus_from_prefs() -> None:
    """Prime the bus's global sampling params from prefs.json's `last_session`.
    Best-effort at startup: a missing/garbled prefs file leaves the defaults intact."""
    import json

    from .settings import SETTINGS
    from .store import read_json

    raw = (read_json(SETTINGS.prefs_path, {}) or {}).get("last_session")
    if not raw:
        return
    try:
        session = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):  # corrupt double-encoded prefs — don't block startup
        return
    if not isinstance(session, dict):
        return
    for k in _SEEDABLE_PARAMS:
        if session.get(k) is not None:
            setattr(BUS.state, k, session[k])
