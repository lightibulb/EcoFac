# Copyright 2026 Lightibulb
"""Integration tests for the data quality engine."""

import datetime as dt

import pytest

from ecofac.domain.models import EnergyReading
from ecofac.persistence.database import get_connection, init_db
from ecofac.persistence.repository import insert_reading
from ecofac.quality.assessment import assess_quality


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


def _insert_reading(conn, reading_id, ts, value, *, is_valid=True):
    r = EnergyReading(
        id=reading_id,
        meter_id="m1",
        timestamp_utc=ts,
        value=value,
        unit="kWh",
        source_id="s1",
        batch_id="ib1",
        is_valid=is_valid,
        created_at="2026-01-01T00:00:00Z",
    )
    insert_reading(conn, r)


def test_perfect_data_eligibility(db_conn):
    """Verify perfect data is marked eligible and no flags are raised."""
    _insert_reading(db_conn, "r1", "2026-01-01T00:00:00Z", 10.0)
    _insert_reading(db_conn, "r2", "2026-01-01T01:00:00Z", 12.0)
    _insert_reading(db_conn, "r3", "2026-01-01T02:00:00Z", 14.0)
    start = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    end = dt.datetime(2026, 1, 1, 2, 0, tzinfo=dt.UTC)
    report = assess_quality(db_conn, "m1", start, end, expected_frequency_minutes=60)
    assert report.expected_readings == 3
    assert report.actual_readings == 3
    assert report.missing_readings == 0
    assert report.is_stale is False
    assert report.is_analysis_eligible is True
    assert len(report.flags) == 0


def test_missing_data_detection(db_conn):
    """Verify missing readings lower eligibility."""
    _insert_reading(db_conn, "r1", "2026-01-01T00:00:00Z", 10.0)
    _insert_reading(db_conn, "r3", "2026-01-01T02:00:00Z", 14.0)
    start = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    end = dt.datetime(2026, 1, 1, 2, 0, tzinfo=dt.UTC)
    report = assess_quality(db_conn, "m1", start, end, expected_frequency_minutes=60)
    assert report.missing_readings == 1
    assert report.is_analysis_eligible is False
    assert "missing_data" in report.flags


def test_stale_data_detection(db_conn):
    """Verify old data is marked as stale."""
    _insert_reading(db_conn, "r1", "2026-01-01T00:00:00Z", 10.0)
    start = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    end = dt.datetime(2026, 1, 1, 10, 0, tzinfo=dt.UTC)
    report = assess_quality(db_conn, "m1", start, end, expected_frequency_minutes=60)
    assert report.is_stale is True
    assert report.is_analysis_eligible is False
    assert "stale_data" in report.flags


def test_invalid_vs_extreme_valid_data(db_conn):
    """Verify extreme but valid data is kept, while invalid data fails eligibility."""
    _insert_reading(db_conn, "r1", "2026-01-01T00:00:00Z", 99999.9, is_valid=True)
    _insert_reading(db_conn, "r2", "2026-01-01T01:00:00Z", -10.0, is_valid=False)
    _insert_reading(db_conn, "r3", "2026-01-01T02:00:00Z", 14.0, is_valid=True)
    start = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    end = dt.datetime(2026, 1, 1, 2, 0, tzinfo=dt.UTC)
    report = assess_quality(db_conn, "m1", start, end, expected_frequency_minutes=60)
    assert report.invalid_readings == 1
    assert report.actual_readings == 3
    assert report.is_analysis_eligible is False
    assert "invalid_data" in report.flags


def test_duplicate_detection(db_conn):
    """Verify duplicates logged during ingestion affect eligibility."""
    _insert_reading(db_conn, "r1", "2026-01-01T00:00:00Z", 10.0)
    db_conn.execute(
        "INSERT INTO data_quality_issues "
        "(id, reading_id, batch_id, issue_type, description, meter_id, "
        "reading_timestamp_utc, created_at) "
        "VALUES ('dq1', NULL, 'ib1', 'duplicate', "
        "'Duplicate in CSV: m1 @ 2026-01-01T00:00:00Z', 'm1', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    start = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    end = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    report = assess_quality(db_conn, "m1", start, end, expected_frequency_minutes=60)
    assert report.duplicate_readings == 1
    assert report.is_analysis_eligible is False
    assert "duplicate_data" in report.flags


def test_duplicate_outside_window_is_ignored(db_conn):
    """Duplicates outside the assessed period must not poison eligibility."""
    _insert_reading(db_conn, "r1", "2026-01-01T00:00:00Z", 10.0)
    db_conn.execute(
        "INSERT INTO data_quality_issues "
        "(id, reading_id, batch_id, issue_type, description, meter_id, "
        "reading_timestamp_utc, created_at) "
        "VALUES ('dq2', NULL, 'ib1', 'duplicate', "
        "'Duplicate in CSV: m1 @ 2026-01-05T00:00:00Z', 'm1', "
        "'2026-01-05T00:00:00Z', '2026-01-05T00:00:00Z')"
    )
    start = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    end = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    report = assess_quality(db_conn, "m1", start, end, expected_frequency_minutes=60)
    assert report.duplicate_readings == 0
    assert report.is_analysis_eligible is True
