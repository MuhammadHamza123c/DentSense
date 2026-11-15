"""Small in-process sliding-window rate limiter.

Limitations (documented, intentional):
- State lives in the current process only. With multiple Uvicorn workers or
  replicas each process keeps its own counters; use a shared store (e.g. Redis)
  if strict global limits are required.
- Client identity is the TCP peer address. Do not trust ``X-Forwarded-For``
  unless a trusted reverse proxy is configured to overwrite it.
"""

from __future__ import annotations

import math
import time
from collections import deque
from threading import Lock


class InMemoryRateLimiter:
    """Sliding-window counter keyed by an arbitrary string (e.g. ``login:1.2.3.4``)."""

    def __init__(self, max_keys: int = 5000) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._max_keys = max_keys
        self._lock = Lock()

    def hit(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        """Record a hit.

        Returns:
            ``(allowed, retry_after_seconds)``. ``retry_after`` is 0 when allowed.
        """
        now = time.monotonic()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            cutoff = now - window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= limit:
                retry_after = max(1, math.ceil(window_seconds - (now - bucket[0])))
                return False, retry_after

            bucket.append(now)
            if len(self._hits) > self._max_keys:
                self._prune(now - window_seconds)
            return True, 0

    def reset(self) -> None:
        """Drop all counters (used by tests)."""
        with self._lock:
            self._hits.clear()

    def _prune(self, cutoff: float) -> None:
        stale = [
            key
            for key, bucket in self._hits.items()
            if not bucket or bucket[-1] <= cutoff
        ]
        for key in stale:
            self._hits.pop(key, None)
