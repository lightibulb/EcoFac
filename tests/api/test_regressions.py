# Copyright 2026 Lightibulb
"""Regression tests for API-level fixes."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ecofac.api.dependencies import get_db
from ecofac.main import app
from ecofac.persistence.database import init_db


@pytest.fixture
def test_db():
    """Yield an in-memory database."""
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    init_db(conn)
    now = "2026-01-01T00:00:00Z"
    conn.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)", (now,))
    conn.execute("INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1', 'o1', 'Bldg', ?)", (now,))
    conn.execute("INSERT INTO meters (id, building_id, fuel_type, unit, created_at) VALUES ('m1', 'b1', 'electricity', 'kWh', ?)", (now,))
    conn.execute("INSERT INTO data_sources (id, name, source_type, created_at) VALUES ('s1', 'Src', 'csv', ?)", (now,))
    yield conn
    conn.close()


@pytest.fixture
def client(test_db):
    """Yield a FastAPI TestClient with the overridden DB dependency."""
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_invalid_source_id_returns_400(client):
    """Unknown source_id must be a 400, not a swallowed FK error / 500."""
    csv_content = (
        "timestamp,building_id,meter_id,energy_value,unit\n"
        "2026-01-01T12:00:00Z,b1,m1,10.5,kWh\n"
    )
    files = {"file": ("x.csv", csv_content, "text/csv")}
    data = {"source_id": "does-not-exist", "batch_id": "ib-x"}
    response = client.post("/api/v1/imports", files=files, data=data)
    assert response.status_code == 400
    assert "source_id" in response.json()["detail"]


def test_quality_rejects_naive_timestamps(client):
    """Naive start/end params must produce a 400, not a 500."""
    response = client.get(
        "/api/v1/quality/m1",
        params={"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00"},
    )
    assert response.status_code == 400


def test_full_incident_lifecycle_via_http(client):
    """DETECTED -> UNDER_REVIEW -> ACKNOWLEDGED -> RESOLVED over HTTP."""
    flat = "timestamp,building_id,meter_id,energy_value,unit\n" + "".join(
        f"2026-01-01T{h:02d}:00:00Z,b1,m1,10.0,kWh\n" for h in range(24)
    )
    resp = client.post(
        "/api/v1/imports",
        files={"file": ("flat.csv", flat, "text/csv")},
        data={"source_id": "s1", "batch_id": "ib-flat"},
    )
    assert resp.status_code == 200

    spike = (
        "timestamp,building_id,meter_id,energy_value,unit\n"
        "2026-01-02T00:00:00Z,b1,m1,100.0,kWh\n"
    )
    resp = client.post(
        "/api/v1/imports",
        files={"file": ("spike.csv", spike, "text/csv")},
        data={"source_id": "s1", "batch_id": "ib-spike"},
    )
    assert resp.status_code == 200

    resp = client.post(
        "/api/v1/analyze/m1", params={"timestamp": "2026-01-02T00:00:00Z"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "anomalies_detected"
    finding = body["findings"][0]
    assert finding["gated_by_quality"] is False
    incident_id = finding["incident_id"]
    assert incident_id

    assert client.post(f"/api/v1/incidents/{incident_id}/review").status_code == 200
    assert (
        client.post(f"/api/v1/incidents/{incident_id}/acknowledge", json={}).status_code
        == 200
    )
    resp = client.post(
        f"/api/v1/incidents/{incident_id}/resolve", json={"reason": "fixed"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "RESOLVED"


def test_csv_naive_timestamp_rejected_via_http(client):
    """CSV rows with naive timestamps are rejected with a counter."""
    csv_content = (
        "timestamp,building_id,meter_id,energy_value,unit\n"
        "2026-01-01T12:00:00,b1,m1,10.5,kWh\n"
    )
    files = {"file": ("n.csv", csv_content, "text/csv")}
    data = {"source_id": "s1", "batch_id": "ib-naive"}
    resp = client.post("/api/v1/imports", files=files, data=data)
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_rows"] == 0
    assert body["invalid_timestamps"] == 1
