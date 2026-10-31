# Copyright 2026 Lightibulb

"""Endpoints for quality assessment and anomaly detection."""

import datetime as dt
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import QualityResponse
from ecofac.detection.anomaly import detect_robust_mad, detect_rolling_baseline
from ecofac.incidents.lifecycle import create_incident
from ecofac.quality.assessment import assess_quality

router = APIRouter()

@router.get("/quality/{meter_id}", tags=["Intelligence"])
def get_quality(
    meter_id: str,
    start: Annotated[str, Query()],
    end: Annotated[str, Query()],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> QualityResponse:
    """Assess data quality for a meter over a period."""
    try:
        start_dt = dt.datetime.fromisoformat(start)
        end_dt = dt.datetime.fromisoformat(end)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid ISO8601 date format") from e

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
    """Run anomaly detection for a specific timestamp and create incidents."""
    anomaly_roll = detect_rolling_baseline(conn, meter_id, timestamp)
    anomaly_mad = detect_robust_mad(conn, meter_id, timestamp)

    results = []
    for anomaly in [anomaly_roll, anomaly_mad]:
        if anomaly:
            incident_id = create_incident(conn, anomaly)
            results.append({
                "anomaly_id": anomaly.id,
                "incident_id": incident_id,
                "detector": anomaly.detector_name,
                "severity": anomaly.severity
            })

    if not results:
        return {"status": "no_anomalies_detected"}

    return {"status": "anomalies_detected", "findings": results}
