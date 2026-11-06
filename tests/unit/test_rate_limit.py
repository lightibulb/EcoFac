# Copyright 2026 Lightibulb
"""Unit tests for the in-process rate limiter."""

from ecofac.api.rate_limit import RateLimiter


class _FakeClient:
    host = "203.0.113.1"


class _FakeRequest:
    def __init__(self, method="GET", headers=None, client=None):
        self.method = method
        self.headers = headers or {}
        self.client = client if client is not None else _FakeClient()


def test_allows_under_limit():
    limiter = RateLimiter(read_limit=3, write_limit=2)
    for _ in range(3):
        allowed, _ = limiter.check(_FakeRequest("GET"))
        assert allowed
    allowed, retry = limiter.check(_FakeRequest("GET"))
    assert not allowed
    assert retry >= 1


def test_read_and_write_buckets_independent():
    limiter = RateLimiter(read_limit=2, write_limit=2)
    for _ in range(2):
        assert limiter.check(_FakeRequest("GET"))[0]
    assert limiter.check(_FakeRequest("POST"))[0]


def test_client_key_by_token(monkeypatch):
    monkeypatch.setenv("ECOFAC_WRITE_TOKEN", "alpha")
    limiter = RateLimiter(read_limit=1)
    req_a = _FakeRequest("GET", headers={"X-EcoFac-Token": "alpha"})
    req_b = _FakeRequest("GET", headers={"X-EcoFac-Token": "beta"})
    assert limiter.check(req_a)[0]
    assert not limiter.check(req_a)[0]
    # req_b has an invalid token, so it falls back to IP (client.host 203.0.113.1)
    # Since IP bucket is fresh, it's allowed once:
    assert limiter.check(req_b)[0]
    assert not limiter.check(req_b)[0]


def test_client_key_by_forwarded_for(monkeypatch):
    monkeypatch.setenv("ECOFAC_TRUSTED_PROXIES", "true")
    limiter = RateLimiter(read_limit=1)
    req_a = _FakeRequest("GET", headers={"X-Forwarded-For": "10.0.0.1, 10.0.0.2"})
    req_b = _FakeRequest("GET", headers={"X-Forwarded-For": "10.0.0.3"})
    assert limiter.check(req_a)[0]
    assert not limiter.check(req_a)[0]
    # req_b is a different client IP (10.0.0.3), so it gets its own fresh bucket
    assert limiter.check(req_b)[0]


def test_forwarded_for_ignored_when_untrusted(monkeypatch):
    monkeypatch.delenv("ECOFAC_TRUSTED_PROXIES", raising=False)
    limiter = RateLimiter(read_limit=1)
    req_a = _FakeRequest("GET", headers={"X-Forwarded-For": "10.0.0.1"})
    req_b = _FakeRequest("GET", headers={"X-Forwarded-For": "10.0.0.2"})
    assert limiter.check(req_a)[0]
    # Should be rejected because both use client.host (203.0.113.1)
    assert not limiter.check(req_b)[0]


def test_client_key_by_remote_address():
    limiter = RateLimiter(read_limit=1)
    req = _FakeRequest("GET", client=type("C", (), {"host": "192.0.2.1"})())
    assert limiter.check(req)[0]
    assert not limiter.check(req)[0]


def test_reset_clears_all_buckets():
    limiter = RateLimiter(read_limit=1)
    assert limiter.check(_FakeRequest("GET"))[0]
    assert not limiter.check(_FakeRequest("GET"))[0]
    limiter.reset()
    assert limiter.check(_FakeRequest("GET"))[0]


def test_unknown_method_returns_allowed():
    limiter = RateLimiter(read_limit=1)
    assert limiter.check(_FakeRequest("TRACE"))[0]
