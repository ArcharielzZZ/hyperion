"""Lightweight async rate limiting (token bucket) for HTTP workers."""

from __future__ import annotations

import asyncio


class AsyncTokenBucket:
    """Fixed-rate spacing between acquisitions (simple, predictable)."""

    def __init__(self, *, min_interval_sec: float) -> None:
        if min_interval_sec <= 0:
            raise ValueError("min_interval_sec must be positive")
        self._interval = min_interval_sec
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            wait = self._next - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = loop.time()
            self._next = max(self._next, now) + self._interval
