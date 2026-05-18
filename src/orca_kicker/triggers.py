"""Thread-safe trigger event queue.

Callbacks (GPIO) enqueue TriggerEvents here.
Main loop drains the queue each tick.

Rules:
- Callbacks must be fast and non-blocking — only enqueue here.
- Never call pyorcasdk from callbacks.
"""

import queue
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class TriggerType(Enum):
    KICK = auto()
    AUTOZERO = auto()
    KICK_RELEASED = auto()
    SLEEP_TOGGLE = auto()


@dataclass(frozen=True)
class TriggerEvent:
    trigger_type: TriggerType
    timestamp_s: float = field(default=0.0)


class TriggerQueue:
    """Thread-safe FIFO queue for trigger events."""

    def __init__(self, maxsize: int = 64) -> None:
        self._queue: queue.Queue[TriggerEvent] = queue.Queue(maxsize=maxsize)

    def enqueue(self, event: TriggerEvent) -> None:
        """Enqueue an event. Drops silently if queue is full."""
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            pass  # Drop — never block a callback

    def drain(self) -> list[TriggerEvent]:
        """Drain all pending events. Call once per loop tick."""
        events: list[TriggerEvent] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    def drain_one(self) -> Optional[TriggerEvent]:
        """Return the next event, or None if empty."""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None
