from collections import defaultdict
from typing import Callable, Any


class EventBus:
    _instance: "EventBus | None" = None

    @classmethod
    def instance(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event: str, handler: Callable[[Any], None]) -> None:
        self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable[[Any], None]) -> None:
        self._handlers[event] = [h for h in self._handlers[event] if h is not handler]

    def emit(self, event: str, data: Any = None) -> None:
        for handler in list(self._handlers[event]):
            handler(data)
