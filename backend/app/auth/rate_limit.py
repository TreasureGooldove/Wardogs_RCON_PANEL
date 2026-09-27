"""Bounded login failure limiter for a single-process initial deployment."""

from collections import deque
from datetime import datetime, timedelta
from threading import Lock


class LoginLimiter:
    def __init__(self, *, max_failures: int = 5, window: timedelta = timedelta(minutes=5)) -> None:
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[str, deque[datetime]] = {}
        self._lock = Lock()

    def _recent(self, key: str, now: datetime) -> deque[datetime]:
        recent = self._failures.setdefault(key, deque())
        cutoff = now - self.window
        while recent and recent[0] <= cutoff:
            recent.popleft()
        return recent

    def is_limited(self, key: str, now: datetime) -> bool:
        with self._lock:
            return len(self._recent(key, now)) >= self.max_failures

    def record_failure(self, key: str, now: datetime) -> None:
        with self._lock:
            self._recent(key, now).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
