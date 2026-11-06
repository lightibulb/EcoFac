# Copyright 2026 Lightibulb

"""Endpoints for quality assessment and anomaly detection."""

import dataclasses
import datetime as dt
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db, require_write_access
from ecofac.api.schemas import QualityResponse
from ecofac.detection.service import analyze_at
from ecofac.quality.assessment import assess_quality
from ecofac.timeutil import parse_utc, to_z

router = APIRouter()

BAD_TIMESTAMP = "Invalid ISO8601 date format (timezone required; encode '+' in offsets as %2B)"


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
        flags=report.flags,
    )


@router.post("/analyze/{meter_id}", tags=["Intelligence"])
def analyze_meter(
    meter_id: str,
    timestamp: Annotated[str, Query()],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
) -> dict:
    """Run anomaly detection for one timestamp.

    Incidents are opened only when the data-quality window is eligible. Findings
    recorded by an earlier run are reported again with ``already_recorded``.
    """
    _require_meter(conn, meter_id)
    stored_ts = to_z(_parse_aware(timestamp).replace(microsecond=0))

    findings = analyze_at(conn, meter_id, stored_ts, create_incidents=True)
    if not findings:
        return {"status": "no_anomalies_detected"}
    return {
        "status": "anomalies_detected",
        "findings": [dataclasses.asdict(f) for f in findings],
    }
