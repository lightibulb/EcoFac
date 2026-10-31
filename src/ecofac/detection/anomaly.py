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
    if stdev == 0.0:
        stdev = ZERO_MAD_EPSILON
    z_score = deviation / stdev

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
