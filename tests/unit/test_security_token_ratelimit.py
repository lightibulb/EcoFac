"""Security regression tests for token comparison and rate-limit bucketing."""

import pathlib

import pytest

from ecofac.api.rate_limit import RateLimiter


class _Client:
    host = "203.0.113.1"


class _Request:
    def __init__(self, method: str = "GET", headers: dict | None = None) -> None:
        self.method = method
        self.headers = headers or {}
        self.client = _Client()


def test_no_direct_token_inequality_in_source() -> None:
    bad = []
    source_root = pathlib.Path("src/ecofac")

    if not source_root.exists():
        pytest.skip("src/ecofac not present")

    for path in source_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "x_ecofac_token !=" in text or "!= x_ecofac_token" in text:
            bad.append(str(path))

    assert bad == []


def test_invalid_token_does_not_create_separate_rate_bucket(monkeypatch) -> None:
    monkeypatch.delenv("ECOFAC_WRITE_TOKEN", raising=False)

    limiter = RateLimiter(read_limit=1)

    first = _Request(headers={"X-EcoFac-Token": "invalid-a"})
    second = _Request(headers={"X-EcoFac-Token": "invalid-b"})

    assert limiter.check(first)[0]
    assert not limiter.check(first)[0]
    assert not limiter.check(second)[0]
