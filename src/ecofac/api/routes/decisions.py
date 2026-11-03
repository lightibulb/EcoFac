# Copyright 2026 Lightibulb
"""Decision Engine API routes."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ecofac.api.dependencies import get_db, require_write_access
from ecofac.api.schemas import (
    DecisionEvaluateRequest,
    DecisionEvaluateResponse,
    DecisionHistoryItem,
    DecisionRecordResponse,
    DecisionStats,
    DecisionVerifyResponse,
    EngineeringSummary,
    SupersessionChainResponse,
)
from ecofac.decision.engineering import build_engineering_summary
from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
)
from ecofac.decision.stats import aggregate_decisions
from ecofac.decision.store import (
    batch_supersession_info,
    count_decisions,
    get_decision,
    list_decisions,
    persist_decision,
    verify_decision,
    walk_supersession_chain_with_branches,
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
    resolved_any = False
    quality_meter: str | None = None
    quality_ts_str: str | None = None

    for field_name in ("it_load", "cooling_load"):
        m = getattr(req, field_name)
        has_meter = m.meter_id is not None
        has_ts = m.measurement_timestamp is not None
        if not has_meter and not has_ts:
            continue

        if not has_meter or not has_ts:
            msg = (
                f"{field_name} in trusted mode requires both meter_id "
                "and measurement_timestamp (or neither)"
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
        resolved_any = True
        if quality_meter is None:
            quality_meter = m.meter_id
            quality_ts_str = m.measurement_timestamp

    if not resolved_any:
        msg = (
            "Trusted mode requires at least one measurement reference "
            "(meter_id + measurement_timestamp)"
        )
        raise HTTPException(status_code=400, detail=msg)

    if quality_meter is not None and quality_ts_str is not None:
        try:
            ts = parse_utc(quality_ts_str)
        except (ValueError, TypeError):
            ts = None
        if ts is not None:
            import datetime as dt  # noqa: PLC0415
            quality = assess_quality(
                conn, quality_meter, ts - dt.timedelta(hours=1), ts
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


def _enrich_history_item(
    row: sqlite3.Row, successors: dict[str, str]
) -> DecisionHistoryItem:
    """Build a DecisionHistoryItem with supersession indicators populated."""
    return DecisionHistoryItem(
        **dict(row),
        is_superseded=row["id"] in successors,
        superseded_by=successors.get(row["id"]),
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
    response: Response,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[str | None, Query()] = None,
    config_version: Annotated[str | None, Query()] = None,
) -> list[DecisionHistoryItem]:
    """List stored decisions, newest first.

    Response header ``X-Total-Count`` carries the total matching the active
    filters (independent of limit/offset), so paginators can compute page
    counts without extra round-trips.
    """
    total = count_decisions(
        conn, status=status, config_version=config_version
    )
    response.headers["X-Total-Count"] = str(total)
    rows = list_decisions(
        conn, limit=limit, offset=offset,
        status=status, config_version=config_version,
    )
    successors = batch_supersession_info(conn, [row["id"] for row in rows])
    return [_enrich_history_item(row, successors) for row in rows]


@router.get("/stats")
def decision_stats_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> DecisionStats:
    """Aggregate decision metrics for the Decision Center overview."""
    stats = aggregate_decisions(conn)
    latest = stats["latest_row"]
    recent = stats["recent_rows"]
    all_ids = (
        ([latest["id"]] if latest is not None else [])
        + [row["id"] for row in recent]
    )
    successors = batch_supersession_info(conn, all_ids)
    return DecisionStats(
        total=stats["total"],
        approved=stats["approved"],
        rejected=stats["rejected"],
        review=stats["review"],
        latest_decision=(
            _enrich_history_item(latest, successors) if latest else None
        ),
        recent_activity=[
            _enrich_history_item(row, successors) for row in recent
        ],
    )


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


@router.get("/{decision_id}/engineering")
def engineering_summary_endpoint(
    decision_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> EngineeringSummary:
    """Structured engineering summary extracted from the stored decision."""
    record = get_decision(conn, decision_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    try:
        data = build_engineering_summary(record)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return EngineeringSummary(**data)


@router.get("/{decision_id}/supersession-chain")
def supersession_chain_endpoint(
    decision_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> SupersessionChainResponse:
    """Return the supersession chain containing this decision."""
    chain, branches = walk_supersession_chain_with_branches(conn, decision_id)
    if not chain:
        raise HTTPException(status_code=404, detail="Decision not found")
    all_ids = [row["id"] for row in chain] + [row["id"] for row in branches]
    successors = batch_supersession_info(conn, all_ids)
    return SupersessionChainResponse(
        decision_id=decision_id,
        chain=[_enrich_history_item(row, successors) for row in chain],
        branches=[_enrich_history_item(row, successors) for row in branches],
    )
