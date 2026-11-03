# Copyright 2026 Lightibulb
"""Decision Engine API routes."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import (
    DecisionEvaluateRequest,
    DecisionEvaluateResponse,
    DecisionHistoryItem,
    DecisionRecordResponse,
    DecisionVerifyResponse,
    SupersessionChainResponse,
)
from ecofac.decision.store import (
    get_decision,
    list_decisions,
    persist_decision,
    verify_decision,
    walk_supersession_chain,
)
from ecofac.decision.transparent import decide_transparently

router = APIRouter(prefix="/decisions", tags=["Decisions"])


@router.post("/evaluate")
def evaluate_decision(
    payload: DecisionEvaluateRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> DecisionEvaluateResponse:
    """Evaluate a proposed operational change and persist the decision."""
    result = decide_transparently(payload.decision)
    try:
        decision_id = persist_decision(
            conn,
            payload.decision,
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
        conn,
        limit=limit,
        offset=offset,
        status=status,
        config_version=config_version,
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
    """Recompute fingerprints and confirm the stored decision is intact."""
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
