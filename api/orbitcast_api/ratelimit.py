"""In-process sliding-window rate limiter (CLAUDE.md §7.3).

Both write endpoints (`POST /v1/users`, `POST /v1/measurements`) are rate-limited
to blunt bulk token minting and ingest floods. §7.1/§8 rule out Redis and queues at
this scale, so limits live in memory on the single API host. They reset on restart —
an accepted trade-off pre-traction; abuse is bounded per-window, not forever.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    """Allow at most ``max_requests`` per ``window_seconds`` for each key.

    Keys are opaque strings: client IP for token minting, the bearer token for
    ingest. Each key holds a deque of recent hit timestamps; on every call we drop
    timestamps older than the window, then admit only if under the cap.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._now = now
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._now()
        cutoff = now - self._window
        hits = self._hits[key]
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self._max:
            return False
        hits.append(now)
        return True
