"""Bounded producer buffer; GUI drains batches without one Qt event per log line."""
from collections import deque
import threading


class LogBuffer:
    def __init__(self, capacity=1000):
        self._items = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._dropped = 0

    def append(self, message):
        with self._lock:
            if len(self._items) == self._items.maxlen:
                self._dropped += 1
            self._items.append(str(message)[:8192])

    def drain(self, limit=50):
        with self._lock:
            items = [self._items.popleft() for _ in range(min(limit, len(self._items)))]
            dropped, self._dropped = self._dropped, 0
        return items, dropped

    def clear(self):
        with self._lock:
            self._items.clear()
            self._dropped = 0
