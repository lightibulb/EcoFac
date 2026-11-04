# Copyright 2026 Lightibulb
"""In-process rate limiting for the demo deployment.

Disabled by default. Enable by setting ECOFAC_RATE_LIMIT_ENABLED to
one of true/1/yes/on.

State is held in a module-level singleton so it works in the
single-process deployment this project targets. Multi-process
deployments would need shared state; see docs/limitations.md.
"""

import os
import threading
import time
from dataclasses import dataclass, field

from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

DEFAULT_READ_LIMIT = 600
DEFAULT_WRITE_LIMIT = 60
DEFAULT_WINDOW_SECONDS = 60


@dataclass
class _Bucket:
    count: int = 0
    window_start: float = 0.0


@dataclass
class RateLimiter:
    """Fixed-window limiter keyed by client identity and method group."""

    read_limit: int = DEFAULT_READ_LIMIT
    write_limit: int = DEFAULT_WRITE_LIMIT
    window_seconds: int = DEFAULT_WINDOW_SECONDS
    _buckets: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _client_key(self, request: Request) -> str:
        token = request.headers.get("X-EcoFac-Token")
        if token:
            return f"token:{token}"
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            first = forwarded.split(",", 1)[0].strip()
            if first:
                return f"ip:{first}"
        client = getattr(request, "client", None)
        if client is not None and client.host:
            return f"ip:{client.host}"
        return "ip:unknown"

    def check(self, request: Request) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        method = request.method.upper()
        if method in READ_METHODS:
            group = "read"
            limit = self.read_limit
        elif method in WRITE_METHODS:
            group = "write"
            limit = self.write_limit
        else:
            return True, 0

        key = f"{self._client_key(request)}:{group}"
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None or (now - bucket.window_start) >= self.window_seconds:
                self._buckets[key] = _Bucket(count=1, window_start=now)
                return True, 0
            if bucket.count < limit:
                bucket.count += 1
                return True, 0
            retry = int(self.window_seconds - (now - bucket.window_start)) + 1
            return False, max(1, retry)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _limiter


def reset_rate_limiter() -> None:
    _limiter.reset()


def is_rate_limit_enabled() -> bool:
    return os.environ.get("ECOFAC_RATE_LIMIT_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Return 429 when a client exceeds the in-process rate limit."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not is_rate_limit_enabled():
            return await call_next(request)
        allowed, retry_after = _limiter.check(request)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
