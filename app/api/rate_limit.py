"""
A sliding-window limiter, in memory.

Each model call costs money, so an unlimited public endpoint is a bill waiting
to happen as much as it is an availability problem. Same caveat as the session
store: per process, and the thing to move to Redis before running more than one.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

WINDOW_SECONDS = 3600


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int = WINDOW_SECONDS):
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        """Records a hit and returns whether it was allowed."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True

    def retry_after(self, key: str) -> int:
        """Seconds until the oldest hit in the window falls out of it."""
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            return max(1, int(self._window - (time.monotonic() - hits[0])))

    def forget_stale(self) -> None:
        """Drops keys with nothing left in the window, so the dict stays bounded."""
        now = time.monotonic()
        with self._lock:
            for key in [k for k, hits in self._hits.items()
                        if not hits or now - hits[-1] > self._window]:
                del self._hits[key]
