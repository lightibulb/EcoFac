# Copyright 2026 Lightibulb

"""Endpoints for incident management."""

import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import EvidenceResponse, IncidentActionRequest, IncidentResponse
from ecofac.incidents.lifecycle import InvalidStateTransitionError, transition_incident

router = APIRouter()

@router.get("/incidents", response_model=list[IncidentResponse], tags=["Incidents"])
def list_incidents(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[IncidentResponse]:
    """List incidents."""
    cursor = conn.execute("SELECT * FROM incidents LIMIT ? OFFSET ?", (limit, offset))
    return [IncidentResponse(**dict(row)) for row in cursor.fetchall()]

@router.get("/incidents/{id}", response_model=IncidentResponse, tags=["Incidents"])
def get_incident(id: str, conn: sqlite3.Connection = Depends(get_db)) -> IncidentResponse:
    """Get an incident by ID."""
    cursor = conn.execute("SELECT * FROM incidents WHERE id = ?", (id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentResponse(**dict(row))

@router.post("/incidents/{id}/acknowledge", response_model=IncidentResponse, tags=["Incidents"])
def acknowledge_incident(
    id: str,
    payload: IncidentActionRequest,
    conn: sqlite3.Connection = Depends(get_db)
) -> IncidentResponse:
    """Transition an incident to ACKNOWLEDGED (requires UNDER_REVIEW)."""
    try:
        transition_incident(conn, id, "ACKNOWLEDGED", reason=payload.reason)
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    cursor = conn.execute("SELECT * FROM incidents WHERE id = ?", (id,))
    return IncidentResponse(**dict(cursor.fetchone()))

@router.post("/incidents/{id}/dismiss", response_model=IncidentResponse, tags=["Incidents"])
def dismiss_incident(
    id: str,
    payload: IncidentActionRequest,
    conn: sqlite3.Connection = Depends(get_db)
) -> IncidentResponse:
    """Transition an incident to DISMISSED (requires UNDER_REVIEW)."""
    try:
        transition_incident(conn, id, "DISMISSED", reason=payload.reason)
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    cursor = conn.execute("SELECT * FROM incidents WHERE id = ?", (id,))
    return IncidentResponse(**dict(cursor.fetchone()))

@router.get("/incidents/{id}/evidence", response_model=list[EvidenceResponse], tags=["Incidents"])
def get_evidence(id: str, conn: sqlite3.Connection = Depends(get_db)) -> list[EvidenceResponse]:
    """Get evidence records for an incident."""
    cursor = conn.execute("SELECT * FROM evidence_records WHERE incident_id = ?", (id,))
    rows = cursor.fetchall()
    return [EvidenceResponse(**dict(row)) for row in rows]
