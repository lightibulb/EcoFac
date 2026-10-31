# Copyright 2026 Lightibulb

"""Endpoints for incident management."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import EvidenceResponse, IncidentActionRequest, IncidentResponse
from ecofac.incidents.lifecycle import InvalidStateTransitionError, transition_incident

router = APIRouter()

@router.get("/incidents", tags=["Incidents"])
def list_incidents(
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
    conn: Annotated[sqlite3.Connection, Depends(get_db)] = None,
) -> list[IncidentResponse]:
    """List incidents."""
    cursor = conn.execute("SELECT * FROM incidents LIMIT ? OFFSET ?", (limit, offset))
    return [IncidentResponse(**dict(row)) for row in cursor.fetchall()]

@router.get("/incidents/{incident_id}", tags=["Incidents"])
def get_incident(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> IncidentResponse:
    """Get an incident by ID."""
    cursor = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentResponse(**dict(row))

@router.post("/incidents/{incident_id}/acknowledge", tags=["Incidents"])
def acknowledge_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> IncidentResponse:
    """Transition an incident to ACKNOWLEDGED (requires UNDER_REVIEW)."""
    try:
        transition_incident(conn, incident_id, "ACKNOWLEDGED", reason=payload.reason)
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    cursor = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    return IncidentResponse(**dict(cursor.fetchone()))

@router.post("/incidents/{incident_id}/dismiss", tags=["Incidents"])
def dismiss_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> IncidentResponse:
    """Transition an incident to DISMISSED (requires UNDER_REVIEW)."""
    try:
        transition_incident(conn, incident_id, "DISMISSED", reason=payload.reason)
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    cursor = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    return IncidentResponse(**dict(cursor.fetchone()))

@router.get("/incidents/{incident_id}/evidence", tags=["Incidents"])
def get_evidence(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[EvidenceResponse]:
    """Get evidence records for an incident."""
    cursor = conn.execute(
        "SELECT * FROM evidence_records WHERE incident_id = ?", (incident_id,)
    )
    rows = cursor.fetchall()
    return [EvidenceResponse(**dict(row)) for row in rows]
