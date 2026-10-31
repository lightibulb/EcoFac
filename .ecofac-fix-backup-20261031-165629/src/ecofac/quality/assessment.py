# Copyright 2026 Lightibulb

"""Data quality assessment engine."""

import dataclasses
import datetime as dt
import sqlite3


@dataclasses.dataclass
class QualityReport:
    """Represents the quality assessment of a meter's data over a period."""

    meter_id: str
    period_start: str
    period_end: str
    expected_readings: int
    actual_readings: int
    missing_readings: int
    duplicate_readings: int
    invalid_readings: int
    latest_observation: str | None
    is_stale: bool
    is_analysis_eligible: bool
    flags: list[str]


def assess_quality(
    conn: sqlite3.Connection,
    meter_id: str,
    period_start: dt.datetime,
    period_end: dt.datetime,
    expected_frequency_minutes: int = 60,
) -> QualityReport:
    """Evaluate data quality for a meter over a specific time period."""
    start_str = period_start.isoformat().replace("+00:00", "Z")
    end_str = period_end.isoformat().replace("+00:00", "Z")

    # 1. Fetch Actual Readings
    cursor = conn.execute(
        "SELECT timestamp_utc, is_valid FROM energy_readings "
        "WHERE meter_id = ? AND timestamp_utc >= ? AND timestamp_utc <= ? "
        "ORDER BY timestamp_utc ASC",
        (meter_id, start_str, end_str),
    )
    rows = cursor.fetchall()

    actual = len(rows)
    invalid = sum(1 for r in rows if r["is_valid"] == 0)
    latest_obs = rows[-1]["timestamp_utc"] if rows else None

    # 2. Calculate Expected & Missing
    duration_minutes = (period_end - period_start).total_seconds() / 60.0
    # Add 1 to include both start and end timestamps if exact
    expected = int(duration_minutes / expected_frequency_minutes) + 1
    missing = max(0, expected - actual)

    # 3. Duplicate Detection (from ingestion issues)
    cursor = conn.execute(
        "SELECT count(*) as c FROM data_quality_issues "
        "WHERE issue_type = 'duplicate' AND description LIKE ?",
        (f"%{meter_id}%",),
    )
    duplicates = cursor.fetchone()["c"]

    # 4. Freshness / Staleness
    is_stale = False
    if not latest_obs:
        is_stale = True
    else:
        latest_dt = dt.datetime.fromisoformat(latest_obs)
        # Stale if the latest reading is older than 2x the expected frequency
        threshold_seconds = expected_frequency_minutes * 60 * 2
        if (period_end - latest_dt).total_seconds() > threshold_seconds:
            is_stale = True

    # 5. Evaluate Flags
    flags = []
    if missing > 0:
        flags.append("missing_data")
    if duplicates > 0:
        flags.append("duplicate_data")
    if invalid > 0:
        flags.append("invalid_data")
    if is_stale:
        flags.append("stale_data")

    is_eligible = len(flags) == 0

    return QualityReport(
        meter_id=meter_id,
        period_start=start_str,
        period_end=end_str,
        expected_readings=expected,
        actual_readings=actual,
        missing_readings=missing,
        duplicate_readings=duplicates,
        invalid_readings=invalid,
        latest_observation=latest_obs,
        is_stale=is_stale,
        is_analysis_eligible=is_eligible,
        flags=flags,
    )
