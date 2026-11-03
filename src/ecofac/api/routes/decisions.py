# Copyright 2026 Lightibulb
"""Decision Engine API routes."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db, require_write_access
from ecofac.api.schemas import (
    DecisionEvaluateRequest,
    DecisionEvaluateResponse,
    DecisionHistoryItem,
    DecisionRecordResponse,
    DecisionVerifyResponse,
    SupersessionChainResponse,
)
from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
)
from ecofac.decision.store import (
    get_decision,
    list_decisions,
    persist_decision,
    verify_decision,
    walk_supersession_chain,
)
from ecofac.decision.transparent import (
    DecisionMode,
    TransparentDecisionRequest,
    decide_transparently,
)
from ecofac.quality.assessment import assess_quality
from ecofac.timeutil import parse_utc

router = APIRouter(prefix="/decisions", tags=["Decisions"])

TRUSTED_LOAD_UNIT = "kW"


def _resolve_trusted(
    conn: sqlite3.Connection, req: TransparentDecisionRequest
) -> TransparentDecisionRequest:
    """Replace caller-supplied measurements with DB-validated readings."""
    if req.mode != DecisionMode.TRUSTED:
        return req

    updates: dict[str, RichMeasurement] = {}
    flags: set[str] = set()

    for field_name in ("it_load", "cooling_load"):
        m = getattr(req, field_name)
        if not m.meter_id or not m.measurement_timestamp:
            msg = (
                f"{field_name} requires meter_id and measurement_timestamp "
                "in trusted mode"
            )
            raise HTTPException(status_code=400, detail=msg)

        row = conn.execute(
            "SELECT id, value, is_valid FROM energy_readings "
            "WHERE meter_id = ? AND timestamp_utc = ?",
            (m.meter_id, m.measurement_timestamp),
        ).fetchone()
        if row is None:
            msg = (
                f"Reading not found for {field_name} "
                f"(meter={m.meter_id}, ts={m.measurement_timestamp})"
            )
            raise HTTPException(status_code=400, detail=msg)
        if row["is_valid"] == 0:
            msg = f"Reading for {field_name} is marked invalid"
            raise HTTPException(status_code=400, detail=msg)

        updates[field_name] = m.model_copy(
            update={
                "value": float(row["value"]),
                "unit": TRUSTED_LOAD_UNIT,
                "reference_id": row["id"],
                "source": MeasurementSource.MEASURED,
            }
        )

    # Server-side quality assessment on the it_load meter.
    try:
        ts = parse_utc(req.it_load.measurement_timestamp or "")
    except (ValueError, TypeError):
        ts = None
    if ts is not None:
        import datetime as dt  # noqa: PLC0415
        quality = assess_quality(
            conn,
            req.it_load.meter_id or "",
            ts - dt.timedelta(hours=1),
            ts,
        )
        if not quality.is_analysis_eligible:
            flags.update(quality.flags)

    return req.model_copy(
        update={
            **updates,
            "quality_eligible": not flags,
            "quality_flags": sorted(flags),
        }
    )


@router.post("/evaluate")
def evaluate_decision(
    payload: DecisionEvaluateRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
) -> DecisionEvaluateResponse:
    """Evaluate a proposed operational change and persist the decision."""
    try:
        req = _resolve_trusted(conn, payload.decision)
        result = decide_transparently(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        decision_id = persist_decision(
            conn,
            req,
            result,
            supersedes=payload.supersedes,
            supersession_reason=payload.supersession_reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return DecisionEvaluateResponse(decision_id=decision_id, result=result)


@router.get("")
def list_decisions_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[str | None, Query()] = None,
    config_version: Annotated[str | None, Query()] = None,
) -> list[DecisionHistoryItem]:
    """List stored decisions, newest first."""
    rows = list_decisions(
        conn, limit=limit, offset=offset,
        status=status, config_version=config_version,
    )
    return [DecisionHistoryItem(**dict(row)) for row in rows]


@router.get("/{decision_id}")
def get_decision_endpoint(
    decision_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> DecisionRecordResponse:
    """Get the full record for one decision."""
    record = get_decision(conn, decision_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return DecisionRecordResponse(**record)


@router.get("/{decision_id}/verify")
def verify_decision_endpoint(
    decision_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> DecisionVerifyResponse:
    """Recompute fingerprints AND replay the decision to verify integrity."""
    verification = verify_decision(conn, decision_id)
    if verification is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return DecisionVerifyResponse(**verification)


@router.get("/{decision_id}/supersession-chain")
def supersession_chain_endpoint(
    decision_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> SupersessionChainResponse:
    """Return every decision in the supersession chain containing this one."""
    chain = walk_supersession_chain(conn, decision_id)
    if not chain:
        raise HTTPException(status_code=404, detail="Decision not found")
    return SupersessionChainResponse(
        decision_id=decision_id,
        chain=[DecisionHistoryItem(**dict(row)) for row in chain],
    )
