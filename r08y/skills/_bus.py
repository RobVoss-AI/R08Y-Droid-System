"""Async event bus for inter-skill communication.

Skills emit and subscribe to named events. Handlers run concurrently via
asyncio.gather. Exceptions in one handler don't crash others.
"""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)

EventHandler = Callable[["Event"], Coroutine[Any, Any, None]]


@dataclass
class Event:
    """A typed event with payload."""
    name: str
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: float = field(default_factory=time.monotonic)


class EventBus:
    """Async pub/sub event bus for decoupled skill communication."""

    def __init__(self):
        self._subscribers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._history: List[Event] = []
        self._max_history = 100

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        self._subscribers[event_name].append(handler)
        logger.debug("EventBus: +subscriber for '%s'", event_name)

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        handlers = self._subscribers.get(event_name, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event_name: str, source: str = "", **payload) -> None:
        """Emit an event to all subscribers. Handlers run concurrently."""
        event = Event(name=event_name, payload=payload, source=source)
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        handlers = self._subscribers.get(event_name, [])
        if not handlers:
            return

        results = await asyncio.gather(
            *(self._safe_call(h, event) for h in handlers),
            return_exceptions=True,
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("EventBus: handler failed for '%s': %s", event_name, result)

    @staticmethod
    async def _safe_call(handler: EventHandler, event: Event) -> None:
        await handler(event)

    def get_recent(self, event_name: Optional[str] = None, limit: int = 20) -> List[Event]:
        events = self._history if event_name is None else [
            e for e in self._history if e.name == event_name
        ]
        return events[-limit:]
