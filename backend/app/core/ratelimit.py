"""Per-process fixed-window rate limiting (Phase 10).

The API runs as one uvicorn process on one Render instance, so in-memory
counters are coherent; a restart resets them, which is acceptable here.
Adding `--workers N` would multiply every limit by N. Only the endpoints that
cost something are limited (Gmail calls, worker dispatches, OAuth redirects);
the polled status endpoints never are.
"""

import threading
import time
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status

from app.auth.dependencies import get_current_user
from app.users.models import User


class FixedWindowLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._counts: dict[tuple[str, str, int], int] = {}
        self._lock = threading.Lock()

    def hit(self, bucket: str, key: str, limit: int, window_seconds: int) -> int | None:
        """Counts one request; returns None if allowed, else seconds to wait."""
        now = self._clock()
        window = int(now // window_seconds)
        with self._lock:
            # Windows before the previous one can never matter again.
            self._counts = {k: v for k, v in self._counts.items() if k[2] >= window - 1}
            count = self._counts.get((bucket, key, window), 0) + 1
            self._counts[(bucket, key, window)] = count
        if count <= limit:
            return None
        return max(1, int((window + 1) * window_seconds - now))

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()

    def __len__(self) -> int:
        return len(self._counts)


limiter = FixedWindowLimiter()


def _too_many(wait: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many requests — try again in {wait} seconds.",
        headers={"Retry-After": str(wait)},
    )


def limit_per_user(bucket: str, limit: int, window_seconds: int = 60) -> Callable[..., None]:
    def dependency(current_user: User = Depends(get_current_user)) -> None:
        wait = limiter.hit(bucket, str(current_user.id), limit, window_seconds)
        if wait is not None:
            raise _too_many(wait)

    return dependency


def limit_per_ip(bucket: str, limit: int, window_seconds: int = 60) -> Callable[..., None]:
    # Best-effort only: uvicorn runs with --forwarded-allow-ips='*' behind
    # Render, so the client address comes from a spoofable X-Forwarded-For.
    # It slows casual hammering of an unauthenticated endpoint, nothing more.
    def dependency(request: Request) -> None:
        key = request.client.host if request.client else "unknown"
        wait = limiter.hit(bucket, key, limit, window_seconds)
        if wait is not None:
            raise _too_many(wait)

    return dependency
