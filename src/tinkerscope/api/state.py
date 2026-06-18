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
class PlaygroundState:
    """What the user (and Claude, via the CLI) is currently looking at."""

    mode: str = "single"                  # "single" | "compare"
    # primary selection
    run_id: str | None = None
    checkpoint: str | None = None         # checkpoint name, e.g. "final"
    # secondary selection (compare mode)
    compare_run_id: str | None = None
    compare_checkpoint: str | None = None
    # conversation + system prompt the next sample would use. In compare mode each
    # model keeps its OWN thread (sharing the user turns): `messages` is the primary
    # panel's transcript, `compare_messages` the compare panel's.
    messages: list[dict] = field(default_factory=list)   # [{role, content}]
    compare_messages: list[dict] = field(default_factory=list)
    system_prompt: str | None = None
    # sampling params
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

    async def publish_state(self, event: str, **patch: Any) -> dict:
        """Apply a patch to the state and broadcast the new snapshot."""
        async with self._lock:
            for k, v in patch.items():
                if hasattr(self.state, k):
                    setattr(self.state, k, v)
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
            for k, v in patch.items():
                if hasattr(self.state, k):
                    setattr(self.state, k, v)
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
            for k, v in patch.items():
                if hasattr(self.state, k):
                    setattr(self.state, k, v)
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
