"""Process-wide playground state + a tiny pub/sub for SSE fan-out.

There is exactly one PlaygroundState per process. The browser treats it as the
source of truth: it subscribes once to `/api/state/events` and re-renders on
every push. Both the browser and the `tinkpg` CLI mutate this state through the
same endpoints, so "drive the model from the terminal" and "click in the
browser" stay consistent — this is what makes the collaborative
"let's-look-at-the-model-together" flow work.

Two kinds of message travel the bus:
  - state patches  (`snapshot` / `patch`): persistent selection + params +
    conversation. Carried as a full state snapshot so a late subscriber is
    immediately consistent.
  - ephemeral broadcasts (`chat_start` / `sample` / `chat_done` / `chat_error`):
    streaming sample results. NOT stored on the state object (50 long samples
    would bloat every snapshot); the browser accumulates them per chat_id.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PanelState:
    """One comparison panel: its model selection + its OWN active-path transcript
    echo. The echo is write-only (the browser's branch tree is the read source); it
    exists so the CLI and external-fold reconcile can see/replay each panel's path.
    `id` is a stable string ('primary','compare','p-2',…), never an array index."""

    id: str = "primary"
    run_id: str | None = None
    checkpoint: str | None = None         # checkpoint name, e.g. "final"
    messages: list[dict] = field(default_factory=list)   # [{role, content}]


@dataclass
class PlaygroundState:
    """What the user (and Claude, via the CLI) is currently looking at. Sampling
    params are GLOBAL (shared across all panels); only run/checkpoint/transcript are
    per-panel, in `panels` (slot 0 = 'primary', always present)."""

    panels: list[PanelState] = field(default_factory=lambda: [PanelState(id="primary")])
    system_prompt: str | None = None
    # sampling params (global — shared across panels)
    temperature: float = 1.0
    max_tokens: int = 1024
    n_samples: int = 1
    thinking: bool = False
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
    _PANEL_FIELDS = ("run_id", "checkpoint", "messages")

    @staticmethod
    def _as_panel(p: Any) -> PanelState:
        if isinstance(p, PanelState):
            return p
        return PanelState(
            id=p["id"],
            run_id=p.get("run_id"),
            checkpoint=p.get("checkpoint"),
            messages=p.get("messages") or [],
        )

    def _patch_panel(self, panel_id: str, key: str, value: Any) -> None:
        """Route a per-panel field (run_id/checkpoint/messages) to an EXISTING panel.
        Never creates a panel: the `panels` field (full replace) is the sole source of
        truth for which panels exist, and every chat path (browser + CLI) registers its
        panel layout with a `panels` patch before it routes any messages here. Auto-
        creating used to let a stale `panel_messages` echo (the store mirrors a tree that
        outlived its panel) resurrect a removed panel with run_id=None — the phantom
        4th panel. Drop the update for an unknown id instead."""
        panel = next((p for p in self.state.panels if p.id == panel_id), None)
        if panel is None:
            return
        setattr(panel, key, value)

    def _apply_patch(self, patch: dict[str, Any]) -> None:
        """Apply a patch: `panels` full-replaces the list (browser/CLI selection);
        a `panel` id routes run_id/checkpoint/messages to that panel (chat.py);
        everything else is a global setattr."""
        panel_id = patch.get("panel")
        for k, v in patch.items():
            if k == "panel":
                continue
            if k == "panels":
                self.state.panels = [self._as_panel(p) for p in v]
            elif k == "panel_messages":
                # {panel_id: messages} — the store's active-path echo for every panel,
                # mirrored in one patch without touching run_id/checkpoint.
                for pid, msgs in (v or {}).items():
                    self._patch_panel(pid, "messages", msgs)
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

    async def chat_begin(self, **patch: Any) -> int:
        """Atomically: allocate a fresh chat_id, mark running, apply the
        selection/conversation/params patch, and broadcast the chat_start state.
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

    async def chat_end(self, event: str = "chat_done", **patch: Any) -> None:
        """Atomically: decrement the in-flight count, apply any patch, clear
        running only when no chat is still streaming, and broadcast."""
        async with self._lock:
            self._inflight = max(0, self._inflight - 1)
            self._apply_patch(patch)
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
