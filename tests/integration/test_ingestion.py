# Copyright 2026 Lightibulb

"""Integration tests for the CSV ingestion pipeline."""

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


def test_valid_csv(db_conn):
    """Verify a standard valid CSV is imported."""
    csv_data = (
        "timestamp,building_id,meter_id,energy_value,unit\n2026-01-01T12:00:00Z,b1,m1,10.5,kWh\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.total_rows == 1
    assert summary.accepted_rows == 1
    assert summary.rejected_rows == 0


def test_empty_csv(db_conn):
    """Verify an empty CSV raises a header error."""
    with pytest.raises(ValueError, match="Missing required columns"):
        ingest_csv(db_conn, "", "s1", "ib1")


def test_missing_columns(db_conn):
    """Verify missing required headers raise a clear error."""
    csv_data = "timestamp,building_id,meter_id,energy_value\n2026-01-01T12:00:00Z,b1,m1,10.5\n"
    with pytest.raises(ValueError, match="Missing required columns"):
        ingest_csv(db_conn, csv_data, "s1", "ib1")


def test_invalid_timestamps(db_conn):
    """Verify invalid timestamps are rejected and logged."""
    csv_data = "timestamp,building_id,meter_id,energy_value,unit\nnot-a-date,b1,m1,10.5,kWh\n"
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.total_rows == 1
    assert summary.rejected_rows == 1
    assert summary.invalid_timestamps == 1


def test_different_time_zones(db_conn):
    """Verify timestamps with offsets are normalized to UTC."""
    csv_data = (
        "timestamp,building_id,meter_id,energy_value,unit\n"
        "2026-01-01T12:00:00+02:00,b1,m1,10.5,kWh\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.accepted_rows == 1

    cursor = db_conn.execute("SELECT timestamp_utc FROM energy_readings")
    assert cursor.fetchone()["timestamp_utc"] == "2026-01-01T10:00:00Z"


def test_duplicate_records(db_conn):
    """Verify duplicates in the same CSV are rejected."""
    csv_data = (
        "timestamp,building_id,meter_id,energy_value,unit\n"
        "2026-01-01T12:00:00Z,b1,m1,10.5,kWh\n"
        "2026-01-01T12:00:00Z,b1,m1,20.0,kWh\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.accepted_rows == 1
    assert summary.duplicate_rows == 1


def test_unsupported_units(db_conn):
    """Verify unsupported units are rejected."""
    csv_data = (
        "timestamp,building_id,meter_id,energy_value,unit\n2026-01-01T12:00:00Z,b1,m1,10.5,BTU\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.rejected_rows == 1
    assert summary.invalid_units == 1


def test_negative_consumption(db_conn):
    """Verify negative consumption is stored as invalid and flagged."""
    csv_data = (
        "timestamp,building_id,meter_id,energy_value,unit\n2026-01-01T12:00:00Z,b1,m1,-10.5,kWh\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.accepted_rows == 1
    assert summary.negative_consumption == 1

    cursor = db_conn.execute("SELECT is_valid FROM energy_readings")
    assert cursor.fetchone()["is_valid"] == 0

    # Verify the issue was actually logged
    cursor = db_conn.execute("SELECT issue_type FROM data_quality_issues")
    assert cursor.fetchone()["issue_type"] == "negative_consumption"


def test_large_input_files(db_conn):
    """Verify row limits are enforced."""
    csv_data = (
        "timestamp,building_id,meter_id,energy_value,unit\n"
        "2026-01-01T12:00:00Z,b1,m1,1.0,kWh\n"
        "2026-01-01T13:00:00Z,b1,m1,1.0,kWh\n"
    )
    with pytest.raises(ValueError, match="Row count exceeds limit"):
        ingest_csv(db_conn, csv_data, "s1", "ib1", max_rows=1)


def test_large_file_size(db_conn):
    """Verify file size limits are enforced."""
    csv_data = (
        "timestamp,building_id,meter_id,energy_value,unit\n2026-01-01T12:00:00Z,b1,m1,1.0,kWh\n"
    )
    with pytest.raises(ValueError, match="File size exceeds limit"):
        ingest_csv(db_conn, csv_data, "s1", "ib1", max_size_mb=0.00001)


def test_partial_import_failures(db_conn):
    """Verify malformed rows do not corrupt valid rows in the same import."""
    csv_data = (
        "timestamp,building_id,meter_id,energy_value,unit\n"
        "2026-01-01T12:00:00Z,b1,m1,10.5,kWh\n"
        "2026-01-01T13:00:00Z,b1,m999,10.5,kWh\n"
        "2026-01-01T14:00:00Z,b1,m1,10.5,INVALID\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.total_rows == 3
    assert summary.accepted_rows == 1
    assert summary.rejected_rows == 2
    assert summary.unknown_meter_ids == 1
    assert summary.invalid_units == 1
