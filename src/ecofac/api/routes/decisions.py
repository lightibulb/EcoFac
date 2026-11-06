# Copyright 2026 Lightibulb
"""Decision Engine API routes."""

import datetime as dt
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
    UncertaintyMetadata,
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
from ecofac.decision.trust import (
    TrustedReading,
    TrustError,
    resolve_trusted_pair,
    server_max_age_seconds,
)
from ecofac.timeutil import to_z

router = APIRouter(prefix="/decisions", tags=["Decisions"])

TRUSTED_LOAD_UNIT = "kW"


def _resolve_trusted(
    conn: sqlite3.Connection, req: TransparentDecisionRequest
) -> TransparentDecisionRequest:
    """Resolve operational inputs server-side; never mix trusted and caller data.

    HYPOTHETICAL requests pass through (only the server-only provenance fields
    are reset so a caller cannot pose as server-resolved).

    TRUSTED requests are rebuilt from the database:

    * BOTH ``it_load`` and ``cooling_load`` need a trusted reference
      (``meter_id`` + ``measurement_timestamp``); a partial reference set is
      rejected instead of silently accepting caller-supplied values.
    * Values, units, source, reference ids and tolerances come from stored
      readings and meter metadata. Energy (kWh per interval) is converted to
      average kW using the meter's own interval; the raw reading is preserved.
    * Quality is assessed for EVERY measurement against its own meter cadence.
    * Quality flags, stale flag, uncertainty flags, freshness policy and the
      evaluation time are server-derived; the caller's values are discarded.
    * PUE must be an explicitly CONFIGURED value; a caller cannot claim
      MEASURED (no trusted PUE source exists) and a missing/assumed PUE is
      carried through so the engine downgrades the decision to REVIEW.
    """
    if req.mode != DecisionMode.TRUSTED:
        if req.inputs_resolved_by_server or req.trust_caveats:
            return req.model_copy(update={"inputs_resolved_by_server": False, "trust_caveats": []})
        return req

    evaluation_dt = dt.datetime.now(dt.UTC)
    try:
        it, cooling = resolve_trusted_pair(
            conn,
            it_ref=(req.it_load.meter_id, req.it_load.measurement_timestamp),
            cooling_ref=(
                req.cooling_load.meter_id,
                req.cooling_load.measurement_timestamp,
            ),
            evaluation_time=evaluation_dt,
        )
    except TrustError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if req.pue is not None and req.pue.source == MeasurementSource.MEASURED:
        raise HTTPException(
            status_code=400,
            detail=(
                "PUE labelled MEASURED cannot be trusted: there is no trusted "
                "PUE reference. Supply an explicitly CONFIGURED PUE, or omit "
                "it to have the decision downgraded to REVIEW."
            ),
        )

    def _measurement(name: str, reading: TrustedReading) -> RichMeasurement:
        uncertainty = (
            UncertaintyMetadata(
                relative_tolerance=reading.relative_tolerance,
                notes="server-side meter accuracy",
            )
            if reading.relative_tolerance is not None
            else None
        )
        return RichMeasurement(
            name=name,
            value=reading.value_kw,
            unit=TRUSTED_LOAD_UNIT,
            source=MeasurementSource.MEASURED,
            uncertainty=uncertainty,
            measurement_timestamp=reading.timestamp_utc,
            reference_id=reading.reading_id,
            meter_id=reading.meter_id,
            raw_value=reading.raw_value,
            raw_unit=reading.raw_unit,
            interval_minutes=reading.interval_minutes,
        )

    flags = sorted({*it.quality_flags, *cooling.quality_flags})
    caveats = sorted({*it.caveats, *cooling.caveats})
    return req.model_copy(
        update={
            "it_load": _measurement("it_load", it),
            "cooling_load": _measurement("cooling_load", cooling),
            "quality_eligible": it.quality_eligible and cooling.quality_eligible,
            "quality_flags": flags,
            "quality_stale": False,
            "uncertainty_flags": [],
            "max_measurement_age_seconds": server_max_age_seconds(),
            "evaluation_time": to_z(evaluation_dt),
            "inputs_resolved_by_server": True,
            "trust_caveats": caveats,
        }
    )


def _enrich_history_item(row: sqlite3.Row, successors: dict[str, str]) -> DecisionHistoryItem:
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
    total = count_decisions(conn, status=status, config_version=config_version)
    response.headers["X-Total-Count"] = str(total)
    rows = list_decisions(
        conn,
        limit=limit,
        offset=offset,
        status=status,
        config_version=config_version,
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
    all_ids = ([latest["id"]] if latest is not None else []) + [row["id"] for row in recent]
    successors = batch_supersession_info(conn, all_ids)
    return DecisionStats(
        total=stats["total"],
        approved=stats["approved"],
        rejected=stats["rejected"],
        review=stats["review"],
        latest_decision=(_enrich_history_item(latest, successors) if latest else None),
        recent_activity=[_enrich_history_item(row, successors) for row in recent],
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
