# P2-8: request-ID logging context tests (unit level).
#
# The integration test (real HTTP request) lives in
# tests/api/test_logging_context_integration.py, because it needs the
# `client` fixture which is provided by tests/api/conftest.py.
#
# Autouse fixture clears request_id_var before and after every test.
# Without it, test_filter_preserves_existing_extra's `set("from-var")`
# leaks into the next test, and the middleware's try/finally reset
# restores to that leaked value instead of None.

import asyncio
import logging

import pytest

from ecofac.api.middleware import RequestIDMiddleware
from ecofac.logging_config import (
    BASE_FMT,
    CTX_FMT,
    _ContextFormatter,
    _RequestContextFilter,
    request_id_var,
)


@pytest.fixture(autouse=True)
def _clean_request_id():
    request_id_var.set(None)
    yield
    request_id_var.set(None)


def _record(level=logging.INFO, msg="hello"):
    return logging.LogRecord(
        name="ecofac.test",
        level=level,
        pathname="/x.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_filter_uses_dash_when_no_context():
    rec = _record()
    _RequestContextFilter().filter(rec)
    assert rec.request_id == "-"


def test_filter_uses_contextvar_when_set():
    token = request_id_var.set("req-42")
    try:
        rec = _record()
        _RequestContextFilter().filter(rec)
        assert rec.request_id == "req-42"
    finally:
        request_id_var.reset(token)


def test_filter_preserves_existing_extra():
    request_id_var.set("from-var")
    rec = _record()
    rec.request_id = "from-extra"
    _RequestContextFilter().filter(rec)
    assert rec.request_id == "from-extra"


def test_formatter_base_when_no_request_id():
    fmt = _ContextFormatter(BASE_FMT, CTX_FMT)
    rec = _record(msg="startup")
    rec.request_id = "-"
    out = fmt.format(rec)
    assert "[request=" not in out
    assert "startup" in out


def test_formatter_context_when_request_id_present():
    fmt = _ContextFormatter(BASE_FMT, CTX_FMT)
    rec = _record(msg="in-request")
    rec.request_id = "abc-123"
    out = fmt.format(rec)
    assert "[request=abc-123]" in out
    assert "in-request" in out


def test_middleware_sets_and_resets_contextvar():
    seen = []

    async def wrapped_app(_scope, _receive, send):
        seen.append(request_id_var.get())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def run():
        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(_msg):
            return None

        middleware = RequestIDMiddleware(wrapped_app)
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/x",
            "raw_path": b"/x",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"x-request-id", b"test-abc")],
            "client": ("testclient", 12345),
            "server": ("testserver", 80),
        }
        await middleware(scope, receive, send)

    asyncio.run(run())
    assert seen == ["test-abc"]
    # Autouse fixture guarantees None before this test; the middleware's
    # try/finally must restore exactly that value.
    assert request_id_var.get() is None
