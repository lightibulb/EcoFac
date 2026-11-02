# Copyright 2026 Lightibulb
"""Statistical anomaly detection engines."""
import datetime as dt
import json
import sqlite3
import statistics
import uuid

from ecofac.domain.models import Anomaly
from ecofac.quality.assessment import assess_quality
from ecofac.timeutil import now_z, parse_utc, to_z

# Default detector configuration
DEFAULT_WINDOW_HOURS = 24
DEFAULT_MIN_SAMPLES = 20

# Rolling baseline thresholds
ROLLING_Z_THRESHOLD = 3.0
ROLLING_Z_HIGH = 4.5

# Robust MAD thresholds
MAD_Z_THRESHOLD = 3.5
MAD_Z_HIGH = 5.0
ZERO_MAD_EPSILON = 1e-6

# Comparable-period confirmation
SLOT_LOOKBACK_DAYS = 28
SLOT_SAMPLES = 5
SLOT_MIN_SAMPLES = 3
SLOT_Z_THRESHOLD = 3.5
SLOT_REL_FLOOR = 0.05
MAD_SCALE = 1.4826


def _comparable_period(
    conn: sqlite3.Connection, meter_id: str, target_ts: str, observed: float
) -> tuple[bool, dict | None]:
    """Check whether the reading matches its own daily routine.

    Returns (True, stats) if the reading is explained by routine, (False, None)
    if fewer than SLOT_MIN_SAMPLES comparable readings exist (fall back to the
    trailing-window verdict).
    """
    target = parse_utc(target_ts)
    weekend = target.weekday() >= 5
    stamps = []
    for days_back in range(1, SLOT_LOOKBACK_DAYS + 1):
        day = target - dt.timedelta(days=days_back)
        if (day.weekday() >= 5) == weekend:
            stamps.append(to_z(day))
    placeholders = ",".join("?" * len(stamps))
    rows = conn.execute(
        "SELECT value FROM energy_readings WHERE meter_id = ? AND is_valid = 1 "
        f"AND timestamp_utc IN ({placeholders}) ORDER BY timestamp_utc DESC LIMIT ?",
        (meter_id, *stamps, SLOT_SAMPLES),
    ).fetchall()
    if len(rows) < SLOT_MIN_SAMPLES:
        return False, None
    values = [row["value"] for row in rows]
    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values])
    spread = max(MAD_SCALE * mad, SLOT_REL_FLOOR * abs(median), ZERO_MAD_EPSILON)
    z_score = (observed - median) / spread
    stats = {"samples": len(values), "median": median, "z_score": z_score}
    return abs(z_score) < SLOT_Z_THRESHOLD, stats


