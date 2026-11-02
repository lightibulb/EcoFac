# Copyright 2026 Lightibulb

"""Tests for the hardened input pipeline (out-of-order, gaps, bounds)."""

import pytest

from ecofac.ingestion.pipeline import ingest_csv
from ecofac.persistence.database import get_connection, init_db

HEADER = "timestamp,building_id,meter_id,energy_value,unit\n"


@pytest.fixture
def db_conn():
    """In-memory DB seeded with one building + one meter (60-min default)."""
    conn = get_connection(":memory:")
    init_db(conn)
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)",
        (now,),
    )
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
        "INSERT INTO data_sources (id, name, source_type, created_at) "
        "VALUES ('s1', 'Src', 'csv', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) "
        "VALUES ('ib1', 's1', 'started', ?)",
        (now,),
    )
    yield conn
    conn.close()


def _count(conn, issue_type):
    return conn.execute(
        "SELECT count(*) AS c FROM data_quality_issues WHERE issue_type = ?",
        (issue_type,),
    ).fetchone()["c"]


def test_out_of_order_readings_are_flagged(db_conn):
    """Unsorted CSV rows are stored but flagged as out_of_order."""
    csv_data = HEADER + (
        "2026-01-01T12:00:00Z,b1,m1,10.0,kWh\n"
        "2026-01-01T10:00:00Z,b1,m1,10.0,kWh\n"
        "2026-01-01T11:00:00Z,b1,m1,10.0,kWh\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.accepted_rows == 3
    assert summary.rejected_rows == 0
    assert _count(db_conn, "out_of_order") == 2


def test_sorted_readings_do_not_flag_out_of_order(db_conn):
    """Chronologically sorted rows should not raise out_of_order."""
    csv_data = HEADER + (
        "2026-01-01T10:00:00Z,b1,m1,10.0,kWh\n"
        "2026-01-01T11:00:00Z,b1,m1,10.0,kWh\n"
        "2026-01-01T12:00:00Z,b1,m1,10.0,kWh\n"
    )
    ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert _count(db_conn, "out_of_order") == 0


def test_large_gap_is_flagged(db_conn):
    """A gap > 2x interval_minutes is logged (default interval = 60 min)."""
    csv_data = HEADER + (
        "2026-01-01T00:00:00Z,b1,m1,10.0,kWh\n"
        "2026-01-01T05:00:00Z,b1,m1,10.0,kWh\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.accepted_rows == 2
    assert _count(db_conn, "irregular_sampling") == 1


def test_small_gap_is_not_flagged(db_conn):
    """Gaps <= 2x interval_minutes are acceptable (60 and 90 min)."""
    csv_data = HEADER + (
        "2026-01-01T00:00:00Z,b1,m1,10.0,kWh\n"
        "2026-01-01T01:00:00Z,b1,m1,10.0,kWh\n"
        "2026-01-01T02:30:00Z,b1,m1,10.0,kWh\n"
    )
    ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert _count(db_conn, "irregular_sampling") == 0


def test_suspicious_value_is_stored_invalid(db_conn):
    """Extremely large values are stored but marked is_valid=0."""
    csv_data = HEADER + "2026-01-01T00:00:00Z,b1,m1,999999999999,kWh\n"
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.accepted_rows == 1
    row = db_conn.execute("SELECT is_valid FROM energy_readings").fetchone()
    assert row["is_valid"] == 0
    assert _count(db_conn, "suspicious_value") == 1
    assert summary.negative_consumption == 0


def test_negative_value_is_not_double_counted(db_conn):
    """A negative value hits negative_consumption only (not suspicious)."""
    csv_data = HEADER + "2026-01-01T00:00:00Z,b1,m1,-10.5,kWh\n"
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.accepted_rows == 1
    assert summary.negative_consumption == 1
    assert _count(db_conn, "negative_consumption") == 1
    assert _count(db_conn, "suspicious_value") == 0


def test_empty_batch_is_noop(db_conn):
    """A CSV with headers and no rows imports cleanly."""
    summary = ingest_csv(db_conn, HEADER, "s1", "ib1")
    assert summary.total_rows == 0
    assert summary.accepted_rows == 0
    assert summary.rejected_rows == 0
    assert _count(db_conn, "out_of_order") == 0
    assert _count(db_conn, "irregular_sampling") == 0


def test_partially_valid_batch(db_conn):
    """Mixed batch: valid + duplicate + invalid-unit rows.

    Accepted rows are 10:00 and 12:00 → 120-min gap exactly at the 2x boundary.
    Strict `>` means no flag at equality.
    """
    csv_data = HEADER + (
        "2026-01-01T10:00:00Z,b1,m1,10.0,kWh\n"
        "2026-01-01T10:00:00Z,b1,m1,20.0,kWh\n"
        "2026-01-01T11:00:00Z,b1,m1,10.0,BTU\n"
        "2026-01-01T12:00:00Z,b1,m1,10.0,kWh\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.accepted_rows == 2
    assert summary.duplicate_rows == 1
    assert summary.invalid_units == 1
    assert _count(db_conn, "irregular_sampling") == 0
