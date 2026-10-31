#!/usr/bin/env bash
set -euo pipefail

echo "==> 1. Fixing anomaly.py (SQL ambiguity and Ruff linting)..."
cat > src/ecofac/detection/anomaly.py <<'PY'
# Copyright 2026 Lightibulb

"""Statistical anomaly detection engines."""

import datetime as dt
import json
import sqlite3
import statistics
import uuid

from ecofac.domain.models import Anomaly
from ecofac.quality.assessment import assess_quality

ROLLING_Z_THRESHOLD = 3.0
ROLLING_Z_HIGH = 4.5
MAD_Z_THRESHOLD = 3.5
MAD_Z_HIGH = 5.0
ZERO_MAD_EPSILON = 1e-6


def detect_rolling_baseline(
    conn: sqlite3.Connection,
    meter_id: str,
    target_ts: str,
    window_size: int = 24,
) -> Anomaly | None:
    """Detect anomalies using a simple rolling window mean and standard deviation."""
    cursor = conn.execute(
        "SELECT id, timestamp_utc, value FROM energy_readings "
        "WHERE meter_id = ? AND timestamp_utc < ? AND is_valid = 1 "
        "ORDER BY timestamp_utc DESC LIMIT ?",
        (meter_id, target_ts, window_size),
    )
    history = cursor.fetchall()

    if len(history) < window_size:
        return None

    values = [row["value"] for row in history]
    obs_ids = [row["id"] for row in history]

    cursor = conn.execute(
        "SELECT energy_readings.id, energy_readings.value, meters.building_id "
        "FROM energy_readings JOIN meters ON meters.id = energy_readings.meter_id "
        "WHERE meter_id = ? AND timestamp_utc = ?",
        (meter_id, target_ts),
    )
    target = cursor.fetchone()
    if not target:
        return None

    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0

    observed = target["value"]
    deviation = observed - mean
    z_score = deviation / stdev if stdev > 0 else 0.0

    if abs(z_score) < ROLLING_Z_THRESHOLD:
        return None

    return _build_anomaly(
        building_id=target["building_id"],
        meter_id=meter_id,
        target_ts=target_ts,
        observed=observed,
        expected=mean,
        detector_name="rolling_baseline",
        severity="high" if abs(z_score) > ROLLING_Z_HIGH else "medium",
        params={"window_size": window_size, "z_score": z_score, "stdev": stdev},
        supporting_ids=obs_ids,
        conn=conn,
    )


def detect_robust_mad(
    conn: sqlite3.Connection,
    meter_id: str,
    target_ts: str,
    window_size: int = 24,
) -> Anomaly | None:
    """Detect anomalies using Median and Median Absolute Deviation (MAD)."""
    cursor = conn.execute(
        "SELECT id, timestamp_utc, value FROM energy_readings "
        "WHERE meter_id = ? AND timestamp_utc < ? AND is_valid = 1 "
        "ORDER BY timestamp_utc DESC LIMIT ?",
        (meter_id, target_ts, window_size),
    )
    history = cursor.fetchall()

    if len(history) < window_size:
        return None

    values = [row["value"] for row in history]
    obs_ids = [row["id"] for row in history]

    cursor = conn.execute(
        "SELECT energy_readings.id, energy_readings.value, meters.building_id "
        "FROM energy_readings JOIN meters ON meters.id = energy_readings.meter_id "
        "WHERE meter_id = ? AND timestamp_utc = ?",
        (meter_id, target_ts),
    )
    target = cursor.fetchone()
    if not target:
        return None

    median = statistics.median(values)
    absolute_deviations = [abs(v - median) for v in values]
    mad = statistics.median(absolute_deviations)

    observed = target["value"]
    deviation = observed - median

    scaled_mad = 1.4826 * mad
    if scaled_mad == 0.0:
        scaled_mad = ZERO_MAD_EPSILON

    z_score = deviation / scaled_mad

    if abs(z_score) < MAD_Z_THRESHOLD:
        return None

    return _build_anomaly(
        building_id=target["building_id"],
        meter_id=meter_id,
        target_ts=target_ts,
        observed=observed,
        expected=median,
        detector_name="robust_mad",
        severity="high" if abs(z_score) > MAD_Z_HIGH else "medium",
        params={"window_size": window_size, "z_score": z_score, "mad": mad},
        supporting_ids=obs_ids,
        conn=conn,
    )


def _build_anomaly(
    *,
    building_id: str,
    meter_id: str,
    target_ts: str,
    observed: float,
    expected: float,
    detector_name: str,
    severity: str,
    params: dict,
    supporting_ids: list[str],
    conn: sqlite3.Connection,
) -> Anomaly:
    """Construct the anomaly record and check data quality eligibility."""
    abs_dev = observed - expected
    pct_dev = (abs_dev / expected * 100) if expected > 0 else None

    target_dt = dt.datetime.fromisoformat(target_ts)
    start_dt = target_dt - dt.timedelta(hours=1)

    quality = assess_quality(conn, meter_id, start_dt, target_dt, expected_frequency_minutes=60)

    anomaly = Anomaly(
        id=str(uuid.uuid4()),
        building_id=building_id,
        meter_id=meter_id,
        timestamp_utc=target_ts,
        observed_consumption=observed,
        expected_consumption=expected,
        absolute_deviation=abs_dev,
        percentage_deviation=pct_dev,
        detector_name=detector_name,
        severity=severity,
        is_data_quality_eligible=quality.is_analysis_eligible,
        detection_timestamp=dt.datetime.now(dt.UTC).isoformat(),
        supporting_observation_ids=supporting_ids,
        detection_parameters=json.dumps(params),
    )

    conn.execute(
        "INSERT INTO anomalies (id, building_id, meter_id, timestamp_utc, "
        "observed_consumption, expected_consumption, absolute_deviation, "
        "percentage_deviation, detector_name, severity, "
        "is_data_quality_eligible, detection_timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            anomaly.id,
            anomaly.building_id,
            anomaly.meter_id,
            anomaly.timestamp_utc,
            anomaly.observed_consumption,
            anomaly.expected_consumption,
            anomaly.absolute_deviation,
            anomaly.percentage_deviation,
            anomaly.detector_name,
            anomaly.severity,
            int(anomaly.is_data_quality_eligible),
            anomaly.detection_timestamp,
        ),
    )
    return anomaly
