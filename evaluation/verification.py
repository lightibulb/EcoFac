# Copyright 2026 Lightibulb

"""Before/after intervention verification engine."""

import dataclasses
import datetime as dt
import sqlite3
import statistics


@dataclasses.dataclass
class VerificationResult:
    """Result of a before/after energy verification."""
    status: str
    baseline_mean: float
    post_mean: float
    percent_change: float
    data_completeness_pct: float
    caveats: list[str]


def verify_intervention(
    conn: sqlite3.Connection,
    meter_id: str,
    intervention_date: dt.datetime,
    baseline_days: int = 30,
    post_days: int = 14,
    *,
    threshold_pct: float = 5.0,
) -> VerificationResult:
    """Compare energy consumption before and after an intervention."""

    # 1. Fetch Baseline (pre-intervention)
    b_start = (intervention_date - dt.timedelta(days=baseline_days)).isoformat()
    b_end = intervention_date.isoformat()

    cursor = conn.execute(
        "SELECT value FROM energy_readings WHERE meter_id = ? "
        "AND timestamp_utc >= ? AND timestamp_utc < ? AND is_valid = 1",
        (meter_id, b_start, b_end)
    )
    baseline_vals = [row["value"] for row in cursor.fetchall()]

    # 2. Fetch Post-intervention
    p_start = (intervention_date + dt.timedelta(days=1)).isoformat()
    p_end = (intervention_date + dt.timedelta(days=post_days)).isoformat()

    cursor = conn.execute(
        "SELECT value FROM energy_readings WHERE meter_id = ? "
        "AND timestamp_utc >= ? AND timestamp_utc <= ? AND is_valid = 1",
        (meter_id, p_start, p_end)
    )
    post_vals = [row["value"] for row in cursor.fetchall()]

    # 3. Evaluate outcomes
    expected_b = baseline_days * 24
    expected_p = post_days * 24
    completeness = (len(baseline_vals) + len(post_vals)) / (expected_b + expected_p) * 100

    caveats = [
        "Synthetic data only.",
        "Weather and occupancy variations are not controlled for.",
        "Not verified against real utility bills."
    ]

    if not baseline_vals or not post_vals or completeness < 80.0:
        return VerificationResult(
            status="INCONCLUSIVE",
            baseline_mean=0.0,
            post_mean=0.0,
            percent_change=0.0,
            data_completeness_pct=round(completeness, 1),
            caveats=caveats
        )

    b_mean = statistics.mean(baseline_vals)
    p_mean = statistics.mean(post_vals)
    pct_change = ((p_mean - b_mean) / b_mean) * 100.0 if b_mean > 0 else 0.0

    # Check for recurrence (a new spike in the post period)
    b_stdev = statistics.stdev(baseline_vals) if len(baseline_vals) > 1 else 0.0
    spike_threshold = b_mean + max(3 * b_stdev, 0.2 * b_mean)
    has_recurrence = any(v > spike_threshold for v in post_vals)

    if has_recurrence:
        status = "RECURRENCE"
    elif pct_change <= -threshold_pct:
        status = "SUCCESSFUL"
    elif pct_change < 0:
        status = "PARTIAL_IMPROVEMENT"
    elif pct_change >= threshold_pct:
        status = "FAILED"
    else:
        status = "INCONCLUSIVE"

    return VerificationResult(
        status=status,
        baseline_mean=round(b_mean, 2),
        post_mean=round(p_mean, 2),
        percent_change=round(pct_change, 2),
        data_completeness_pct=round(completeness, 1),
        caveats=caveats
    )
