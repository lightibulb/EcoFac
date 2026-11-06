# Copyright 2026 Lightibulb
"""Regression tests for ingestion and time handling fixes."""

import pytest

from ecofac.ingestion.pipeline import ingest_csv
from ecofac.persistence.database import get_connection, init_db


@pytest.fixture
def db_conn():
    """Yield an in-memory database seeded with prerequisite data."""
    conn = get_connection(":memory:")
    init_db(conn)
    now = "2026-01-01T00:00:00Z"
    conn.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)", (now,))
    conn.execute(
        "INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1', 'o1', 'Bldg', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO meters (id, building_id, fuel_type, unit, created_at) "
        "VALUES ('m1', 'b1', 'electricity', 'kWh', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO data_sources (id, name, source_type, created_at) VALUES ('s1', 'Src', 'csv', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) VALUES ('ib1', 's1', 'started', ?)",
        (now,),
    )
    yield conn
    conn.close()


def test_naive_timestamp_is_rejected(db_conn):
    """Naive timestamps must be rejected, not interpreted as local time."""
    csv_data = (
        "timestamp,building_id,meter_id,energy_value,unit\n2026-01-01T12:00:00,b1,m1,10.5,kWh\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.total_rows == 1
    assert summary.accepted_rows == 0
    assert summary.rejected_rows == 1
    assert summary.invalid_timestamps == 1


def test_aware_offset_timestamp_still_normalized(db_conn):
    """Timezone-aware offsets remain normalized to UTC 'Z' storage form."""
    csv_data = (
        "timestamp,building_id,meter_id,energy_value,unit\n"
        "2026-01-01T12:00:00+02:00,b1,m1,10.5,kWh\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.accepted_rows == 1
    row = db_conn.execute("SELECT timestamp_utc FROM energy_readings").fetchone()
    assert row["timestamp_utc"] == "2026-01-01T10:00:00Z"


def test_duplicate_issue_is_scoped_with_typed_columns(db_conn):
    """Logged duplicate issues carry meter_id and reading timestamp."""
    csv_data = (
        "timestamp,building_id,meter_id,energy_value,unit\n"
        "2026-01-01T12:00:00Z,b1,m1,10.5,kWh\n"
        "2026-01-01T12:00:00Z,b1,m1,20.0,kWh\n"
    )
    ingest_csv(db_conn, csv_data, "s1", "ib1")
    row = db_conn.execute(
        "SELECT meter_id, reading_timestamp_utc FROM data_quality_issues "
        "WHERE issue_type = 'duplicate'"
    ).fetchone()
    assert row["meter_id"] == "m1"
    assert row["reading_timestamp_utc"] == "2026-01-01T12:00:00Z"