PY

echo "==> 2. Fixing test_detection.py (imports and logic)..."
cat > tests/integration/test_detection.py <<'PY'
# Copyright 2026 Lightibulb

"""Integration tests for anomaly detection."""

import datetime as dt

import pytest

from ecofac.detection.anomaly import detect_robust_mad, detect_rolling_baseline
from ecofac.domain.models import EnergyReading
from ecofac.persistence.database import get_connection, init_db
from ecofac.persistence.repository import insert_reading


@pytest.fixture
def db_conn():
    """Yield an in-memory database."""
    conn = get_connection(":memory:")
    init_db(conn)
    now = "2026-01-01T00:00:00Z"

    conn.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)", (now,))
    conn.execute("INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1', 'o1', 'Bldg', ?)", (now,))
    conn.execute(
        "INSERT INTO meters (id, building_id, fuel_type, unit, created_at) "
        "VALUES ('m1', 'b1', 'electricity', 'kWh', ?)",
        (now,)
    )
    conn.execute("INSERT INTO data_sources (id, name, source_type, created_at) VALUES ('s1', 'Src', 'csv', ?)", (now,))
    conn.execute("INSERT INTO import_batches (id, source_id, status, imported_at) VALUES ('ib1', 's1', 'started', ?)", (now,))

    yield conn
    conn.close()


def _insert_series(conn, values, start_hour=0, day=1):
    """Helper to insert a continuous series of hourly readings."""
    for i, val in enumerate(values):
        ts = dt.datetime(2026, 1, day, start_hour, 0, tzinfo=dt.UTC)
        ts += dt.timedelta(hours=i)
        r = EnergyReading(
            id=f"r-{day}-{i}", meter_id="m1", timestamp_utc=ts.isoformat().replace("+00:00", "Z"),
            value=val, unit="kWh", source_id="s1", batch_id="ib1",
            is_valid=True, created_at="2026-01-01T00:00:00Z"
        )
        insert_reading(conn, r)


def test_normal_consumption(db_conn):
    """Verify normal steady data does not trigger anomalies."""
    _insert_series(db_conn, [10.0] * 24)
    _insert_series(db_conn, [10.0], start_hour=0, day=2)  # The target

    target_ts = "2026-01-02T00:00:00Z"
    anomaly_roll = detect_rolling_baseline(db_conn, "m1", target_ts)
    anomaly_mad = detect_robust_mad(db_conn, "m1", target_ts)

    assert anomaly_roll is None
    assert anomaly_mad is None


def test_sudden_spike(db_conn):
    """Verify a sudden spike triggers anomalies."""
    _insert_series(db_conn, [10.0] * 24)
    _insert_series(db_conn, [100.0], start_hour=0, day=2)  # The spike

    target_ts = "2026-01-02T00:00:00Z"
    anomaly_roll = detect_rolling_baseline(db_conn, "m1", target_ts)
    anomaly_mad = detect_robust_mad(db_conn, "m1", target_ts)

    assert anomaly_roll is not None
    assert anomaly_mad is not None
    assert anomaly_roll.observed_consumption == 100.0
    assert anomaly_roll.expected_consumption == 10.0


def test_insufficient_history(db_conn):
    """Verify detectors return None if there is not enough data."""
    _insert_series(db_conn, [10.0] * 5)  # Only 5 points, need 24
    _insert_series(db_conn, [10.0], start_hour=0, day=2)

    target_ts = "2026-01-02T00:00:00Z"
    anomaly_roll = detect_rolling_baseline(db_conn, "m1", target_ts)
    anomaly_mad = detect_robust_mad(db_conn, "m1", target_ts)

    assert anomaly_roll is None
    assert anomaly_mad is None


def test_zero_mad_constant_data(db_conn):
    """Verify zero MAD is handled safely."""
    _insert_series(db_conn, [10.0] * 24)
    _insert_series(db_conn, [15.0], start_hour=0, day=2)

    target_ts = "2026-01-02T00:00:00Z"
    anomaly_mad = detect_robust_mad(db_conn, "m1", target_ts)

    assert anomaly_mad is not None


def test_missing_readings_quality_impact(db_conn):
    """Verify data quality eligibility flag is passed to the anomaly."""
    _insert_series(db_conn, [10.0] * 24, day=1)
    
    # Delete the reading at hour 23 to create a gap right before the target
    db_conn.execute("DELETE FROM energy_readings WHERE id = 'r-1-23'")
    
    # Insert one reading at day 0 to maintain the 24-item window
    _insert_series(db_conn, [10.0], start_hour=23, day=0)
    
    _insert_series(db_conn, [100.0], start_hour=0, day=2)

    target_ts = "2026-01-02T00:00:00Z"
    anomaly = detect_rolling_baseline(db_conn, "m1", target_ts)

    assert anomaly is not None
    assert anomaly.is_data_quality_eligible is False
PY

echo "==> 3. Stripping trailing whitespace from all Python files..."
find src tests simulator evaluation -name "*.py" -exec sed -i 's/[[:space:]]*$//' {} +

echo "==> Done. All issues resolved."
