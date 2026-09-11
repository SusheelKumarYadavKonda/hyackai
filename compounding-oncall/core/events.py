"""Event bus. Lets the loop narrate itself without knowing who is listening.

The loop emits structured events; sinks decide how to render them. That keeps
one code path feeding both the terminal narration and the live web UI, so what
an audience sees on screen is the same run, not a replayed recording.
"""

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class Event:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class EventBus:
    """Fan-out to any number of async subscribers.

    Late subscribers receive the backlog, so a browser that connects mid-run
    still renders everything that already happened rather than joining blind.
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[Optional[Event]]] = []
        self._history: list[Event] = []

    def subscribe(self) -> asyncio.Queue[Optional[Event]]:
        queue: asyncio.Queue[Optional[Event]] = asyncio.Queue()
        for event in self._history:
            queue.put_nowait(event)
        self._queues.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Optional[Event]]) -> None:
        if queue in self._queues:
            self._queues.remove(queue)

    def emit(self, kind: str, **data: Any) -> Event:
        event = Event(kind=kind, data=data)
        self._history.append(event)
        for queue in self._queues:
            queue.put_nowait(event)
        return event

    def close(self) -> None:
        """Signal end of stream so subscribers can finish cleanly."""
        for queue in self._queues:
            queue.put_nowait(None)

    def reset(self) -> None:
        self._history.clear()

    @property
    def history(self) -> list[Event]:
        return list(self._history)


# Module-level bus. A single demo run per process, so a singleton is honest here
# rather than threading an instance through every call site.
BUS = EventBus()
