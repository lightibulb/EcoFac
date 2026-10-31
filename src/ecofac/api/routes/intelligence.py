# Copyright 2026 Lightibulb
"""Endpoints for quality assessment and anomaly detection."""
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import QualityResponse
from ecofac.detection.anomaly import detect_robust_mad, detect_rolling_baseline
from ecofac.incidents.lifecycle import create_incident
from ecofac.quality.assessment import assess_quality
from ecofac.timeutil import parse_utc

router = APIRouter()


@router.get("/quality/{meter_id}", tags=["Intelligence"])
def get_quality(
    meter_id: str,
    start: Annotated[str, Query()],
    end: Annotated[str, Query()],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> QualityResponse:
    """Assess data quality for a meter over a period (tz-aware bounds only)."""
    try:
        start_dt = parse_utc(start)
        end_dt = parse_utc(end)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail="Timestamps must be ISO-8601 with timezone info (e.g. 2026-01-01T00:00:00Z)",
        ) from e
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
        flags=report.flags,
    )


@router.post("/analyze/{meter_id}", tags=["Intelligence"])
def analyze_meter(
    meter_id: str,
    timestamp: Annotated[str, Query()],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict:
    """Run anomaly detection for a specific timestamp and create incidents.

    Incidents are only created for anomalies whose data quality window is
    eligible; ineligible anomalies are reported as gated.
    """
    try:
        parse_utc(timestamp)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail="Timestamp must be ISO-8601 with timezone info (e.g. 2026-01-01T00:00:00Z)",
        ) from e

    anomaly_roll = detect_rolling_baseline(conn, meter_id, timestamp)
    anomaly_mad = detect_robust_mad(conn, meter_id, timestamp)
    results = []
    for anomaly in [anomaly_roll, anomaly_mad]:
        if anomaly is None:
            continue
        gated = not anomaly.is_data_quality_eligible
        incident_id = None if gated else create_incident(conn, anomaly)
        results.append({
            "anomaly_id": anomaly.id,
            "incident_id": incident_id,
            "detector": anomaly.detector_name,
            "severity": anomaly.severity,
            "gated_by_quality": gated,
        })
    if not results:
        return {"status": "no_anomalies_detected"}
    return {"status": "anomalies_detected", "findings": results}
