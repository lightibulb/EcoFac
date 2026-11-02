# Copyright 2026 Lightibulb

"""Tests for time-based detection windows and explainable metadata."""

import datetime as dt
import json

import pytest

from ecofac.detection.anomaly import (
    DEFAULT_MIN_SAMPLES,
    detect_robust_mad,
    detect_rolling_baseline,
)
from ecofac.domain.models import EnergyReading
from ecofac.persistence.database import get_connection, init_db
from ecofac.persistence.repository import insert_reading
from ecofac.timeutil import to_z


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


def _insert_at(conn, reading_id, ts, value):
    r = EnergyReading(
        id=reading_id, meter_id="m1", timestamp_utc=ts, value=value,
        unit="kWh", source_id="s1", batch_id="ib1", is_valid=True,
        created_at="2026-01-01T00:00:00Z",
    )
    insert_reading(conn, r)


def test_irregular_sampling_uses_time_window(db_conn):
    """24 readings spread over 48h: only ~12 fall in the trailing 24h."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    for i in range(24):
        ts = start + dt.timedelta(hours=2 * i)  # hours 0,2,...,46
        _insert_at(db_conn, f"r{i}", to_z(ts), 10.0)
    _insert_at(db_conn, "target", "2026-01-03T00:00:00Z", 100.0)

    # 24-hour window: only 12 readings → 12 < 20 → None.
    assert detect_rolling_baseline(db_conn, "m1", "2026-01-03T00:00:00Z") is None
    # 48-hour window: 24 readings → fires.
    anomaly = detect_rolling_baseline(
        db_conn, "m1", "2026-01-03T00:00:00Z", window_hours=48
    )
    assert anomaly is not None
    assert anomaly.observed_consumption == 100.0


def test_insufficient_samples_returns_none(db_conn):
    """Fewer readings than min_samples in the window → abstain."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    for i in range(5):
        ts = start + dt.timedelta(hours=i)
        _insert_at(db_conn, f"r{i}", to_z(ts), 10.0)
    _insert_at(db_conn, "target", "2026-01-02T00:00:00Z", 100.0)

    assert detect_rolling_baseline(db_conn, "m1", "2026-01-02T00:00:00Z") is None
    assert detect_robust_mad(db_conn, "m1", "2026-01-02T00:00:00Z") is None
    # With a lower min_samples the detector will fire.
    anomaly = detect_rolling_baseline(
        db_conn, "m1", "2026-01-02T00:00:00Z", min_samples=5
    )
    assert anomaly is not None


def test_metadata_present_and_structured(db_conn):
    """detection_parameters must expose window, sample_count, threshold."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    for i in range(24):
        ts = start + dt.timedelta(hours=i)
        _insert_at(db_conn, f"r{i}", to_z(ts), 10.0)
    _insert_at(db_conn, "target", "2026-01-02T00:00:00Z", 100.0)

    anomaly = detect_rolling_baseline(db_conn, "m1", "2026-01-02T00:00:00Z")
    assert anomaly is not None
    params = json.loads(anomaly.detection_parameters)
    assert params["window_start"] == "2026-01-01T00:00:00Z"
    assert params["window_end"] == "2026-01-02T00:00:00Z"
    assert params["window_hours"] == 24
    assert params["sample_count"] == 24
    assert params["z_threshold"] == 3.0
    assert "z_score" in params
    assert "quality_flags" in params
    assert isinstance(params["quality_flags"], list)


def test_configurable_threshold(db_conn):
    """z_threshold in the detector argument changes the fire decision."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    for i in range(24):
        ts = start + dt.timedelta(hours=i)
        # Alternating 10/12 → stdev ≈ 1 → z ≈ 3.9 for target 15.
        _insert_at(db_conn, f"r{i}", to_z(ts), 10.0 if i % 2 == 0 else 12.0)
    _insert_at(db_conn, "target", "2026-01-02T00:00:00Z", 15.0)

    target = "2026-01-02T00:00:00Z"
    # Stricter threshold: z ≈ 3.9 < 5.0 → no anomaly.
    assert detect_rolling_baseline(db_conn, "m1", target, z_threshold=5.0) is None
    # Default threshold: z ≈ 3.9 > 3.0 → fires.
    anomaly = detect_rolling_baseline(db_conn, "m1", target)
    assert anomaly is not None


def test_deterministic_across_repeated_calls(db_conn):
    """Two runs on identical data produce identical statistical output."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    for i in range(24):
        ts = start + dt.timedelta(hours=i)
        _insert_at(db_conn, f"r{i}", to_z(ts), 10.0)
    _insert_at(db_conn, "target", "2026-01-02T00:00:00Z", 100.0)

    a1 = detect_rolling_baseline(db_conn, "m1", "2026-01-02T00:00:00Z")
    assert a1 is not None
    p1 = json.loads(a1.detection_parameters)

    # Clear anomalies so the detector runs again on identical inputs.
    db_conn.execute("DELETE FROM anomalies")
    a2 = detect_rolling_baseline(db_conn, "m1", "2026-01-02T00:00:00Z")
    assert a2 is not None
    p2 = json.loads(a2.detection_parameters)

    assert p1 == p2
    assert a1.observed_consumption == a2.observed_consumption
    assert a1.expected_consumption == a2.expected_consumption
    assert a1.severity == a2.severity


def test_default_min_samples_constant():
    """DEFAULT_MIN_SAMPLES must be less than or equal to 23.

    test_missing_readings_quality_impact uses a 23-reading window with one
    deletion; if min_samples were 24 the detector would abstain and the test
    would fail. Locking this invariant prevents accidental tightening.
    """
    assert DEFAULT_MIN_SAMPLES <= 23
