# Copyright 2026 Lightibulb

"""Shared fixtures for API tests."""

import sqlite3
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from ecofac.api.dependencies import get_db
from ecofac.api.rate_limit import reset_rate_limiter
from ecofac.main import app
from ecofac.persistence.database import init_db


@pytest.fixture(autouse=True)
def _reset_rate_limiter_between_tests() -> Generator[None, None, None]:
    """Clear the rate-limiter buckets so tests do not bleed state.

    Rate limiting is on by default (DEF-20); without this reset the 600
    reads/min default would accumulate across the whole session.
    """
    reset_rate_limiter()
    yield
    reset_rate_limiter()


@pytest.fixture
def test_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield an in-memory database."""
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    init_db(conn)

    # Seed base metadata
    now = "2026-01-01T00:00:00Z"
    conn.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)", (now,))
    conn.execute("INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1', 'o1', 'Bldg', ?)", (now,))
    conn.execute("INSERT INTO meters (id, building_id, fuel_type, unit, created_at) VALUES ('m1', 'b1', 'electricity', 'kWh', ?)", (now,))
    conn.execute("INSERT INTO data_sources (id, name, source_type, created_at) VALUES ('s1', 'Src', 'csv', ?)", (now,))

    yield conn
    conn.close()


@pytest.fixture
def client(test_db: sqlite3.Connection) -> Generator[TestClient, None, None]:
    """Yield a FastAPI TestClient."""
    def override_get_db() -> Generator[sqlite3.Connection, None, None]:
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
