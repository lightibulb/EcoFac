# Copyright 2026 Lightibulb
"""ASGI middleware for operational observability.

Three middlewares:
  - RequestIDMiddleware: attaches X-Request-ID to every request/response.
  - SecurityHeadersMiddleware: attaches defensive headers.
  - BodySizeLimitMiddleware: bounds the request body for selected routes
    before the multipart parser can buffer it.

The first two are safe to run on every request and cannot raise.
"""

import logging
import uuid
from collections.abc import Mapping

from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from ecofac.api.errors import api_error
from ecofac.logging_config import request_id_var

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a per-request ID to the response and to request.state.

    If the client provides X-Request-ID, that value is preserved so
    distributed traces can correlate. Otherwise a UUID is generated.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        # P2-8: expose the ID to logging via ContextVar. Set/reset
        # per request in try/finally so a leaked exception cannot
        # contaminate the next request in the same worker.
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach defensive headers to every response.

    No Content-Security-Policy is added: the HTML templates use inline
    <script> and <style> blocks so a strict CSP would break the UI.
    See docs/limitations.md.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response


def _too_large_detail(limit: int) -> dict:
    return {
        "code": "request_too_large",
        "message": f"Request body exceeds the {limit} byte limit for this endpoint",
        "details": {"limit_bytes": limit},
    }


class BodySizeLimitMiddleware:
    """Reject oversized request bodies for specific (method, path) routes.

    Why this exists: Starlette parses a multipart upload *completely*
    (spooling anything over 1 MiB to a temporary file) before the route
    handler runs, so a size check inside the handler cannot stop the
    buffering. This middleware limits the body before that happens:

    1. A declared ``Content-Length`` above the limit is answered with 413
       immediately and the body is never read.
    2. Otherwise the bytes actually received are counted; the moment the
       total passes the limit the next read raises a 413 ``HTTPException``,
       which covers chunked uploads and a lying ``Content-Length``.

    It is a pure ASGI middleware (not ``BaseHTTPMiddleware``) because it must
    wrap ``receive``. Add it first so it is the innermost layer and its 413s
    still pick up the request-ID and security headers.
    """

    def __init__(self, app: ASGIApp, limits: Mapping[tuple[str, str], int]) -> None:
        self.app = app
        self.limits = dict(limits)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = (
            self.limits.get((scope.get("method", ""), scope.get("path", "")))
            if scope["type"] == "http"
            else None
        )
        if limit is None:
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except ValueError:
                declared_size = 0
            if declared_size > limit:
                response = JSONResponse(
                    status_code=413, content={"detail": _too_large_detail(limit)}
                )
                await response(scope, receive, send)
                return

        received = 0
        limit_exceeded = False

        async def limited_receive() -> dict:
            nonlocal received, limit_exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    limit_exceeded = True
                    detail = _too_large_detail(limit)
                    raise api_error(
                        413,
                        detail["code"],
                        detail["message"],
                        **detail["details"],
                    )
            return message

        async def intercepted_send(message: dict) -> None:
            if not limit_exceeded:
                await send(message)

        try:
            await self.app(scope, limited_receive, intercepted_send)
        except Exception:
            if limit_exceeded:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": _too_large_detail(limit)},
                )
                await response(scope, receive, send)
                return
            raise
