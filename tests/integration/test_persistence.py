# Copyright 2026 Lightibulb

"""Integration tests for the SQLite persistence layer."""

import datetime as dt
import sqlite3

import pytest

from ecofac.domain.models import EnergyReading
from ecofac.persistence.database import get_connection, init_db
from ecofac.persistence.repository import DuplicateReadingError, insert_reading


@pytest.fixture
def db_conn():
    """Yield an in-memory database connection initialized with the schema."""
    conn = get_connection(":memory:")
    init_db(conn)
    yield conn
    conn.close()


def test_schema_initialization(db_conn):
    """Verify tables are created."""
    cursor = db_conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row["name"] for row in cursor.fetchall()}
    assert "organizations" in tables
    assert "energy_readings" in tables
    assert "audit_events" in tables


def test_identifier_uniqueness(db_conn):
    """Verify primary keys prevent duplicate organizations."""
    now = dt.datetime.now(dt.UTC).isoformat()
    
    db_conn.execute(
        "INSERT INTO organizations (id, name, created_at) VALUES (?, ?, ?)", 
        ("org-1", "Test", now)
    )

    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO organizations (id, name, created_at) VALUES (?, ?, ?)", 
            ("org-1", "Duplicate", now)
        )


def test_relationships(db_conn):
    """Verify foreign keys restrict invalid associations."""
    now = dt.datetime.now(dt.UTC).isoformat()
    # Attempting to add a building to a non-existent org should fail
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO buildings (id, org_id, name, created_at) VALUES (?, ?, ?, ?)", 
            ("b-1", "org-999", "Test Bldg", now)
        )


def test_duplicate_reading_handling(db_conn):
    """Verify duplicate readings raise an error and do not silently overwrite."""
    now = dt.datetime.now(dt.UTC).isoformat()
    
    db_conn.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'O1', ?)", (now,))
    db_conn.execute("INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1', 'o1', 'B1', ?)", (now,))
    db_conn.execute(
        "INSERT INTO meters (id, building_id, fuel_type, unit, created_at) "
        "VALUES ('m1', 'b1', 'electricity', 'kWh', ?)", 
        (now,)
    )
    db_conn.execute("INSERT INTO data_sources (id, name, source_type, created_at) VALUES ('s1', 'S1', 'csv', ?)", (now,))
    db_conn.execute("INSERT INTO import_batches (id, source_id, status, imported_at) VALUES ('ib1', 's1', 'complete', ?)", (now,))

    r1 = EnergyReading(
        id="r1", meter_id="m1", timestamp_utc="2026-01-01T00:00:00Z",
        value=10.5, unit="kWh", source_id="s1", batch_id="ib1",
        is_valid=True, created_at=now
    )
    insert_reading(db_conn, r1)

    # Attempt duplicate
    r2 = EnergyReading(
        id="r2", meter_id="m1", timestamp_utc="2026-01-01T00:00:00Z",
        value=20.0, unit="kWh", source_id="s1", batch_id="ib1",
        is_valid=True, created_at=now
    )
    with pytest.raises(DuplicateReadingError):
        insert_reading(db_conn, r2)

    # Verify value was not overwritten
    cursor = db_conn.execute("SELECT value FROM energy_readings WHERE meter_id='m1'")
    assert cursor.fetchone()["value"] == 10.5


def test_transaction_rollback(db_conn):
    """Verify that a failed insert rolls back completely."""
    now = dt.datetime.now(dt.UTC).isoformat()
    
    db_conn.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o2', 'O2', ?)", (now,))
    
    try:
        db_conn.execute("BEGIN")
        db_conn.execute("INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b2', 'o2', 'B2', ?)", (now,))
        # Force an error by inserting a duplicate primary key
        db_conn.execute("INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b2', 'o2', 'B2-dupe', ?)", (now,))
        db_conn.execute("COMMIT")
    except sqlite3.IntegrityError:
        db_conn.execute("ROLLBACK")

    # Verify the first insert was rolled back
    cursor = db_conn.execute("SELECT count(*) as c FROM buildings WHERE id='b2'")
    assert cursor.fetchone()["c"] == 0


def test_persistence_across_restarts(tmp_path):
    """Verify data persists when closing and reopening the connection."""
    db_file = tmp_path / "test.db"

    # 1. Write data
    conn1 = get_connection(str(db_file))
    init_db(conn1)
    now = dt.datetime.now(dt.UTC).isoformat()
    conn1.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o3', 'Persist', ?)", (now,))
    conn1.close()

    # 2. Read data after restart
    conn2 = get_connection(str(db_file))
    cursor = conn2.execute("SELECT name FROM organizations WHERE id='o3'")
    assert cursor.fetchone()["name"] == "Persist"
    conn2.close()
