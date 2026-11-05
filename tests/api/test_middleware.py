# Copyright 2026 Lightibulb
"""API tests for the operational foundation middlewares."""

from ecofac import main as ecofac_main


def test_request_id_header_present(client):
    resp = client.get("/health")
    assert "X-Request-ID" in resp.headers
    assert len(resp.headers["X-Request-ID"]) > 0


def test_request_id_echoes_client_value(client):
    resp = client.get("/health", headers={"X-Request-ID": "my-trace-123"})
    assert resp.headers["X-Request-ID"] == "my-trace-123"


def test_security_headers_on_json(client):
    resp = client.get("/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"


def test_security_headers_on_html(client):
    resp = client.get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_security_headers_on_error(client):
    resp = client.get("/api/v1/decisions/does-not-exist")
    assert resp.status_code == 404
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_ready_returns_200_when_db_available(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_ready_returns_503_when_db_unavailable(client, monkeypatch):
    monkeypatch.setattr(ecofac_main, "_check_db_ready", lambda: ["database unreachable"])
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert "unreachable" in resp.json()["detail"].lower()


def test_ready_distinct_from_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


def test_existing_endpoints_still_work(client):
    for path in (
        "/",
        "/health",
        "/ready",
        "/decisions",
        "/scenarios",
        "/api/v1/decisions",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
