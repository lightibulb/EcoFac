# Copyright 2026 Lightibulb
"""End-to-end tests for rate-limit middleware (opt-in via env)."""

import pytest

from ecofac.api.rate_limit import get_rate_limiter, reset_rate_limiter


@pytest.fixture
def enabled_limiter(monkeypatch):
    monkeypatch.setenv("ECOFAC_RATE_LIMIT_ENABLED", "true")
    limiter = get_rate_limiter()
    saved_read = limiter.read_limit
    saved_write = limiter.write_limit
    limiter.read_limit = 3
    limiter.write_limit = 2
    reset_rate_limiter()
    try:
        yield limiter
    finally:
        limiter.read_limit = saved_read
        limiter.write_limit = saved_write
        reset_rate_limiter()


def test_rate_limit_disabled_by_default(client):
    for _ in range(5):
        assert client.get("/health").status_code == 200


@pytest.mark.usefixtures("enabled_limiter")
def test_rate_limit_blocks_after_threshold(client):
    headers = {"X-Forwarded-For": "198.51.100.42"}
    codes = [client.get("/health", headers=headers).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
    assert codes[4] == 429


def test_rate_limit_returns_retry_after(client, enabled_limiter):
    enabled_limiter.read_limit = 1
    headers = {"X-Forwarded-For": "198.51.100.99"}
    client.get("/health", headers=headers)
    resp = client.get("/health", headers=headers)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) >= 1


def test_429_still_has_security_headers(client, enabled_limiter):
    enabled_limiter.read_limit = 1
    headers = {"X-Forwarded-For": "198.51.100.55"}
    client.get("/health", headers=headers)
    resp = client.get("/health", headers=headers)
    assert resp.status_code == 429
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert "X-Request-ID" in resp.headers