def detect_rolling_baseline(
    conn: sqlite3.Connection,
    meter_id: str,
    target_ts: str,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    z_threshold: float = ROLLING_Z_THRESHOLD,
    z_high: float = ROLLING_Z_HIGH,
) -> Anomaly | None:
    """Detect anomalies using a time-windowed rolling mean and stdev.

    The window is [target_ts - window_hours, target_ts). Only valid readings
    in that interval contribute to the baseline. If fewer than ``min_samples``
    readings exist, the detector abstains (returns None).
    """
    target_dt = parse_utc(target_ts)
    window_start = to_z(target_dt - dt.timedelta(hours=window_hours))

    cursor = conn.execute(
        "SELECT id, timestamp_utc, value FROM energy_readings "
        "WHERE meter_id = ? AND timestamp_utc < ? AND timestamp_utc >= ? "
        "AND is_valid = 1 ORDER BY timestamp_utc DESC",
        (meter_id, target_ts, window_start),
    )
    history = cursor.fetchall()
    if len(history) < min_samples:
        return None
    values = [row["value"] for row in history]
    obs_ids = [row["id"] for row in history]

    cursor = conn.execute(
        "SELECT energy_readings.id, energy_readings.value, meters.building_id "
        "FROM energy_readings JOIN meters ON meters.id = energy_readings.meter_id "
        "WHERE energy_readings.meter_id = ? AND energy_readings.timestamp_utc = ? "
        "AND energy_readings.is_valid = 1",
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
    if abs(z_score) < z_threshold:
        return None
    explained, slot = _comparable_period(conn, meter_id, target_ts, observed)
    if explained:
        return None

    return _build_anomaly(
        building_id=target["building_id"],
        meter_id=meter_id,
        target_ts=target_ts,
        observed=observed,
        expected=mean,
        detector_name="rolling_baseline",
        severity="high" if abs(z_score) > z_high else "medium",
        params={
            "window_hours": window_hours,
            "window_start": window_start,
            "window_end": target_ts,
            "sample_count": len(history),
            "z_threshold": z_threshold,
            "z_score": z_score,
            "mean": mean,
            "stdev": stdev,
            "comparable_period": slot,
        },
        supporting_ids=obs_ids,
        conn=conn,
    )


def detect_robust_mad(
    conn: sqlite3.Connection,
    meter_id: str,
    target_ts: str,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    z_threshold: float = MAD_Z_THRESHOLD,
    z_high: float = MAD_Z_HIGH,
) -> Anomaly | None:
    """Detect anomalies using a time-windowed Median and MAD."""
    target_dt = parse_utc(target_ts)
    window_start = to_z(target_dt - dt.timedelta(hours=window_hours))

    cursor = conn.execute(
        "SELECT id, timestamp_utc, value FROM energy_readings "
        "WHERE meter_id = ? AND timestamp_utc < ? AND timestamp_utc >= ? "
        "AND is_valid = 1 ORDER BY timestamp_utc DESC",
        (meter_id, target_ts, window_start),
    )
    history = cursor.fetchall()
    if len(history) < min_samples:
        return None
    values = [row["value"] for row in history]
    obs_ids = [row["id"] for row in history]

    cursor = conn.execute(
        "SELECT energy_readings.id, energy_readings.value, meters.building_id "
        "FROM energy_readings JOIN meters ON meters.id = energy_readings.meter_id "
        "WHERE energy_readings.meter_id = ? AND energy_readings.timestamp_utc = ? "
        "AND energy_readings.is_valid = 1",
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
    scaled_mad = MAD_SCALE * mad
    if scaled_mad == 0.0:
        scaled_mad = ZERO_MAD_EPSILON
    z_score = deviation / scaled_mad
    if abs(z_score) < z_threshold:
        return None
    explained, slot = _comparable_period(conn, meter_id, target_ts, observed)
    if explained:
        return None

    return _build_anomaly(
        building_id=target["building_id"],
        meter_id=meter_id,
        target_ts=target_ts,
        observed=observed,
        expected=median,
        detector_name="robust_mad",
        severity="high" if abs(z_score) > z_high else "medium",
        params={
            "window_hours": window_hours,
            "window_start": window_start,
            "window_end": target_ts,
            "sample_count": len(history),
            "z_threshold": z_threshold,
            "z_score": z_score,
            "median": median,
            "mad": mad,
            "comparable_period": slot,
        },
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
) -> Anomaly | None:
    """Construct the anomaly record and check data quality eligibility.

    Returns None if this (meter, timestamp, detector) was already flagged,
    so re-analysis cannot spawn duplicate anomalies or incidents.
    """
    existing = conn.execute(
        "SELECT 1 FROM anomalies "
        "WHERE meter_id = ? AND timestamp_utc = ? AND detector_name = ?",
        (meter_id, target_ts, detector_name),
    ).fetchone()
    if existing:
        return None

    abs_dev = observed - expected
    pct_dev = (abs_dev / expected * 100) if expected > 0 else None
    target_dt = parse_utc(target_ts)
    start_dt = target_dt - dt.timedelta(hours=1)
    quality = assess_quality(
        conn, meter_id, start_dt, target_dt, expected_frequency_minutes=60
    )

    # Enrich params with data-quality metadata (explainability)
    enriched = {**params, "quality_flags": quality.flags}

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
        detection_timestamp=now_z(),
        supporting_observation_ids=supporting_ids,
        detection_parameters=json.dumps(enriched),
    )
    conn.execute(
        "INSERT INTO anomalies (id, building_id, meter_id, timestamp_utc, "
        "observed_consumption, expected_consumption, absolute_deviation, "
        "percentage_deviation, detector_name, severity, "
        "is_data_quality_eligible, detection_timestamp, detection_parameters) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            anomaly.detection_parameters,
        ),
    )
    return anomaly
