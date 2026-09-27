"""In-memory sliding-window rate limiter. Single Render instance, no Redis;
counters reset on restart (CLAUDE.md -> State and caching)."""

import time
from collections import defaultdict, deque

from fastapi import Depends, Request

from app.api.deps import get_session_id
from app.config import settings
from app.errors import ApiError


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(
        self, key: str, limit: int, window_seconds: float, now: float | None = None
    ) -> float | None:
        """Records a hit for `key` and returns None if it's within `limit` hits in the
        trailing `window_seconds`, otherwise the number of seconds until the oldest
        hit in the window expires (for Retry-After)."""
        now = time.monotonic() if now is None else now
        hits = self._hits[key]
        cutoff = now - window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= limit:
            return hits[0] + window_seconds - now

        hits.append(now)
        return None


# Module-level singleton: one limiter for the process, reused across every
# session/IP key. Reset on restart, which is acceptable (single Render instance).
_limiter = SlidingWindowLimiter()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


CONFIRM_LIMIT = 5
CONFIRM_WINDOW_SECONDS = 15 * 60


def _enforce(checks: tuple[tuple[str, int, int, str], ...]) -> None:
    for key, limit, window, message in checks:
        retry_after = _limiter.check(key, limit, window)
        if retry_after is not None:
            raise ApiError(
                429,
                "rate_limited",
                message,
                headers={"Retry-After": str(int(retry_after) + 1)},
            )


async def enforce_confirm_rate_limits(
    request: Request, session_id: str = Depends(get_session_id)
) -> None:
    """Booking confirm attempts: blocks passcode guessing (stage 10)."""
    message = "Too many confirm attempts, try again later."
    _enforce(
        (
            (f"confirm-session:{session_id}", CONFIRM_LIMIT, CONFIRM_WINDOW_SECONDS, message),
            (f"confirm-ip:{_client_ip(request)}", CONFIRM_LIMIT, CONFIRM_WINDOW_SECONDS, message),
        )
    )


async def enforce_rate_limits(request: Request, session_id: str = Depends(get_session_id)) -> None:
    ip = _client_ip(request)
    _enforce(
        (
            (
                f"session:{session_id}",
                settings.rate_limit_session_per_min,
                60,
                "Too many messages, slow down.",
            ),
            (
                f"ip-hour:{ip}",
                settings.rate_limit_ip_per_hour,
                3600,
                "Hourly limit reached, try again later.",
            ),
            (
                f"ip-day:{ip}",
                settings.rate_limit_ip_per_day,
                86400,
                "Daily limit reached, try again tomorrow.",
            ),
        )
    )
