# Copyright 2026 Lightibulb

"""Unit tests for BodySizeLimitMiddleware (pure ASGI, no app needed)."""

import asyncio

from ecofac.api.middleware import BodySizeLimitMiddleware

LIMIT = 100
LIMITS = {("POST", "/up"): LIMIT}


class _Sink:
    """Minimal ASGI app that reads the whole body then answers 200."""

    def __init__(self):
        self.called = False
        self.total = 0

    async def __call__(self, scope, receive, send):
        self.called = True
        if scope["type"] != "http":
            return
        while True:
            message = await receive()
            self.total += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _drive(sink, scope, chunks):
    queue = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    ] or [{"type": "http.request", "body": b"", "more_body": False}]
    sent = []

    async def receive():
        return queue.pop(0)

    async def send(message):
        sent.append(message)

    asyncio.run(BodySizeLimitMiddleware(sink, LIMITS)(scope, receive, send))
    return sent


def _scope(method="POST", path="/up", headers=()):
    return {"type": "http", "method": method, "path": path, "headers": list(headers)}


def test_declared_length_over_limit_gets_413_without_reading_the_body():
    sink = _Sink()
    sent = _drive(sink, _scope(headers=[(b"content-length", b"101")]), [b"x" * 101])
    assert sent[0]["status"] == 413
    assert b"request_too_large" in sent[1]["body"]
    assert sink.called is False


def test_streamed_body_over_limit_raises_413_even_without_content_length():
    sink = _Sink()
    sent = _drive(sink, _scope(), [b"x" * 60, b"x" * 60])
    assert sent[0]["status"] == 413
    assert b"request_too_large" in sent[1]["body"]


def test_lying_content_length_is_caught_by_the_byte_count():
    sink = _Sink()
    sent = _drive(sink, _scope(headers=[(b"content-length", b"10")]), [b"x" * 150])
    assert sent[0]["status"] == 413
    assert b"request_too_large" in sent[1]["body"]


def test_body_exactly_at_the_limit_passes():
    sink = _Sink()
    sent = _drive(sink, _scope(headers=[(b"content-length", b"100")]), [b"x" * 100])
    assert sent[0]["status"] == 200
    assert sink.total == LIMIT


def test_other_paths_methods_and_scope_types_are_untouched():
    for scope in (_scope(path="/other"), _scope(method="GET")):
        sink = _Sink()
        sent = _drive(sink, scope, [b"x" * 500])
        assert sent[0]["status"] == 200
        assert sink.total == 500
    sink = _Sink()
    _drive(sink, {"type": "lifespan"}, [])
    assert sink.called is True
