# Copyright 2026 Lightibulb
"""ASGI middleware for operational observability.

Two middlewares:
  - RequestIDMiddleware: attaches X-Request-ID to every request/response.
  - SecurityHeadersMiddleware: attaches defensive headers.

Both are safe to run on every request and cannot raise.
"""

import logging
import uuid

from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a per-request ID to the response and to request.state.

    If the client provides X-Request-ID, that value is preserved so
    distributed traces can correlate. Otherwise a UUID is generated.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach defensive headers to every response.

    No Content-Security-Policy is added: the HTML templates use inline
    <script> and <style> blocks so a strict CSP would break the UI.
    See docs/limitations.md.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response
