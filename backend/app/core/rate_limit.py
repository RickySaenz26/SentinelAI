"""Small fail-closed process-local limiter for the single-node Sprint 1B deployment."""

from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from app.core.errors import ApplicationError

_attempts: defaultdict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def enforce_rate_limit(scope: str, key: str, *, limit: int, window_seconds: int) -> None:
    now = monotonic()
    bucket_key = f"{scope}:{key}"
    with _lock:
        bucket = _attempts[bucket_key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(window_seconds - (now - bucket[0])))
            raise ApplicationError(
                "RATE_LIMITED",
                "Demasiadas solicitudes; inténtalo más tarde.",
                429,
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)


def reset_rate_limits() -> None:
    with _lock:
        _attempts.clear()
