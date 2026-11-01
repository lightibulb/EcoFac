# Copyright 2026 Lightibulb

"""Endpoints for quality assessment and anomaly detection."""

import datetime as dt
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import QualityResponse
from ecofac.detection.anomaly import detect_robust_mad, detect_rolling_baseline
from ecofac.incidents.lifecycle import create_incident, find_event_incident
from ecofac.quality.assessment import assess_quality
from ecofac.timeutil import parse_utc, to_z

router = APIRouter()

BAD_TIMESTAMP = (
    "Invalid ISO8601 date format (timezone required; encode '+' in offsets as %2B)"
)


def _parse_aware(value: str) -> dt.datetime:
    """Parse a timezone-aware ISO 8601 query parameter or raise a 400."""
    try:
        return parse_utc(value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=BAD_TIMESTAMP) from e


def _require_meter(conn: sqlite3.Connection, meter_id: str) -> None:
    if not conn.execute("SELECT 1 FROM meters WHERE id = ?", (meter_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Meter not found")


@router.get("/quality/{meter_id}", tags=["Intelligence"])
def get_quality(
    meter_id: str,
    start: Annotated[str, Query()],
    end: Annotated[str, Query()],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> QualityResponse:
    """Assess data quality for a meter over a period."""
    start_dt = _parse_aware(start)
    end_dt = _parse_aware(end)
    if end_dt < start_dt:
        raise HTTPException(status_code=400, detail="end must not be before start")
    _require_meter(conn, meter_id)

    report = assess_quality(conn, meter_id, start_dt, end_dt)
    return QualityResponse(
        meter_id=report.meter_id,
        period_start=report.period_start,
        period_end=report.period_end,
        expected_readings=report.expected_readings,
        actual_readings=report.actual_readings,
        missing_readings=report.missing_readings,
        duplicate_readings=report.duplicate_readings,
        invalid_readings=report.invalid_readings,
        latest_observation=report.latest_observation,
        is_stale=report.is_stale,
        is_analysis_eligible=report.is_analysis_eligible,
        flags=report.flags
    )


@router.post("/analyze/{meter_id}", tags=["Intelligence"])
def analyze_meter(
    meter_id: str,
    timestamp: Annotated[str, Query()],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict:
    """Run anomaly detection for one timestamp.

    Incidents are opened only when the data-quality window is eligible; gated
    findings are reported but never become incidents. Findings recorded by an
    earlier run (or by import-time analysis) are reported again, flagged
    with ``already_recorded``.
    """
    _require_meter(conn, meter_id)
    # Normalise to the stored form so offsets like +00:00 still match.
    stored_ts = to_z(_parse_aware(timestamp).replace(microsecond=0))

    findings: list[dict] = []
    fresh: set[str] = set()
    for detect in (detect_rolling_baseline, detect_robust_mad):
        anomaly = detect(conn, meter_id, stored_ts)
        if anomaly is None:
            continue
        eligible = anomaly.is_data_quality_eligible
        fresh.add(anomaly.detector_name)
        findings.append({
            "anomaly_id": anomaly.id,
            "incident_id": create_incident(conn, anomaly) if eligible else None,
            "detector": anomaly.detector_name,
            "severity": anomaly.severity,
            "gated_by_quality": not eligible,
            "already_recorded": False,
        })

    recorded = conn.execute(
        "SELECT id, detector_name, severity, is_data_quality_eligible FROM anomalies "
        "WHERE meter_id = ? AND timestamp_utc = ? ORDER BY detector_name",
        (meter_id, stored_ts),
    ).fetchall()
    event_incident = find_event_incident(conn, meter_id, stored_ts)
    findings.extend(
        {
            "anomaly_id": row["id"],
            "incident_id": event_incident if row["is_data_quality_eligible"] else None,
            "detector": row["detector_name"],
            "severity": row["severity"],
            "gated_by_quality": not row["is_data_quality_eligible"],
            "already_recorded": True,
        }
        for row in recorded
        if row["detector_name"] not in fresh
    )

    if not findings:
        return {"status": "no_anomalies_detected"}

    return {"status": "anomalies_detected", "findings": findings}
