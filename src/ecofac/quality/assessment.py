# Copyright 2026 Lightibulb
"""Data quality assessment engine."""

import dataclasses
import datetime as dt
import sqlite3

from ecofac.timeutil import parse_utc, to_z

DEFAULT_INTERVAL_MINUTES = 60


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
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES


def meter_interval_minutes(conn: sqlite3.Connection, meter_id: str) -> int:
    """Return the configured reporting interval of a meter in minutes.

    Unknown meters fall back to ``DEFAULT_INTERVAL_MINUTES`` so quality
    assessment of an unregistered meter still runs; trusted-decision
    resolution performs its own strict meter validation.
    """
    row = conn.execute("SELECT interval_minutes FROM meters WHERE id = ?", (meter_id,)).fetchone()
    if row is None or row[0] is None:
        return DEFAULT_INTERVAL_MINUTES
    interval = int(row[0])
    if interval <= 0:
        msg = f"Meter {meter_id} has non-positive interval_minutes ({interval})"
        raise ValueError(msg)
    return interval


def assess_quality(
    conn: sqlite3.Connection,
    meter_id: str,
    period_start: dt.datetime,
    period_end: dt.datetime,
    expected_frequency_minutes: int | None = None,
) -> QualityReport:
    """Evaluate data quality for a meter over a specific time period.

    ``expected_frequency_minutes`` defaults to the meter's own configured
    ``interval_minutes`` (not an assumed hourly cadence). Pass an explicit
    value only to override it, e.g. in tests.

    Bounds must be timezone-aware; naive datetimes are rejected loudly
    instead of being silently misinterpreted.
    """
    if expected_frequency_minutes is None:
        expected_frequency_minutes = meter_interval_minutes(conn, meter_id)
    if expected_frequency_minutes <= 0:
        msg = f"expected_frequency_minutes must be > 0 (got {expected_frequency_minutes})"
        raise ValueError(msg)
    start_str = to_z(period_start)
    end_str = to_z(period_end)

    # 1. Fetch actual readings inside the window
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

    # 2. Expected vs missing
    duration_minutes = (period_end - period_start).total_seconds() / 60.0
    expected = int(duration_minutes / expected_frequency_minutes) + 1
    missing = max(0, expected - actual)

    # 3. Duplicates: scoped to THIS meter and THIS period (typed columns,
    #    not substring matching on free-text descriptions).
    cursor = conn.execute(
        "SELECT count(*) AS c FROM data_quality_issues "
        "WHERE issue_type = 'duplicate' AND meter_id = ? "
        "AND reading_timestamp_utc >= ? AND reading_timestamp_utc <= ?",
        (meter_id, start_str, end_str),
    )
    duplicates = cursor.fetchone()["c"]

    # 4. Freshness / staleness
    is_stale = False
    if not latest_obs:
        is_stale = True
    else:
        latest_dt = parse_utc(latest_obs)
        threshold_seconds = expected_frequency_minutes * 60 * 2
        if (period_end - latest_dt).total_seconds() > threshold_seconds:
            is_stale = True

    # 5. Flags
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
        interval_minutes=expected_frequency_minutes,
    )
