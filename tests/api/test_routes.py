# Copyright 2026 Lightibulb

"""API integration tests."""

import os
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from ecofac.api.dependencies import get_db
from ecofac.main import app


@pytest.fixture
def test_db():
    """Yield an in-memory database."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    from ecofac.persistence.database import init_db
    init_db(conn)

    # Seed data
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


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_buildings(client):
    response = client.get("/api/v1/buildings")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == "b1"


def test_get_building_404(client):
    response = client.get("/api/v1/buildings/nonexistent")
    assert response.status_code == 404


def test_upload_csv(client):
    csv_content = (
        "timestamp,building_id,meter_id,energy_value,unit\n"
        "2026-01-01T12:00:00Z,b1,m1,10.5,kWh\n"
    )
    files = {"file": ("test.csv", csv_content, "text/csv")}
    data = {"source_id": "s1", "batch_id": "ib1"}

    response = client.post("/api/v1/imports", files=files, data=data)
    assert response.status_code == 200
    assert response.json()["accepted_rows"] == 1


def test_import_too_large(client):
    # Create a string > 5MB
    huge_content = "timestamp,building_id,meter_id,energy_value,unit\n" + "2026-01-01T12:00:00Z,b1,m1,10.5,kWh\n" * 200000
    files = {"file": ("huge.csv", huge_content, "text/csv")}
    data = {"source_id": "s1", "batch_id": "ib1"}

    response = client.post("/api/v1/imports", files=files, data=data)
    assert response.status_code == 413
